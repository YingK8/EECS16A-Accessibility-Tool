"""The conversion engine: turn a :class:`~latexa11y.run.RunConfig` into PDFs.

This is the code that used to be ``examples/build-corpus.sh``. Moving it into
Python is not tidying: the shell version was the *definition* of what conversion
means -- which lines get injected, in what order, with what options -- expressed
as a ``sed`` expression that nothing tested and nothing else could call.

Four responsibilities, in order:

``preamble_for``    decide what to inject, given the toggles and the toolchain
``materialise``     put a converted copy of the source somewhere buildable
``compile_document``run the engine enough times for the tags to resolve
``inspect``         report errors, warnings, pages, bookmarks and pixel drift

The pixel comparison is the honest part. Tagging repaginates slightly, and a
tool that claims "identical" without measuring is a tool nobody should believe.
Every build reports the fraction of strongly-differing pixels against the
untouched original, so the claim is checkable per assignment rather than taken
on faith.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Profile
from ..errors import LatexA11yError, ToolchainError
from ..run import Assignment, RunConfig
from ..texlex import EditBuffer, TexSource
from ..toolchain import TaggingMode, probe

__all__ = [
    "BuildReport",
    "preamble_for",
    "materialise",
    "compile_document",
    "inspect_pdf",
    "compare_pdfs",
    "build_assignment",
    "build_run",
    "tex_search_path",
]

#: Where this package's .sty/.cls files live. Present for an editable install
#: (the normal case); when absent the files are assumed to be on the TeX tree
#: already, and kpsewhich finds them without help.
PACKAGE_TEX_DIR = Path(__file__).resolve().parents[3] / "tex"


def _package_tex_dirs() -> list[Path]:
    return [PACKAGE_TEX_DIR] if PACKAGE_TEX_DIR.is_dir() else []

#: Pixel comparison settings, proven during the fidelity work: greyscale at
#: 110 dpi, counting only pixels that differ by more than 96/255. A lower
#: threshold reports antialiasing noise as a difference and drowns the signal.
_DIFF_DPI = 110
_DIFF_THRESHOLD = 96


# ---------------------------------------------------------------------- #
# what conversion injects
# ---------------------------------------------------------------------- #


def preamble_for(
    config: RunConfig, profile: Profile, mode: TaggingMode | None = None
) -> list[str]:
    """The lines conversion prepends to a driver, given the run's toggles.

    Split in two by the caller: ``\\DocumentMetadata`` must be the very first
    line of the file (the kernel enforces this), everything else belongs in the
    preamble. See :func:`split_preamble`.

    Refuses outright when the toolchain cannot tag. That is the whole point of
    the check: on this toolchain an unsupported ``testphase`` module is a
    *silent* no-op, so a build with a missing module produces a clean log, a
    plausible PDF, and no tags at all.
    """
    if mode is None:
        mode = probe(profile).tagging_mode

    lines: list[str] = []
    engine = profile.engine

    if config.standards.tagging:
        if mode is TaggingMode.UNAVAILABLE:
            raise ToolchainError(
                "this toolchain cannot produce a tagged PDF",
                hint=(
                    "run `latexa11y doctor` for the specific missing capability; "
                    "building anyway would emit an untagged PDF with no error"
                ),
            )
        keys = [f"lang={profile.course.language}", f"pdfversion={engine.pdf_version}"]
        if mode is TaggingMode.MODERN:
            # The supported switch. Only this mode can *declare* conformance.
            keys.append("tagging=on")
            if engine.pdf_standard:
                keys.append(f"pdfstandard={engine.pdf_standard}")
        else:
            keys.append("testphase={" + ",".join(engine.legacy_testphase) + "}")
        lines.append("\\DocumentMetadata{" + ",".join(keys) + "}")

    # Which of our packages is loaded governs which of our macros may be used.
    # Emitting \accesssetup with neither loaded is an undefined control sequence
    # -- and one that a naive log scan reports as a clean build, because pdflatex
    # carries on in nonstop mode and still writes a PDF.
    wants_core = (
        config.standards.bookmarks
        or config.standards.question_tags
        or config.colors.mode == "conforming"
        or not config.alt.strict
    )
    if config.standards.retrofit:
        lines.append("\\usepackage{latexa11y-ee16}")
        loaded = True
    elif wants_core:
        # The primitives without the course-specific patching. Asking for
        # bookmarks with the retrofit off would otherwise silently do nothing.
        lines.append("\\usepackage{latexa11y-core}")
        loaded = True
    else:
        loaded = False

    if config.standards.question_tags and not config.standards.retrofit:
        # \accessquestiontags is defined by the retrofit, which is what knows
        # what a "question" is in this course. latexa11y-core has no such notion,
        # so the combination is undefined -- say so rather than emit a control
        # sequence that does not exist.
        raise LatexA11yError(
            "question_tags needs the course macro retrofit, which is switched off",
            hint=(
                "turn 'Course macro retrofit' back on, or turn question H2 tags "
                "off; only the retrofit knows which macro is a question title"
            ),
        )

    if loaded:
        if config.standards.question_tags:
            lines.append("\\accessquestiontags")
        if config.colors.mode == "conforming":
            lines.append("\\accesssetup{conforming-colors}")
        if not config.alt.strict:
            lines.append("\\accesssetup{strict=false}")

    if config.standards.unicode_map:
        # Guarded: \pdfgentounicode is a pdfTeX primitive and does not exist
        # under lualatex or xelatex, where the map is produced anyway.
        lines.append("\\ifdefined\\pdfgentounicode\\pdfgentounicode=1\\fi")

    return lines


def split_preamble(lines: list[str]) -> tuple[list[str], list[str]]:
    """Separate the lines that must lead the file from the rest.

    ``\\DocumentMetadata`` is only honoured as the first line of the document;
    anywhere else the kernel ignores it and the build is silently untagged.
    """
    first = [line for line in lines if line.startswith("\\DocumentMetadata")]
    rest = [line for line in lines if not line.startswith("\\DocumentMetadata")]
    return first, rest


# ---------------------------------------------------------------------- #
# where the package lines go
# ---------------------------------------------------------------------- #


def _preamble_insertion_point(source: TexSource) -> int:
    """Byte offset in a driver at which ``\\usepackage`` lines may be inserted.

    Two shapes exist in the wild and both must work:

    * the driver carries ``\\begin{document}`` itself -- insert just before it;
    * the driver is preamble only and ends with ``\\input{body}``, which pulls in
      the file that opens the document -- insert before that final input.

    The old shell version matched ``^\\input{body...}$`` by name. That works for
    this corpus by luck: a driver that spells its body file anything else, or
    that inputs a shared preamble *after* the body line, silently gets no
    package and builds untagged. Anchoring on structure rather than on a
    filename removes the guess.
    """
    begin = source.search(r"\\begin\s*\{document\}")
    if begin is not None:
        return begin.start()

    last: int | None = None
    for match in source.finditer(r"\\(?:input|include)\s*\{[^}]*\}"):
        last = match.start()
    if last is not None:
        return last

    raise LatexA11yError(
        "cannot find where to insert packages: the driver has no "
        "\\begin{document} and no \\input",
        hint="pass the file that actually starts the document",
    )


def inject(source: TexSource, lines: list[str]) -> str:
    """Return the driver text with the conversion lines added.

    Recorded through :class:`EditBuffer` as pure insertions, so this can never
    alter a byte of the original -- the same guarantee the description writer
    gives, and for the same reason: these files are course material.
    """
    lead, packages = split_preamble(lines)
    buffer = EditBuffer(source.path)
    if lead:
        buffer.insert(0, "\n".join(lead) + "\n", reason="tagging metadata", rule="BUILD-METADATA")
    if packages:
        point = _preamble_insertion_point(source)
        buffer.insert(
            point,
            "\n".join(packages) + "\n",
            reason="accessibility packages",
            rule="BUILD-PACKAGES",
        )
    return buffer.apply(source.text)


# ---------------------------------------------------------------------- #
# materialising a buildable copy
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class Prepared:
    """A driver ready to compile, and the directory to compile it from."""

    assignment: Assignment
    #: The file handed to pdflatex.
    driver: Path
    #: Working directory, so relative \input and \includegraphics resolve.
    work_dir: Path
    #: Directories added to TEXINPUTS, in order.
    search_path: list[Path] = field(default_factory=list)
    #: Text actually written, for dry-run display.
    text: str = ""
    injected: list[str] = field(default_factory=list)


def tex_search_path(*directories: Path) -> str:
    """A TEXINPUTS value. The trailing empty element keeps the system tree."""
    parts = [str(Path(directory).resolve()) for directory in directories]
    return os.pathsep.join(parts) + os.pathsep


def materialise(
    assignment: Assignment,
    config: RunConfig,
    profile: Profile,
    *,
    lines: list[str] | None = None,
    write: bool | None = None,
) -> Prepared:
    """Produce a buildable, converted copy of one assignment.

    In ``mirror`` mode the corpus is never touched: the assignment's own ``.tex``
    files are copied into the output tree and the driver is converted there. The
    original directory is still added to TEXINPUTS, so shared assets that live
    outside the assignment -- ``ee16.sty`` three levels up, ``figures/``,
    ``timestamp.sty`` -- resolve back to the corpus without being copied.
    """
    if assignment.driver is None:
        raise LatexA11yError(
            f"{assignment.path} has no driver file to build",
            hint="a driver is the .tex containing \\begin{document}",
        )
    write = config.write if write is None else write
    root = profile.corpus.root.resolve()
    source_dir = (root / assignment.path).resolve()
    lines = preamble_for(config, profile) if lines is None else lines

    source = TexSource.from_path(source_dir / assignment.driver)
    converted = inject(source, lines)

    if config.output.in_place:
        driver = source_dir / assignment.driver
        if write:
            require_clean_worktree(root)
            driver.write_bytes(source.encode(converted))
        return Prepared(
            assignment, driver, source_dir, _package_tex_dirs(), converted, lines
        )

    mirror_root = config.output.tex_dir().resolve()
    target_dir = (mirror_root / assignment.path).resolve()
    driver = target_dir / assignment.driver
    if write:
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in (".tex", ".sty", ".cls"):
                if path.name != assignment.driver:
                    shutil.copy2(path, target_dir / path.name)
        driver.write_bytes(source.encode(converted))
        # Everything the driver reaches by an explicit relative path, at the
        # same offsets, so the mirror builds without the corpus beside it.
        mirror_dependencies(
            source_dir / assignment.driver, source_dir, target_dir, mirror_root
        )
    return Prepared(
        assignment,
        driver,
        target_dir,
        # Order is load-bearing. The mirror must come FIRST: kpathsea searches
        # TEXINPUTS entries before the default (which is where "." lives), so
        # listing the corpus ahead of the mirror makes `\input{body}` find the
        # ORIGINAL body.tex and silently discard every edit made in the mirror.
        # The corpus stays on the path last, as a fallback for assets the
        # dependency walk did not recognise.
        [target_dir, *_package_tex_dirs(), source_dir],
        converted,
        lines,
    )


#: Commands whose argument names a file, and the extensions to try for each.
#: `\includegraphics` is listed with an empty extension first because the corpus
#: writes both `{figures/x.png}` and the extension-less `{figures/x}`.
_FILE_REFERENCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", (".sty",)),
    (r"\\RequirePackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", (".sty",)),
    (r"\\(?:input|include)\s*\{([^}]*)\}", (".tex", "")),
    (
        r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
        ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps"),
    ),
    (r"\\lstinputlisting\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", ("",)),
)


def relative_dependencies(
    driver: Path, *, base: Path | None = None, _seen: set[Path] | None = None
) -> set[Path]:
    """Every file a document reaches by an explicit relative path, transitively.

    This exists because EECS 16A drivers say ``\\usepackage{../../../ee16}`` and
    ``\\input{../preambleFa23}``. TeX resolves a path spelled with ``../``
    against the current directory, not against TEXINPUTS -- so a mirrored copy
    that only carries the assignment's own files dies on line 2 with
    "File `../../../timestamp.sty' not found", no matter what the search path
    says. Copying these into the mirror at the *same relative offsets* is what
    makes the output tree genuinely standalone rather than a set of files that
    only build while the corpus sits next to them.

    Same-directory inputs are followed too, even though they are already copied
    wholesale: ``\\input{body}`` is what *reaches* the cross-assignment includes
    (``\\input{../../../questionBank/hw/12/q_eigen_noise.tex}`` is real), so
    skipping it because it needs no copying of its own loses everything below it.

    A reference is only ever treated as a local file when that file actually
    exists on disk, which is what keeps ``\\usepackage{tikz}`` -- a TeX-tree
    package, emphatically not ours to copy -- out of the result.

    ``base`` is the directory every relative reference resolves against, and it
    stays fixed at the driver's directory for the whole walk. That is TeX's own
    rule and not an approximation of it: a path is resolved against the *current
    directory*, never against the file that mentions it. ``sp26/dis/preambleFa23.tex``
    saying ``\\usepackage{../../fa23}`` means ``sp26/fa23.sty`` -- two levels up
    from the assignment being built -- and resolving it against the preamble's
    own directory instead points at a file that does not exist.
    """
    seen = _seen if _seen is not None else set()
    base = base if base is not None else driver.parent
    if driver in seen or not driver.is_file():
        return seen
    seen.add(driver)
    try:
        text = TexSource.from_path(driver).masked
    except Exception:  # pragma: no cover - unreadable file is caught downstream
        return seen

    for pattern, extensions in _FILE_REFERENCES:
        for match in re.finditer(pattern, text):
            for reference in match.group(1).split(","):
                reference = reference.strip()
                if not reference:
                    continue
                for extension in extensions:
                    candidate = (base / (reference + extension)).resolve()
                    if candidate.is_file():
                        if candidate.suffix in (".tex", ".sty", ".cls"):
                            relative_dependencies(candidate, base=base, _seen=seen)
                        else:
                            seen.add(candidate)
                        break
    return seen


def mirror_dependencies(
    driver: Path, source_dir: Path, target_dir: Path, mirror_root: Path
) -> list[Path]:
    """Copy relative dependencies into the mirror, preserving their offsets.

    ``../../../ee16.sty`` relative to ``<corpus>/sp26/hw/9`` must land at
    ``../../../ee16.sty`` relative to ``<out>/tex/sp26/hw/9`` -- that is, at
    ``<out>/tex/ee16.sty`` -- or TeX will not find it either.

    A dependency that would land outside ``mirror_root`` is skipped and
    reported, never written: an assignment shallow enough for its ``../`` hops
    to climb past the output root would otherwise have this tool scattering
    copies into whatever directory happens to sit above it.
    """
    mirror_root = mirror_root.resolve()
    copied: list[Path] = []
    for dependency in sorted(relative_dependencies(driver)):
        # Never the driver itself: its mirrored copy is the CONVERTED one, and
        # copying the original over it would undo the whole conversion. The
        # `destination.exists()` guard below happens to catch this too, but only
        # because materialise writes the driver first -- too load-bearing an
        # ordering to leave implicit.
        if dependency == driver.resolve():
            continue
        offset = os.path.relpath(dependency, source_dir)
        destination = Path(os.path.normpath(target_dir / offset))
        if destination == dependency or destination.exists():
            continue
        if not destination.is_relative_to(mirror_root):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dependency, destination)
        copied.append(destination)
    return copied


def require_clean_worktree(root: Path) -> None:
    """Refuse to edit a corpus in place unless git can undo it.

    In-place conversion rewrites real course material. The guard is not that git
    exists but that the worktree is *clean*: with a clean tree a bad sweep is one
    ``git checkout`` away from gone, and the diff shows exactly what the tool
    did. With a dirty tree the tool's edits are tangled with someone's
    unfinished work and neither can be reviewed separately.
    """
    if shutil.which("git") is None:
        raise LatexA11yError(
            "in-place conversion needs git to be undoable, and git is not installed",
            hint="use the default mirror mode, which never touches the corpus",
        )
    inside = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise LatexA11yError(
            f"{root} is not a git repository, so in-place edits could not be undone",
            hint="use mirror mode, or `git init` the corpus first",
        )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        listed = "\n    ".join(dirty[:8])
        more = f"\n    …and {len(dirty) - 8} more" if len(dirty) > 8 else ""
        raise LatexA11yError(
            f"the corpus has {len(dirty)} uncommitted change(s):\n    {listed}{more}",
            hint=(
                "commit or stash them first, so this tool's edits are reviewable "
                "on their own — or use the default mirror mode"
            ),
        )


# ---------------------------------------------------------------------- #
# compiling
# ---------------------------------------------------------------------- #


def compile_document(
    driver: Path,
    *,
    work_dir: Path,
    output_dir: Path,
    profile: Profile,
    jobname: str | None = None,
    search_path: list[Path] | None = None,
) -> Path:
    """Run the engine ``min_runs`` times and return the PDF path.

    Three runs is not superstition. tagpdf resolves the structure tree's
    marked-content ids through the .aux file: after a single run every ``/MCID``
    in the tree reads 1 while the content stream numbers them 0..n, so the
    reading order is wrong in a way that no error reports.
    """
    engine = profile.engine
    if shutil.which(engine.name) is None:
        raise ToolchainError(
            f"{engine.name} is not on PATH",
            hint="install TeX Live, or run `latexa11y doctor` for the full picture",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    jobname = jobname or driver.stem

    environment = dict(os.environ)
    if search_path:
        environment["TEXINPUTS"] = tex_search_path(*search_path)

    command = [
        engine.name,
        *engine.latexmk_args,
        f"-output-directory={output_dir}",
        f"-jobname={jobname}",
        str(driver),
    ]
    for _ in range(max(1, engine.min_runs)):
        try:
            subprocess.run(
                command,
                cwd=work_dir,
                env=environment,
                capture_output=True,
                timeout=engine.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexA11yError(
                f"{engine.name} timed out after {engine.timeout_seconds}s on {driver.name}",
                hint="raise engine.timeout_seconds in the profile, or fix the loop",
            ) from exc
    return output_dir / f"{jobname}.pdf"


# ---------------------------------------------------------------------- #
# inspecting the result
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class BuildReport:
    """What one assignment's conversion produced."""

    assignment: str
    ok: bool = False
    driver: str | None = None
    pdf: Path | None = None
    log: Path | None = None
    errors: list[str] = field(default_factory=list)
    tagpdf_warnings: list[str] = field(default_factory=list)
    pages: int | None = None
    bookmarks: int | None = None
    figures: int | None = None
    #: Fraction of strongly-differing pixels vs the untouched original, 0..1.
    pixel_diff: float | None = None
    #: Set when the comparison could not be made, with the reason.
    diff_note: str | None = None
    injected: list[str] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "assignment": self.assignment,
            "ok": self.ok,
            "driver": self.driver,
            "pdf": str(self.pdf) if self.pdf else None,
            "log": str(self.log) if self.log else None,
            "errors": self.errors,
            "tagpdf_warnings": self.tagpdf_warnings,
            "pages": self.pages,
            "bookmarks": self.bookmarks,
            "figures": self.figures,
            "pixel_diff": self.pixel_diff,
            "diff_note": self.diff_note,
            "injected": self.injected,
            "note": self.note,
        }


#: `-file-line-error` output: "<path>.tex:12: message". The path may be relative
#: ("./body.tex") or absolute — a mirrored build is handed an absolute driver, so
#: matching only "./" silently reported zero errors for builds that had died with
#: "Emergency stop". Anchoring on the <path>:<digits>: shape catches both.
_LOG_ERROR = re.compile(r"^(?P<file>\S*\.(?:tex|sty|cls|ltx)):(?P<line>\d+): (?P<message>.+)$")

#: Failures that do not always carry a file:line prefix. Without these a log can
#: end in "Fatal error occurred, no output PDF file produced!" and still parse as
#: clean, which is the exact false pass this package exists to prevent.
_LOG_FATAL = (
    "Emergency stop",
    "Fatal error occurred",
    "! LaTeX Error:",
    "! Undefined control sequence",
    "! Package",
)


def _log_findings(log: Path) -> tuple[list[str], list[str]]:
    """(LaTeX errors, tagpdf warnings) from a build log."""
    if not log.is_file():
        return [], []
    text = log.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    warnings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _LOG_ERROR.match(stripped) or stripped.startswith(_LOG_FATAL):
            errors.append(stripped)
        elif "tagpdf Warning" in line:
            warnings.append(stripped)
    return errors, warnings


def inspect_pdf(pdf: Path) -> dict:
    """Page count, bookmark count and Figure count, degrading gracefully."""
    facts: dict = {"pages": None, "bookmarks": None, "figures": None}
    if not pdf.is_file():
        return facts
    try:
        import pikepdf
    except ImportError:
        return facts

    try:
        with pikepdf.open(pdf) as document:
            facts["pages"] = len(document.pages)
            try:
                with document.open_outline() as outline:

                    def count(items) -> int:
                        return sum(1 + count(item.children) for item in items)

                    facts["bookmarks"] = count(outline.root)
            except Exception:
                facts["bookmarks"] = 0
    except Exception:  # pragma: no cover - a corrupt PDF is a build failure
        return facts

    try:
        from ..check.structure import read_structure

        facts["figures"] = len(read_structure(pdf).of_tag("Figure"))
    except Exception:
        pass
    return facts


def compare_pdfs(original: Path, converted: Path) -> tuple[float | None, str | None]:
    """Fraction of strongly-differing pixels, or ``(None, reason)``.

    Both PDFs must have been built in the same minute: EECS 16A's running header
    prints ``\\timestamp``, so a pair built ten minutes apart differs on every
    single page for a reason that has nothing to do with accessibility.
    """
    if not (original.is_file() and converted.is_file()):
        return None, "one side missing"
    try:
        import pymupdf
    except ImportError:
        return None, "install the [tui] extra for PyMuPDF to measure fidelity"

    try:
        left, right = pymupdf.open(original), pymupdf.open(converted)
    except Exception as exc:  # pragma: no cover
        return None, f"unreadable: {exc}"
    if left.page_count != right.page_count:
        return None, f"{left.page_count} vs {right.page_count} pages"

    differing = total = 0
    for index in range(left.page_count):
        a = left[index].get_pixmap(dpi=_DIFF_DPI, colorspace=pymupdf.csGRAY).samples
        b = right[index].get_pixmap(dpi=_DIFF_DPI, colorspace=pymupdf.csGRAY).samples
        if len(a) != len(b):
            return None, "page sizes differ"
        differing += sum(1 for x, y in zip(a, b) if abs(x - y) > _DIFF_THRESHOLD)
        total += len(a)
    return (differing / total if total else 0.0), None


# ---------------------------------------------------------------------- #
# the whole job
# ---------------------------------------------------------------------- #


def _collect_log(pdf: Path, config: RunConfig) -> Path | None:
    """Move a build's .log into the log directory; drop the .aux beside it.

    Returns the log's new location, or its old one if the move fails -- a log
    that could not be relocated is still a log worth reading.
    """
    produced = pdf.with_suffix(".log")
    if not produced.is_file():
        return None
    destination = config.output.log_dir() / produced.name
    if destination == produced:
        return produced
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), destination)
    except OSError:  # pragma: no cover
        return produced
    for leftover in (".aux", ".out", ".annotations"):
        candidate = pdf.with_suffix(leftover)
        if candidate.is_file():
            try:
                shutil.move(str(candidate), config.output.log_dir() / candidate.name)
            except OSError:  # pragma: no cover
                pass
    return destination


def build_assignment(
    assignment: Assignment,
    config: RunConfig,
    profile: Profile,
    *,
    lines: list[str] | None = None,
    compare: bool = True,
) -> BuildReport:
    """Convert and build one assignment, reporting on the result."""
    report = BuildReport(assignment=assignment.path, driver=assignment.driver)
    if assignment.driver is None:
        report.note = "no driver file; nothing to build"
        return report

    lines = preamble_for(config, profile) if lines is None else lines
    report.injected = list(lines)

    prepared = materialise(assignment, config, profile, lines=lines)
    if not config.write:
        report.ok = True
        report.note = "dry run: nothing written"
        return report

    slug = assignment.path.replace("/", "-")
    pdf_dir = config.output.pdf_dir()

    # The untouched original first and immediately before the converted build:
    # \timestamp in the running header means a pair built minutes apart differs
    # on every page for reasons unrelated to this tool.
    original_pdf: Path | None = None
    if compare and not config.output.in_place:
        root = profile.corpus.root.resolve()
        try:
            original_pdf = compile_document(
                (root / assignment.path / assignment.driver),
                work_dir=root / assignment.path,
                output_dir=config.output.root / "baseline",
                profile=profile,
                jobname=f"{slug}-original",
            )
        except LatexA11yError:
            original_pdf = None

    pdf = compile_document(
        prepared.driver,
        work_dir=prepared.work_dir,
        output_dir=pdf_dir,
        profile=profile,
        jobname=slug,
        search_path=prepared.search_path,
    )
    report.pdf = pdf if pdf.is_file() else None
    # pdflatex writes .pdf, .log and .aux to one -output-directory. Move the log
    # into its own directory afterwards so `pdf/` holds only deliverables and the
    # layout matches what the runner tells the user it produced.
    report.log = _collect_log(pdf, config)
    report.errors, report.tagpdf_warnings = _log_findings(report.log)
    facts = inspect_pdf(pdf)
    report.pages, report.bookmarks, report.figures = (
        facts["pages"],
        facts["bookmarks"],
        facts["figures"],
    )
    if original_pdf is not None:
        report.pixel_diff, report.diff_note = compare_pdfs(original_pdf, pdf)

    report.ok = report.pdf is not None and not report.errors
    if not config.output.keep_logs and report.log and report.log.is_file():
        report.log.unlink()
        report.log = None
    return report


def source_files_for(assignment: Assignment, profile: Profile) -> list[Path]:
    """Every ``.tex`` file this assignment actually uses, in or out of its folder.

    This is the honest answer to "what does converting this assignment mean",
    and it is not the same as "the .tex files in this directory": across sp26,
    76.5% of graphics are reached by ``\\input`` from the shared question bank
    rather than living in the assignment's own folder.
    """
    if assignment.driver is None:
        return []
    directory = (profile.corpus.root.resolve() / assignment.path).resolve()
    return sorted(
        path
        for path in relative_dependencies(directory / assignment.driver)
        if path.suffix.lower() == ".tex"
    )


def describe_run(config: RunConfig, profile: Profile) -> dict:
    """Scan the run's real file set and refresh its worklogs.

    Returns a summary dict; the caller decides how to display it.
    """
    from ..catalog import build_catalog
    from ..run import iter_selected

    if not config.alt.scans:
        return {"scanned": False}

    files: list[Path] = []
    for assignment in iter_selected(profile, config):
        files.extend(source_files_for(assignment, profile))
    files = sorted(set(files))

    result = build_catalog(
        profile,
        files=files,
        write=config.write,
        output_root=config.output.root if config.write else None,
    )
    return {
        "scanned": True,
        "files": len(files),
        "call_sites": result.call_sites,
        "unique": result.unique,
        "described": result.done,
        "outstanding": len(result.outstanding),
        "worklogs": [str(path) for path in result.worklogs],
    }


def build_run(
    config: RunConfig,
    profile: Profile,
    *,
    on_start=None,
    on_finish=None,
) -> list[BuildReport]:
    """Build every assignment a config names.

    ``on_start``/``on_finish`` let the TUI draw progress without this module
    importing anything that knows about a terminal.
    """
    from ..run import iter_selected

    lines = preamble_for(config, profile)

    # Every run records how it was run, next to what it produced. Without this a
    # PDF in an output directory is unreproducible six months later: which
    # standards were on, which colours, which toolchain mode. It is written up
    # front so it survives a build that fails half way.
    if config.write:
        config.output.root.mkdir(parents=True, exist_ok=True)
        (config.output.root / "run.yaml").write_text(config.to_yaml(), encoding="utf-8")

    reports: list[BuildReport] = []
    for assignment in iter_selected(profile, config):
        if on_start:
            on_start(assignment)
        try:
            report = build_assignment(assignment, config, profile, lines=lines)
        except LatexA11yError as exc:
            report = BuildReport(
                assignment=assignment.path,
                driver=assignment.driver,
                note=str(exc),
                injected=list(lines),
            )
        reports.append(report)
        if on_finish:
            on_finish(report)
    return reports

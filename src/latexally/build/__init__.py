"""The conversion engine: turn a :class:`~latexally.run.RunConfig` into PDFs.

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
from ..errors import LatexAllyError, ToolchainError
from ..run import RunConfig
from ..discover import Assignment
from ..texlex import EditBuffer, TexSource
from ..toolchain import TaggingMode, probe

__all__ = [
    "combine_logs",
    "write_report",
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


#: Suffix on the artefact this tool PRODUCED, and on the untouched copy it was
#: measured against. Both are marked: an unsuffixed `fa19-hw-7-solution.pdf`
#: says nothing about where it came from, and in `in_place` mode it lands in the
#: course repository next to files a TA built by hand, where "which of these did
#: the tool make?" has no answer. Naming only the baseline, as this once did,
#: marks the one file nobody needs to identify.
ACCESSIBLE_SUFFIX = "accessible"
ORIGINAL_SUFFIX = "original"


def base_slug(assignment_path: str, variant: str = "document") -> str:
    """``fa19/hw/7`` + ``solution`` -> ``fa19-hw-7-solution``."""
    slug = assignment_path.replace("/", "-")
    return slug if variant == "document" else f"{slug}-{variant}"


def accessible_slug(base: str) -> str:
    """The converted build's jobname, and so its .pdf/.log/.annotations."""
    return f"{base}-{ACCESSIBLE_SUFFIX}"


def original_slug(base: str) -> str:
    """The untouched baseline's jobname, built for the visual diff."""
    return f"{base}-{ORIGINAL_SUFFIX}"

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
                    "run `latexally doctor` for the specific missing capability; "
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
    recolours = config.colors.replacements(profile)
    wants_core = (
        config.standards.bookmarks
        or config.standards.question_tags
        or config.colors.mode == "conforming"
        or bool(recolours)
        or not config.alt.strict
    )
    if config.standards.retrofit:
        lines.append("\\usepackage{latexally-ee16}")
        loaded = True
    elif wants_core:
        # The primitives without the course-specific patching. Asking for
        # bookmarks with the retrofit off would otherwise silently do nothing.
        lines.append("\\usepackage{latexally-core}")
        loaded = True
    else:
        loaded = False

    if config.standards.question_tags and not config.standards.retrofit:
        # \accessquestiontags is defined by the retrofit, which is what knows
        # what a "question" is in this course. latexally-core has no such notion,
        # so the combination is undefined -- say so rather than emit a control
        # sequence that does not exist.
        raise LatexAllyError(
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
        # Order is load-bearing. Both this and conforming-colors act from a
        # begindocument hook, and hooks run in the order they are declared, so
        # the per-name values must come second to win over the blind allySolution
        # fallbacks. Without these lines nothing the runner computed or the user
        # confirmed ever reaches the page: `conforming-colors` alone remaps to a
        # fixed palette and ignores the run entirely.
        for name, value in recolours.items():
            lines.append(f"\\accessrecolor{{{name}}}{{{value.lstrip('#')}}}")
        if not config.alt.strict:
            lines.append("\\accesssetup{strict=false}")

    if config.standards.math_speech:
        # Formula /Alt. Loaded even on the first pass, when the speech table it
        # reads does not exist yet -- the package tolerates that, and it is the
        # run that produces the table's input.
        lines.append("\\usepackage{latexally-math}")

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

    raise LatexAllyError(
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
    #: Missing includes that were resolved from elsewhere in the corpus.
    substitutions: list = field(default_factory=list)
    #: The driver as it was before conversion, mirrored so the baseline builds
    #: against the same repaired includes the converted document does.
    original: Path | None = None


#: The rules that decide whether a figure or formula actually says anything: no
#: /Alt, an unfilled placeholder, a file name, raw LaTeX read aloud. Every one
#: is a *silent* failure -- the PDF is well-formed and veraPDF passes it -- so
#: they are the ones worth failing a build over.
_ALT_RULES = ("ALLY-PDF-002", "ALLY-PDF-003", "ALLY-PDF-004", "ALLY-PDF-040", "ALLY-PDF-041")


def _alt_text_failures(pdf: Path, config: RunConfig) -> list[str]:
    """Alt-text errors in the built PDF, as build errors, under strict mode.

    ``check_pdf_structure`` used to be reachable only from ``latexally check``,
    so a build could emit a PDF whose every figure was described by its own file
    name and still report a clean tick. ``alt.strict`` meanwhile only ever
    reached LaTeX, where it guards a placeholder blocklist that nothing in this
    corpus triggers. Checking the artefact here is what makes the setting mean
    what its name says.
    """
    if not config.alt.strict or pdf is None or not pdf.is_file():
        return []
    from ..check.rules import Severity, check_pdf_structure

    try:
        findings = check_pdf_structure(pdf, require_bookmarks=config.standards.bookmarks)
    except Exception as exc:  # a check that crashes must not mask the build
        return [f"alt-text check could not run: {exc}"]
    return [
        f"{finding.rule}: {finding.message}"
        for finding in findings
        if finding.severity is Severity.ERROR and finding.rule in _ALT_RULES
    ]


def apply_descriptions(
    prepared: Prepared, config: RunConfig, profile: Profile
) -> int:
    r"""Wrap the mirror's figures in ``Described``. Returns how many.

    This is the step that turns a worklog into accessible output, and it runs
    against ``prepared.work_dir`` -- the mirrored copy -- so the corpus is never
    edited. Figure ids are content hashes, so a description written against the
    corpus file matches its mirrored twin without any path bookkeeping.

    The baseline ``-original.tex`` is deliberately excluded: it exists to be
    compiled *unconverted* for the visual diff, and wrapping its figures would
    make the comparison measure this tool against itself.
    """
    if not config.alt.scans:
        return 0

    from ..apply import apply_scope
    from ..catalog import build_catalog, load_entries

    # The dependency walk, NOT a glob of the assignment directory: this corpus
    # keeps its figures in shared question files three levels up
    # (`fall19_questionBank/hw/7/q_multitouch_new.tex`), which a walk of
    # `work_dir` never reaches. Run against the mirrored driver, so the paths
    # that come back are the mirror's own copies.
    files = sorted(
        path
        for path in relative_dependencies(prepared.driver)
        if path.suffix.lower() == ".tex" and not path.stem.endswith("-original")
    )
    if not files:
        return 0

    # Catalogue from the MIRROR, not the corpus. `describe_run` scans the corpus
    # before any mirror exists, where this corpus's cross-semester includes are
    # dangling -- `repair_missing` resolves them while materialising, so the
    # mirror is the first place the document is whole. Scanning it here is what
    # makes the worklog list every figure instead of the handful reachable
    # before the repair. Ids are content hashes, so the two agree on any figure
    # both can see.
    build_catalog(
        profile,
        files=files,
        write=config.write,
        output_root=config.output.root if config.write else None,
        # The mirror repeats the corpus's directory layout, so sharding against
        # its root yields exactly the worklog names a corpus scan would.
        shard_root=config.output.tex_dir(),
    )

    entries = load_entries(profile, config.output.root)
    if not entries:
        return 0

    plans = apply_scope(
        profile,
        None,
        entries,
        dry_run=False,
        placeholders=config.alt.injects,
        files=files,
    )
    return sum(plan.wrapped for plan in plans)


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
    driver: str | None = None,
    siblings_to_skip: frozenset[str] = frozenset(),
) -> Prepared:
    """Produce a buildable, converted copy of one assignment.

    In ``mirror`` mode the corpus is never touched: the assignment's own ``.tex``
    files are copied into the output tree and the driver is converted there. The
    original directory is still added to TEXINPUTS, so shared assets that live
    outside the assignment -- ``ee16.sty`` three levels up, ``figures/``,
    ``timestamp.sty`` -- resolve back to the corpus without being copied.
    """
    driver_name = driver or assignment.driver
    if driver_name is None:
        raise LatexAllyError(
            f"{assignment.path} has no driver file to build",
            hint="a driver is the .tex containing \\begin{document}",
        )
    write = config.write if write is None else write
    substitutions: list = []
    root = profile.corpus.root.resolve()
    source_dir = (root / assignment.path).resolve()
    lines = preamble_for(config, profile) if lines is None else lines

    source = TexSource.from_path(source_dir / driver_name)
    converted = inject(source, lines)

    mirror_root = config.output.tex_dir().resolve()
    target_dir = (mirror_root / assignment.path).resolve()
    driver = target_dir / driver_name
    original = target_dir / f"{Path(driver_name).stem}-original.tex"
    if write:
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in (".tex", ".sty", ".cls"):
                # Never copy the original over a driver this run converts. An
                # assignment has several drivers built in turn, and copying
                # siblings wholesale meant the `problem` pass laid the ORIGINAL
                # sol9.tex over the converted one written moments earlier: the
                # PDFs were right, the mirrored tree was not, and rebuilding
                # from it produced an untagged document.
                if path.name != driver_name and path.name not in siblings_to_skip:
                    shutil.copy2(path, target_dir / path.name)
        driver.write_bytes(source.encode(converted))
        # The untouched driver, beside the converted one and inside the same
        # mirror. The baseline used to compile from the corpus, where a
        # historical assignment's includes are still missing -- so every
        # repaired document produced no "before" PDF and its pixel diff read
        # "one side missing". Built from here, both sides see the same repaired
        # includes and the diff measures only what the conversion changed.
        original = target_dir / f"{Path(driver_name).stem}-original.tex"
        original.write_bytes(source.encode(source.text))
        # Everything the driver reaches by an explicit relative path, at the
        # same offsets, so the mirror builds without the corpus beside it.
        mirror_dependencies(
            source_dir / driver_name, source_dir, target_dir, mirror_root
        )
        substitutions = repair_missing(
            source_dir / driver_name, root, mirror_root, assignment.path
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
        substitutions,
        original,
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


def repair_missing(
    driver: Path, corpus_root: Path, mirror_root: Path, assignment_path: str
) -> list:
    """Place a stand-in for every include this document cannot resolve.

    Historical assignments reference questions the live bank has since retired;
    the files survive in the frozen per-semester snapshots. See
    :mod:`latexally.repair` for how one is chosen, and why choosing carefully
    matters. Nothing is written to the corpus -- the replacement lands in the
    mirror at the path the source asked for, so the source is never edited and
    the substitution is a file you can diff.
    """
    from ..repair import assets_beside, find_replacements, unresolved_packages
    from ..texlex.includes import IncludeGraph

    graph = IncludeGraph([corpus_root])
    resolved, _ = graph.transitive_inputs(driver)
    unresolved: list[tuple[Path, str]] = []
    for source in [driver, *resolved]:
        try:
            _, missing = graph.direct_inputs(source)
        except OSError:
            continue
        unresolved.extend((source, target) for target in missing)
        # A package loaded by path is a dependency the include graph never
        # followed, so a missing one stopped the build with nothing to point at.
        unresolved.extend((source, target) for target in unresolved_packages(source))
    if not unresolved:
        return []

    semester = assignment_path.split("/", 1)[0]
    substitutions = find_replacements(
        unresolved,
        corpus_root=corpus_root,
        mirror_root=mirror_root,
        semester=semester,
    )
    for substitution in substitutions:
        for target in (substitution.destination, substitution.alias):
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(substitution.used, target)
        # A question carries its figures beside it. Copying the .tex alone
        # builds a PDF with blank boxes where the circuits should be.
        for asset, target in assets_beside(
            substitution.used, substitution.destination
        ):
            if target.is_relative_to(mirror_root) and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, target)
    return substitutions


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
        raise LatexAllyError(
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
        raise LatexAllyError(
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
        raise LatexAllyError(
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
    math_dir: Path | None = None,
) -> Path:
    """Run the engine ``min_runs`` times and return the PDF path.

    Three runs is not superstition. tagpdf resolves the structure tree's
    marked-content ids through the .aux file: after a single run every ``/MCID``
    in the tree reads 1 while the content stream numbers them 0..n, so the
    reading order is wrong in a way that no error reports.

    Math speech rides on the same repetition: the first run writes the list of
    formulas latex-lab tagged, the conversion happens between runs, and the
    remaining runs pick the speech up. No extra compilation is bought.

    ``math_dir`` is where the generated MathML and speech table go. It is not
    the PDF directory: that holds deliverables, and a reader handed a folder of
    PDFs should not have to pick them out from among the machinery.
    """
    engine = profile.engine
    if shutil.which(engine.name) is None:
        raise ToolchainError(
            f"{engine.name} is not on PATH",
            hint="install TeX Live, or run `latexally doctor` for the full picture",
        )
    # Absolute before it reaches the subprocess: -output-directory is resolved
    # against the child's cwd, which is the directory being built, not ours.
    output_dir = output_dir.absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    jobname = jobname or driver.stem

    environment = dict(os.environ)
    # The output directory has to be on the *input* path too. TeX does not put
    # it there: -output-directory governs where files are written, and an
    # \input of a generated file next to the .aux fails with "File not found"
    # -- verified, and silent in nonstop mode. The generated math speech table
    # lives there, and it must not be written into the corpus instead.
    inputs = [output_dir] + ([math_dir] if math_dir else []) + list(search_path or [])
    environment["TEXINPUTS"] = tex_search_path(*inputs)

    command = [
        engine.name,
        *engine.latexmk_args,
        f"-output-directory={output_dir}",
        f"-jobname={jobname}",
        str(driver),
    ]
    runs = max(1, engine.min_runs)
    for index in range(runs):
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
            raise LatexAllyError(
                f"{engine.name} timed out after {engine.timeout_seconds}s on {driver.name}",
                hint="raise engine.timeout_seconds in the profile, or fix the loop",
            ) from exc
        if math_dir is not None and index == 0 and runs > 1:
            _convert_math(output_dir, math_dir, jobname)
    return output_dir / f"{jobname}.pdf"


def _convert_math(output_dir: Path, math_dir: Path, jobname: str) -> None:
    """Turn the formulas the first run reported into speech for the next one.

    Never fatal. A missing Node, a missing extra, or one unconvertible equation
    must not destroy an otherwise good build: the result is a Formula without
    an /Alt, and ``ALLY-PDF-040`` reports that as an error against the artefact,
    which is a finding somebody can act on.
    """
    from ..mathspeech import convert, read_dummy, write_sources

    # The dummy is written by the engine, which sends every stream to its
    # -output-directory; the generated files are ours and go elsewhere.
    dummy = output_dir / f"{jobname}-mathml-dummy.html"
    if not dummy.is_file():
        return
    try:
        formulas = read_dummy(dummy)
        if not formulas:
            return
        math_dir.mkdir(parents=True, exist_ok=True)
        # The cache lives with them, keyed by latex-lab's hash: a rebuild after
        # editing one equation converts one equation.
        results = convert(formulas, cache=math_dir / f"{jobname}-mathspeech.json")
        write_sources(results, formulas, jobname, math_dir)
    except LatexAllyError:
        return


# ---------------------------------------------------------------------- #
# inspecting the result
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class BuildReport:
    """What one assignment's conversion produced."""

    assignment: str
    ok: bool = False
    #: Which document of the assignment this is: solution, problem, answer.
    variant: str = "document"
    driver: str | None = None
    pdf: Path | None = None
    log: Path | None = None
    errors: list[str] = field(default_factory=list)
    tagpdf_warnings: list[str] = field(default_factory=list)
    pages: int | None = None
    bookmarks: int | None = None
    figures: int | None = None
    #: Figures wrapped in `Described` from an approved worklog entry this build.
    described: int = 0
    #: Fraction of strongly-differing pixels vs the untouched original, 0..1.
    pixel_diff: float | None = None
    #: Set when the comparison could not be made, with the reason.
    diff_note: str | None = None
    injected: list[str] = field(default_factory=list)
    note: str | None = None
    #: Includes the corpus could not resolve, and what stood in for them.
    substitutions: list = field(default_factory=list)

    @property
    def substituted(self) -> bool:
        """True when this document contains a question from another semester."""
        return bool(self.substitutions)

    @property
    def uncertain(self) -> bool:
        """True when a stand-in was picked from candidates that DIFFER.

        Such a document is not a faithful conversion of anything and must never
        be reported as a clean one.
        """
        return any(item.ambiguous for item in self.substitutions)

    @property
    def built(self) -> bool:
        """A PDF came out. ``ok`` additionally requires a clean log.

        The two are not the same outcome and must not read as one. A missing
        ``\\input`` stops the run dead and produces nothing; a "Missing number,
        treated as zero" leaves a PDF that opens, paginates and diffs -- with a
        dimension silently wrong somewhere inside it. Reporting both as
        "failed" sent people looking for output that was already on disk;
        reporting both as "ok" is the false pass this package exists to
        prevent.
        """
        return self.pdf is not None

    def as_dict(self) -> dict:
        return {
            "assignment": self.assignment,
            "ok": self.ok,
            "built": self.built,
            "substitutions": [item.as_dict() for item in self.substitutions],
            "variant": self.variant,
            "driver": self.driver,
            "pdf": str(self.pdf) if self.pdf else None,
            "log": str(self.log) if self.log else None,
            "errors": self.errors,
            "tagpdf_warnings": self.tagpdf_warnings,
            "pages": self.pages,
            "bookmarks": self.bookmarks,
            "figures": self.figures,
            "described": self.described,
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


def _log_findings(log: Path | None) -> tuple[list[str], list[str]]:
    """(LaTeX errors, tagpdf warnings) from a build log.

    ``None`` is a real case, not defensive padding: a build that never got far
    enough to write a log has no log to read. Crashing here would replace the
    build failure the user needs to see with an AttributeError from the
    reporting code.
    """
    if log is None or not log.is_file():
        return [], []
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    errors: list[str] = []
    warnings: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if _LOG_ERROR.match(stripped) or stripped.startswith(_LOG_FATAL):
            errors.append(_unwrap(lines, index))
        elif "tagpdf Warning" in line:
            warnings.append(_unwrap(lines, index))
    dropped = text.count(_TOUNICODE_DROPPED)
    if dropped:
        warnings.append(
            f"pdfTeX dropped {dropped} ToUnicode mapping(s) as out of range; "
            "ligatures and composed symbols will extract as presentation forms"
        )
    return errors, warnings


#: pdfTeX says this when \pdfglyphtounicode is handed a value it cannot parse,
#: and then SILENTLY DROPS the mapping. It happened here because
#: glyphtounicode.tex spells multi-codepoint entries "{0066 0066 0069}" and was
#: being read under expl3 catcodes, where space is ignored -- 119 glyphs per
#: document, every one of which then extracted as its presentation form
#: ("difficult" -> "di<U+FB03>cult"), breaking search and reading badly aloud.
#: Cheap to check, invisible otherwise: the build is "clean" while it happens.
_TOUNICODE_DROPPED = "ToUnicode: value out of range"


#: TeX hard-wraps its log at `max_print_line` (79 by default) with no
#: continuation marker, so a message is routinely cut mid-word: "LaTeX Error:
#: There's n" / "o line here to end." Reporting the first fragment alone tells a
#: user almost nothing, and the break lands in a different place every time.
_WRAP_WIDTH = 79


def _unwrap(lines: list[str], index: int, limit: int = 3) -> str:
    """Rejoin a log message TeX split across lines.

    Only continues while the previous line is full-width, which is what makes
    this safe: a message shorter than the wrap width was never split, so nothing
    unrelated is ever glued onto it.
    """
    # lstrip, never strip: TeX breaks at exactly max_print_line without
    # dropping a character, so a space sitting on the boundary is part of the
    # message. Stripping it turned "not found" into "notfound" -- and would
    # silently close up a path that happened to break on a space.
    joined = lines[index].lstrip()
    cursor = index
    while len(lines[cursor]) >= _WRAP_WIDTH and cursor + 1 < len(lines) and limit > 0:
        cursor += 1
        nxt = lines[cursor]
        if not nxt.strip() or _LOG_ERROR.match(nxt.strip()):
            break
        joined += nxt
        limit -= 1
    return " ".join(joined.split())


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
    """Move a build's .log into the log directory and bin the intermediates.

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
    # pdflatex drops the formula list beside the PDF so the speech pass can
    # find the formulas; read_dummy consumes it and nothing reads it again.
    # Filing it under math/ only moved the clutter -- unlike its neighbours
    # there, which the *next* LaTeX run reads: `-mathml.html` carries the
    # MathML and `-mathspeech.ltx` the spoken strings. Those two are inputs,
    # not debris, and deleting them would silently cost the run its spoken
    # math.
    dummy = pdf.parent / f"{pdf.stem}-mathml-dummy.html"
    if dummy.is_file():
        try:
            dummy.unlink()
        except OSError:  # pragma: no cover
            pass
    # .aux and .out are cross-reference intermediates, rewritten from scratch
    # on every run and readable by nothing. They used to be *moved* into the
    # log directory, which kept pdf/ tidy by making logs/ the dumping ground
    # instead -- fifteen files to find one. .annotations is kept for now so
    # combine_logs can fold anything it actually says into run.log.
    for leftover in (".aux", ".out"):
        candidate = pdf.with_suffix(leftover)
        if candidate.is_file():
            try:
                candidate.unlink()
            except OSError:  # pragma: no cover
                pass
    annotations = pdf.with_suffix(".annotations")
    if annotations.is_file():
        try:
            shutil.move(str(annotations), config.output.log_dir() / annotations.name)
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
    variant: str = "document",
    driver: str | None = None,
    siblings_to_skip: frozenset[str] = frozenset(),
) -> BuildReport:
    """Convert and build ONE variant of one assignment.

    An assignment is several documents built from one body -- solutions, the
    blank handout students receive, sometimes answers-only -- so the caller says
    which. :func:`build_run` expands the selection; this builds one.
    """
    driver = driver or assignment.driver
    report = BuildReport(assignment=assignment.path, variant=variant, driver=driver)
    if driver is None:
        report.note = "no driver file; nothing to build"
        return report

    lines = preamble_for(config, profile) if lines is None else lines
    report.injected = list(lines)

    prepared = materialise(
        assignment,
        config,
        profile,
        lines=lines,
        driver=driver,
        siblings_to_skip=siblings_to_skip,
    )
    report.substitutions = prepared.substitutions
    if not config.write:
        report.ok = True
        report.note = "dry run: nothing written"
        return report

    # Approved descriptions become real /Alt HERE, in the mirror `materialise`
    # just wrote -- never in the corpus. Without this step a run scans figures,
    # writes worklogs, and then builds a PDF whose figures still carry
    # latex-lab's default alt: the source file name, read aloud verbatim.
    report.described = apply_descriptions(prepared, config, profile)

    slug = base_slug(assignment.path, variant)
    # `in-place` is a destination for the PDF, not a licence to edit the source.
    # It used to rewrite the corpus driver, guarded by a clean git worktree; the
    # conversion is now always mirrored and the only thing that reaches the
    # corpus is the finished document, beside the original it was built from.
    if config.output.in_place:
        # Additive, but not harmless: a PDF of the same name may already sit
        # there. The clean-worktree guard is what makes that revertible, and it
        # is the same one this mode has always used.
        require_clean_worktree(profile.corpus.root.resolve())
        pdf_dir = (profile.corpus.root / assignment.path).resolve()
    else:
        pdf_dir = config.output.pdf_dir()

    # The untouched original first and immediately before the converted build:
    # \timestamp in the running header means a pair built minutes apart differs
    # on every page for reasons unrelated to this tool.
    original_pdf: Path | None = None
    if compare and prepared.original is not None:
        try:
            original_pdf = compile_document(
                prepared.original,
                work_dir=prepared.work_dir,
                output_dir=config.output.baseline_dir(),
                profile=profile,
                jobname=original_slug(slug),
                search_path=prepared.search_path,
            )
        except LatexAllyError:
            original_pdf = None

    pdf = compile_document(
        prepared.driver,
        work_dir=prepared.work_dir,
        output_dir=pdf_dir,
        profile=profile,
        jobname=accessible_slug(slug),
        search_path=prepared.search_path,
        math_dir=config.output.math_dir() if config.standards.math_speech else None,
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

    report.errors += _alt_text_failures(pdf, config)

    report.ok = report.pdf is not None and not report.errors
    if report.pdf is None and not report.errors:
        # No PDF and nothing in the log to explain it -- usually the engine
        # never ran. Say so, rather than showing a bare ✗ with zero errors.
        report.note = (
            f"{profile.engine.name} produced no PDF and no readable log; "
            f"expected {pdf}"
        )
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
    from ..discover import iter_selected

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


def combine_logs(config: RunConfig, reports: list[BuildReport]) -> Path | None:
    """Fold every document's LaTeX log into one file for the run.

    pdflatex writes one log per document, so a directory that used to hold
    three of them holds thirty-eight after a fortnight, and finding the run you
    care about means reading timestamps. One file per run, sections banner-
    separated and greppable, and each report repointed at it so every "full
    log:" line in the output leads somewhere that exists.
    """
    if not config.write:
        return None
    merged = [report for report in reports if report.log and report.log.is_file()]
    if not merged:
        return None
    log_dir = config.output.log_dir()
    combined = log_dir / "run.log"
    parts: list[str] = []
    baseline_dir = config.output.baseline_dir()
    for report in merged:
        slug = _slug_for(report)
        parts.append(f"{'=' * 78}\n=== {slug}\n{'=' * 78}\n")
        parts.append(report.log.read_text(encoding="utf-8", errors="replace"))
        parts.append(_annotations_of(log_dir / f"{accessible_slug(slug)}.annotations"))
        # The untouched build's log belongs here too. It is the half that
        # explains a "one side missing" diff, and it was being left in
        # baseline/ beside its own pile of .aux and .out.
        before = baseline_dir / f"{original_slug(slug)}.log"
        if before.is_file():
            parts.append(f"{'=' * 78}\n=== {slug}-original\n{'=' * 78}\n")
            parts.append(before.read_text(encoding="utf-8", errors="replace"))
    combined.parent.mkdir(parents=True, exist_ok=True)
    combined.write_text("\n".join(parts), encoding="utf-8")
    for report in merged:
        if report.log != combined:
            try:
                report.log.unlink()
            except OSError:  # pragma: no cover
                pass
        report.log = combined
    # Both directories hold only deliverables now: run.log here, PDFs there.
    # Anything with these extensions is a LaTeX intermediate this tool put
    # there, including the ones left by runs before it stopped doing that.
    strays = [
        path
        for directory in (log_dir, baseline_dir)
        for pattern in ("*.aux", "*.out", "*.annotations", "*-original.log")
        for path in directory.glob(pattern)
    ]
    for leftover in strays:
        try:
            leftover.unlink()
        except OSError:  # pragma: no cover
            pass
    return combined


def _annotations_of(path: Path) -> str:
    """The tagging annotations for one document, when it wrote any.

    latex-lab emits an ``.annotations`` file per document; almost always it is
    the eighteen bytes of its own header and nothing else. Folded in when there
    is something to fold, skipped when there is not, rather than left as a file
    per document beside the log.
    """
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return ""
    body = [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("BEGIN ANNOTATIONS", "END ANNOTATIONS"))
    ]
    if not body:
        return ""
    return "\n".join([f"--- annotations: {path.stem}", *body, ""])


def _slug_for(report: BuildReport) -> str:
    """The BASE name of an assignment's artefacts, with no provenance suffix."""
    return base_slug(report.assignment, report.variant)


def write_report(config: RunConfig, reports: list[BuildReport]) -> Path | None:
    """Write a plain-text account of the run beside what it produced.

    ``run.yaml`` records how the run was configured and says nothing about how
    it went, and the runner's own report goes back to the terminal it came
    from. Neither survives closing the window, which left "it failed" as
    something to be retyped by hand. This is greppable, pasteable, and sits
    next to the PDFs it describes.
    """
    if not config.write:
        return None
    path = config.output.root / "build-log.txt"
    lines = [
        f"latexally {len(reports)} document(s), profile {config.profile}",
        f"output: {config.output.root}  ({config.output.write_mode})",
        "",
        f"{'assignment':<28} {'document':<10} {'state':<10} "
        f"{'pages':>5} {'marks':>5} {'figs':>5}  pixel diff",
    ]
    for report in reports:
        state = "ok" if report.ok else ("errors" if report.built else "FAILED")
        if report.uncertain:
            state = "SUBSTITUTED"
        elif report.substituted and state == "ok":
            state = "ok (repaired)"
        diff = (
            f"{100 * report.pixel_diff:.2f}%"
            if report.pixel_diff is not None
            else (report.diff_note or "-")
        )
        lines.append(
            f"{report.assignment:<28} {report.variant:<10} {state:<10} "
            f"{_or_dash(report.pages):>5} {_or_dash(report.bookmarks):>5} "
            f"{_or_dash(report.figures):>5}  {diff}"
        )
    for report in reports:
        if report.ok:
            continue
        lines += [
            "",
            f"{report.assignment} ({report.variant}) "
            + (
                f"built, with {len(report.errors)} error(s) in the log"
                if report.built
                else "failed - no PDF"
            ),
        ]
        if report.note:
            lines.append(f"  {report.note}")
        lines += [f"  {line}" for line in report.errors]
        lines += [f"  tagpdf: {line}" for line in report.tagpdf_warnings[:10]]
        if report.pdf:
            lines.append(f"  pdf: {report.pdf}")
        if report.log:
            lines.append(
                f"  full log: {report.log}  (search '=== {_slug_for(report)}')"
            )
    repaired = [report for report in reports if report.substituted]
    if repaired:
        lines += [
            "",
            "=" * 78,
            "SUBSTITUTED INCLUDES",
            "=" * 78,
            "",
            "These files were missing from the corpus. Each was found elsewhere",
            "and copied into the output mirror so the document could build. The",
            "corpus was NOT modified -- to make the fix permanent, apply the",
            "action under each entry.",
            "",
            "A line marked DIFFERS means the candidate banks did not agree: the",
            "stand-in is one of several versions and may not be the question the",
            "assignment originally asked. Check that one by hand before shipping",
            "the PDF.",
        ]
        for report in repaired:
            lines += ["", f"{report.assignment} ({report.variant})"]
            for item in report.substitutions:
                mark = "  DIFFERS  " if item.ambiguous else "  ok       "
                lines += [
                    f"{mark}{item.wanted}",
                    f"             referenced by: {item.referenced_by}",
                    f"             stood in from: {item.used}",
                    f"             fix: {item.fix}",
                ]
                if item.ambiguous:
                    lines.append(
                        f"             {len(item.candidates)} candidates, not "
                        "all identical:"
                    )
                    lines += [
                        f"               {path}" for path in item.candidates[:8]
                    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _or_dash(value: int | None) -> str:
    return "-" if value is None else str(value)


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
    from ..discover import iter_selected

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
        # Every variant the assignment has, unless the run named a subset. The
        # blank handout is the document students are actually given; converting
        # only the solutions would leave the one that matters most untagged.
        variants = assignment.variants_for(config.variants)
        if not variants:
            # Two different situations, and conflating them is misleading: a
            # directory with no document at all, versus one that simply has no
            # copy of the version this run asked for.
            if assignment.drivers:
                note = (
                    "has no "
                    + " or ".join(config.variants)
                    + " version; it has "
                    + ", ".join(sorted(assignment.drivers))
                )
            else:
                note = "no driver file; nothing to build"
            reports.append(
                BuildReport(
                    assignment=assignment.path,
                    driver=None,
                    note=note,
                    injected=list(lines),
                )
            )
            continue
        # Each of these is converted in its own pass, so none may be copied
        # over as an original by another pass.
        converted = frozenset(variants.values())
        for variant, driver in variants.items():
            if on_start:
                on_start(assignment, variant)
            try:
                report = build_assignment(
                    assignment,
                    config,
                    profile,
                    lines=lines,
                    variant=variant,
                    driver=driver,
                    siblings_to_skip=converted,
                )
            except LatexAllyError as exc:
                report = BuildReport(
                    assignment=assignment.path,
                    variant=variant,
                    driver=driver,
                    note=str(exc),
                    injected=list(lines),
                )
            reports.append(report)
            if on_finish:
                on_finish(report)
    combine_logs(config, reports)
    write_report(config, reports)
    return reports

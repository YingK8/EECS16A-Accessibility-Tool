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
from concurrent.futures import ThreadPoolExecutor
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
        # Turning tagging on makes the kernel define names it did not define
        # before, and a course package that defines the same name then dies with
        # "Command \\proof already defined" -- on source that has compiled for
        # years and still compiles untagged. Measured: it took out every
        # document that loads ee16.sty.
        #
        # Freeing the name before the package loads is NOT enough, and the
        # first version of this did only that. ee16.sty defines `proof' at line
        # 22 and then does `\\let\\proof\\relax' itself at line 365, expecting
        # amsthm to supply one afterwards. Under \\DocumentMetadata amsthm does
        # not: the kernel has already declared a tagged `proof', so amsthm's own
        # `\\newenvironment{proof}' is suppressed and the name is left \\relax.
        # Every document that actually writes \\begin{proof} then failed with
        # "Environment proof undefined" -- 60 files in this corpus. Freeing the
        # name only changed which error you got, which is why a build of a
        # document that merely *loads* ee16 looked fixed.
        #
        # So: save what the kernel defined, free it so the course package's own
        # definition does not error, and restore it afterwards. **[verified]**
        # the kernel's tagged `proof' and amsthm's render identically.
        #
        # The hook has to be installed before the package is read, and it cannot
        # live in latexally-ee16.sty, which loads after it. It also cannot be
        # inserted between \\documentclass and the course \\usepackage, because a
        # shared preamble brings both in from the same included file. So it
        # leads the document, beside \\DocumentMetadata.
        for package, names in sorted(engine.unlet_before.items()):
            if not names:
                continue
            slug = re.sub(r"[^A-Za-z]", "", package)
            saves, restores = [], []
            for name in names:
                for command in (name, f"end{name}"):
                    keep = f"latexallykept{slug}{command}"
                    saves.append(
                        f"\\expandafter\\let\\csname {keep}\\expandafter\\endcsname"
                        f"\\csname {command}\\endcsname\\let\\{command}\\relax"
                    )
                    restores.append(
                        f"\\expandafter\\let\\csname {command}\\expandafter\\endcsname"
                        f"\\csname {keep}\\endcsname"
                    )
            lines.append(f"\\AddToHook{{file/{package}/before}}{{{''.join(saves)}}}")
            lines.append(f"\\AddToHook{{file/{package}/after}}{{{''.join(restores)}}}")

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


#: Lines that have to sit above ``\documentclass``, in this order.
#:
#: ``\DocumentMetadata`` is only honoured as the *first* line of the document;
#: anywhere else the kernel ignores it and the build is silently untagged.
#:
#: ``\AddToHook{file/…/before}`` has to lead for a different reason: it must be
#: installed before the package it guards is read, and for a shared preamble the
#: course ``\usepackage`` and ``\documentclass`` arrive in the same included
#: file, so there is no line between them to insert at.
_LEADING = ("\\DocumentMetadata", "\\AddToHook")


def split_preamble(lines: list[str]) -> tuple[list[str], list[str]]:
    """Separate the lines that must lead the file from the rest."""
    first = [line for line in lines if line.startswith(_LEADING)]
    rest = [line for line in lines if not line.startswith(_LEADING)]
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


def rewrite_incompatibilities(
    prepared: Prepared, *, mode: TaggingMode | None = None
) -> dict[str, int]:
    """Fix the constructs tagging cannot compile, in the MIRROR.

    Runs before :func:`apply_descriptions` rather than beside it, so the two
    edit passes never share an ``EditBuffer`` and a rewrite conflict can never
    be misread as an alt-text bug.

    ``prepared.driver`` is the *mirrored* driver, so every path
    :func:`relative_dependencies` returns is a mirror path and the corpus is
    unreachable from here -- the same guarantee :func:`apply_descriptions`
    relies on. ``-original.tex`` is excluded for the same reason it is there:
    it is compiled unconverted for the visual diff, and rewriting it would make
    the comparison measure this tool against itself.
    """
    from ..rewrite import FIXED_BY_TAGGING, rewrite_files

    # On a toolchain with `tagging=on` the kernel already handles some of these,
    # and rewriting them would be 667 edits of churn per run. Measured, not
    # assumed: see FIXED_BY_TAGGING.
    skip = FIXED_BY_TAGGING if mode is TaggingMode.MODERN else frozenset()
    files = [
        path
        for path in relative_dependencies(prepared.driver)
        if path.suffix.lower() == ".tex" and not path.stem.endswith("-original")
    ]
    counts: dict[str, int] = {}
    for plan in rewrite_files(files, write=True, skip=skip):
        for rule, sites in plan.counts().items():
            counts[rule] = counts.get(rule, 0) + sites
    return counts


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
    # In `edit` mode the worklog belongs beside the .tex, in the corpus. The
    # scan runs against the mirror -- that is where the document is whole --
    # so the sources are the mirror's and the destination is the corpus, which
    # is exactly the pair of roots these two arguments name.
    beside = profile.corpus.root.resolve() if config.output.edits_sources else None
    build_catalog(
        profile,
        files=files,
        write=config.write,
        output_root=config.output.root if config.write else None,
        # The mirror repeats the corpus's directory layout, so sharding against
        # its root yields exactly the worklog names a corpus scan would.
        shard_root=config.output.tex_dir(),
        beside=beside,
    )

    entries = load_entries(profile, config.output.root, beside=beside)
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
    r"""Place a stand-in for every include this document cannot resolve.

    Historical assignments reference questions the live bank has since retired;
    the files survive in the frozen per-semester snapshots. See
    :mod:`latexally.repair` for how one is chosen, and why choosing carefully
    matters. Nothing is written to the corpus: the replacement lands in the
    mirror at the path the source asked for, so the source is never edited and
    the substitution is a file you can diff.

    Two questions are being asked, and each needs a different view of the
    document:

    what the SOURCE asks for
        Answered against the corpus. It is the same answer for every variant of
        an assignment. Asking the mirror instead lets the second variant see the
        first variant's stand-ins already in place and call itself clean, so
        ``prob9.tex`` -- the file students actually receive -- would ship a
        substituted question with nothing on the report saying so.
    what the STAND-INS ask for
        Answered against the mirror, and only once they are on disk. fa18's
        ``q_matrix_visualization`` says
        ``\input{../../../fa18_questionBank/sec/dis2A/figures/rotate1}``, which
        no single pass can see.

    So: the corpus once, then the mirror until a round finds nothing new. Four
    rounds is the observed depth plus headroom, and ``seen`` terminates a cycle
    regardless of depth.

    @param driver: the corpus driver being converted
    @param corpus_root: where stand-ins are searched for; never written to
    @param mirror_root: the output ``tex/`` tree; every write lands under it
    @param assignment_path: corpus-relative assignment directory, e.g. ``sp26/hw/9``
    @return: every :class:`~latexally.repair.Substitution` made, gaps included
    """
    from ..repair import (
        assets_beside,
        find_replacements,
        unresolved_references,
        write_gap_note,
    )

    semester = assignment_path.split("/", 1)[0]
    mirrored_driver = mirror_root / assignment_path / driver.name
    placed: list = []
    seen: set[tuple[str, str]] = set()

    # Round 0 reads the corpus, the rest read the mirror. See the docstring.
    for round_number in range(4):
        from_corpus = round_number == 0 or not mirrored_driver.is_file()
        scan_from = driver if from_corpus else mirrored_driver
        scan_root = corpus_root if from_corpus else mirror_root
        fresh = [
            (source, target)
            for source, target in unresolved_references(scan_from, scan_root)
            if (str(source), target) not in seen
        ]
        if not fresh:
            if from_corpus:
                continue  # the corpus is clean; the stand-ins may not be
            break
        seen.update((str(source), target) for source, target in fresh)
        substitutions = find_replacements(
            # Distance is measured in the corpus, so a mirrored file is asked
            # about under the name it has there. A file that exists only in the
            # mirror -- a stand-in placed a moment ago -- keeps its mirror path
            # and simply ranks from where it sits.
            [(_in_corpus(source, mirror_root, corpus_root), target) for source, target in fresh],
            corpus_root=corpus_root,
            mirror_root=mirror_root,
            semester=semester,
            build_dir=assignment_path,
        )
        for substitution in substitutions:
            if substitution.placeholder:
                # Nothing to copy: the file exists nowhere. Say so in the
                # document rather than letting the build die over a question
                # nobody can recover.
                if not substitution.destination.exists():
                    write_gap_note(substitution.destination, substitution.wanted)
                continue
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
        placed.extend(substitutions)
    return placed


def _in_corpus(path: Path, mirror_root: Path, corpus_root: Path) -> Path:
    """A mirrored path expressed under the corpus, when it has an original."""
    try:
        return corpus_root / path.resolve().relative_to(mirror_root.resolve())
    except ValueError:
        return path


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
    #: Tagging-incompatible constructs rewritten in the mirror, rule -> sites.
    #: Not an accessibility number: these are LaTeX constructs that pdfLaTeX
    #: accepts and tagging does not, fixed so the document builds at all.
    rewrites: dict[str, int] = field(default_factory=dict)
    #: Corpus files this run overwrote, in `edit` mode. Empty everywhere else,
    #: and the honest answer to "what did it touch" when someone asks later.
    edited: list[str] = field(default_factory=list)
    #: Fraction of strongly-differing pixels vs the untouched original, 0..1.
    pixel_diff: float | None = None
    #: Set when the comparison could not be made, with the reason.
    diff_note: str | None = None
    injected: list[str] = field(default_factory=list)
    note: str | None = None
    #: Includes the corpus could not resolve, and what stood in for them.
    substitutions: list = field(default_factory=list)
    #: Errors the UNTOUCHED source produces. See :attr:`inherited`.
    baseline_errors: list[str] = field(default_factory=list)

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
    def inherited(self) -> bool:
        """True when the untouched source fails too: this tool did not break it.

        A decade-old exam whose era's `ee16.sty` no longer defines the macro it
        calls fails identically before and after conversion. Reporting that as a
        conversion failure sends someone hunting through the injected preamble
        for a bug that is not there, and -- worse -- hides the failures that
        *are* this tool's, in a list too long to read.
        """
        return bool(self.baseline_errors)

    @property
    def regression(self) -> list[str]:
        """Errors that appeared only after conversion, the ones worth chasing.

        Compared as messages with the line numbers stripped, because injecting a
        preamble shifts every line below it and an unchanged error would
        otherwise read as a new one.
        """
        before = {_error_shape(item) for item in self.baseline_errors}
        return [item for item in self.errors if _error_shape(item) not in before]

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
            "rewrites": self.rewrites,
            "pixel_diff": self.pixel_diff,
            "diff_note": self.diff_note,
            "injected": self.injected,
            "note": self.note,
            "inherited": self.inherited,
            "regression": self.regression,
        }


def _error_shape(message: str) -> str:
    """One error message reduced to what makes it the *same* error.

    ``body.tex:19: Missing number`` and ``body.tex:26: Missing number`` are one
    defect seen through a preamble that pushed everything seven lines down.
    Dropping the line number is what lets :attr:`BuildReport.regression` tell an
    inherited failure from one this conversion introduced.
    """
    return re.sub(r":\d+:", ":", message.strip())


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

    # `with`, because three of the four exits below are early returns and the
    # documents used to leak on every one of them. Serially that is a handful of
    # mmaps; at `--jobs 8` over a semester it is hundreds.
    try:
        left = pymupdf.open(original)
    except Exception as exc:  # pragma: no cover
        return None, f"unreadable: {exc}"
    with left:
        try:
            right = pymupdf.open(converted)
        except Exception as exc:  # pragma: no cover
            return None, f"unreadable: {exc}"
        with right:
            if left.page_count != right.page_count:
                return None, f"{left.page_count} vs {right.page_count} pages"

            differing = total = 0
            for index in range(left.page_count):
                a = left[index].get_pixmap(dpi=_DIFF_DPI, colorspace=pymupdf.csGRAY).samples
                b = right[index].get_pixmap(dpi=_DIFF_DPI, colorspace=pymupdf.csGRAY).samples
                if len(a) != len(b):
                    return None, "page sizes differ"
                # ponytail: GIL-bound pixel loop, ~1.1 MB a page. It is the one
                # thing `--jobs` cannot overlap; move the compare into its own
                # serial phase after the compiles if -j stops scaling.
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

    # Before the first byte reaches the corpus, and that is the whole point of
    # the position: `apply_descriptions` below writes the worklog beside the
    # sources in `edit` mode, so a guard placed after it would be checking a
    # tree this run had already dirtied. `build_run` checks once up front too,
    # so a multi-document run cannot get half way; this one is for the callers
    # that reach here directly -- the agent API and the tests both do.
    if config.output.in_place:
        require_clean_worktree(profile.corpus.root.resolve())

    # Approved descriptions become real /Alt HERE, in the mirror `materialise`
    # just wrote -- never in the corpus. Without this step a run scans figures,
    # writes worklogs, and then builds a PDF whose figures still carry
    # latex-lab's default alt: the source file name, read aloud verbatim.
    # Before the descriptions: a document that cannot compile has no figures to
    # describe, and three of the four constructs below produce no PDF at all.
    report.rewrites = rewrite_incompatibilities(prepared, mode=probe(profile).tagging_mode)
    report.described = apply_descriptions(prepared, config, profile)
    return _compile_assignment(prepared, report, assignment, config, profile, variant, compare)


def _compile_assignment(
    prepared: Prepared,
    report: BuildReport,
    assignment: Assignment,
    config: RunConfig,
    profile: Profile,
    variant: str,
    compare: bool,
) -> BuildReport:
    """Everything from the first LaTeX run onward.

    Split out of :func:`build_assignment` so :func:`build_run` can run this half
    concurrently while the half above it -- which writes into a mirror
    directory shared by an assignment's variants -- stays serial. The split is
    where the shared state ends: from here down every path is keyed by
    ``jobname``, which is unique per assignment *and* variant.
    """
    slug = base_slug(assignment.path, variant)
    # `in-place` is a destination for the PDF, not a licence to edit the source.
    # It used to rewrite the corpus driver, guarded by a clean git worktree; the
    # conversion is now always mirrored and the only thing that reaches the
    # corpus is the finished document, beside the original it was built from.
    if config.output.in_place:
        # The guard for a direct caller used to live here. It cannot: by this
        # point `apply_descriptions` has written the worklog beside the sources
        # -- in `edit` mode that is inside the corpus -- so the guard tripped
        # over this run's own file and refused every build. It now runs in
        # `build_assignment`, before anything is written anywhere.
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
        # What the source does on its own, before a single line was injected.
        # Without this a 2015 exam that has not compiled since 2015 reads
        # exactly like a document this tool broke, and there is no way to tell
        # a regression from an inheritance.
        report.baseline_errors, _ = _log_findings(
            config.output.baseline_dir() / f"{slug}-original.log"
        )

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
    # Only now, and only if it built. `edit` overwrites course material, and
    # material overwritten with sources that do not compile is the worst thing
    # this tool could do to a repository it was pointed at.
    if config.write and config.output.edits_sources and report.ok:
        report.edited = [str(path) for path in copy_back(prepared, config, profile)]
    return report


def copy_back(prepared: Prepared, config: RunConfig, profile: Profile) -> list[Path]:
    r"""Write the mirror's converted sources back over the corpus originals.

    Only ``edit`` mode reaches here, and only after the document has compiled.
    Overwriting course material with sources that do not build would be the
    worst possible failure mode of this tool, so the successful PDF is the
    precondition.

    Two rules decide what comes back, and both are about what must NOT:

    1. **A file must already exist in the corpus.** The mirror holds more than
       converted originals -- ``repair_missing`` copies stand-in questions from
       other semesters to resolve dangling includes, and
       ``repair.write_gap_note`` writes stubs for the ones it cannot resolve.
       Those exist so a historical assignment can be *built*; writing them into
       the course repository would be this tool inventing course material.
    2. **Never the baseline.** ``<stem>-original.tex`` is the untouched copy the
       pixel diff measures against. It is an artefact of the comparison, not
       output, and it would land beside the real driver as a duplicate.

    The package's own ``.sty`` files are then copied in beside the driver. The
    mirror never needed them on disk -- it reached them through ``TEXINPUTS``
    (see :func:`materialise`) -- but the whole point of this mode is a folder
    that builds with a bare ``pdflatex``, and a bare ``pdflatex`` has no such
    path. They are named ``latexally-*``, which is what lets
    :mod:`latexally.revert` recognise them again.
    """
    mirror_root = config.output.tex_dir().resolve()
    corpus_root = profile.corpus.root.resolve()
    written: list[Path] = []

    for source in sorted(prepared.work_dir.rglob("*")):
        if not source.is_file() or source.name.endswith(f"-{ORIGINAL_SUFFIX}.tex"):
            continue
        try:
            relative = source.resolve().relative_to(mirror_root)
        except ValueError:
            continue
        target = corpus_root / relative
        if not target.is_file():
            continue  # rule 1: repaired stand-ins and gap notes stay in the mirror
        if target.read_bytes() == source.read_bytes():
            continue
        target.write_bytes(source.read_bytes())
        written.append(target)

    written.extend(_install_packages(prepared.work_dir, corpus_root, prepared))
    return written


def _install_packages(
    work_dir: Path, corpus_root: Path, prepared: Prepared
) -> list[Path]:
    """Put the ``latexally-*.sty`` the driver loads beside it, for bare pdflatex."""
    if not PACKAGE_TEX_DIR.is_dir():
        return []
    target_dir = (corpus_root / prepared.assignment.path).resolve()
    wanted = {
        Path(name).stem
        for name in _package_names(prepared.driver)
    }
    installed: list[Path] = []
    for package in sorted(PACKAGE_TEX_DIR.iterdir()):
        if package.suffix.lower() not in (".sty", ".cls") or package.stem not in wanted:
            continue
        target = target_dir / package.name
        if target.is_file() and target.read_bytes() == package.read_bytes():
            continue
        shutil.copy2(package, target)
        installed.append(target)
    return installed


def _package_names(driver: Path) -> set[str]:
    r"""Every ``latexally-*`` package the converted driver loads, transitively.

    Read from the file rather than derived from the run's toggles: the preamble
    the conversion injects is the authority on what the document needs, and a
    package that loads another (``latexally-ee16`` requires ``latexally-core``)
    must bring it along or the bare build fails on the second file.
    """
    seen: set[str] = set()
    frontier = [driver]
    pattern = re.compile(r"\\(?:usepackage|RequirePackage|documentclass)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
    while frontier:
        current = frontier.pop()
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(text):
            for name in match.group(1).split(","):
                name = name.strip()
                if not name.startswith("latexally") or name in seen:
                    continue
                seen.add(name)
                for suffix in (".sty", ".cls"):
                    candidate = PACKAGE_TEX_DIR / f"{name}{suffix}"
                    if candidate.is_file():
                        frontier.append(candidate)
    return seen


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
        # Scanned from the corpus here, so the corpus is both source root and
        # destination -- and the worklog this reports is the same file the
        # build's own scan will refresh later.
        beside=profile.corpus.root.resolve() if config.output.edits_sources else None,
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
    # One run-level line, not a column: this is bookkeeping about the *source*,
    # not about any one document's accessibility, and the table above already
    # carries nine columns.
    fixed: dict[str, int] = {}
    for report in reports:
        for rule, sites in report.rewrites.items():
            fixed[rule] = fixed.get(rule, 0) + sites
    if fixed:
        lines += [
            "",
            "auto-fixed in the mirror, corpus unchanged: "
            + ", ".join(f"{rule} x{sites}" for rule, sites in sorted(fixed.items())),
        ]

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

    # The clean-worktree guard used to fire per document, which let documents
    # 1..N-1 build before N discovered the corpus was dirty. It is a property of
    # the run, so it is checked once, before anything is written.
    if config.write and config.output.in_place:
        require_clean_worktree(profile.corpus.root.resolve())

    reports: list[BuildReport] = []
    work: list[tuple[Assignment, str, str, frozenset[str]]] = []
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
            work.append((assignment, variant, driver, converted))

    def build_one(item: tuple[Assignment, str, str, frozenset[str]]) -> BuildReport:
        assignment, variant, driver, converted = item
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
        if on_finish:
            on_finish(report)
        return report

    # `map`, never `as_completed`: combine_logs, write_report and the CLI's
    # report table all read `reports` positionally, and build-log.txt is an
    # artefact people diff between runs. Threads rather than processes because
    # the work is `subprocess.run` on latexmk, which holds the GIL for none of
    # its runtime -- and because the TUI's callbacks close over a Textual
    # screen, which cannot be pickled.
    if config.jobs > 1 and len(work) > 1:
        with ThreadPoolExecutor(max_workers=config.jobs) as pool:
            reports.extend(pool.map(build_one, work))
    else:
        reports.extend(build_one(item) for item in work)
    combine_logs(config, reports)
    write_report(config, reports)
    return reports

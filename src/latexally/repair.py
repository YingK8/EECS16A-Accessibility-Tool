r"""Find the question a historical assignment meant, when the bank has moved on.

Most of this corpus does not build. 3,175 ``\input`` targets across 1,109 files
do not exist: an assignment from fa17 says
``\input{../../../questionBank/hw/9/q_headphones_2}`` -- the *live* shared bank
-- for a question that has since been retired from it and now survives only in
the frozen per-semester snapshots (``fa19_questionBank/``, ``fa23_questionBank/``
and so on). The assignment compiled the year it was set and has been dead ever
since; nothing noticed, because nobody rebuilds old homeworks until a conversion
tool does.

The file is almost always still there under a different roof, so this finds it:

    1,521  the assignment's own semester snapshot has it -- no choice to make
      594  several snapshots have it and they are byte-identical -- no choice
            *matters*
      875  several snapshots have it and they DIFFER
      208  not a bank reference at all
        8  nowhere in the corpus

The 875 are the whole reason this module is careful. Substituting the wrong one
puts a *different question* into a document that claims to be a faithful
conversion of the original -- a worse outcome than the build failing. So the
order is "nearest earlier semester": the assignment's own snapshot, then
backwards in time, then forwards, and only then today's live bank. That is the
question as it most likely stood when the assignment was set. Every substitution
is recorded on the report, and one drawn from a set of *differing* candidates is
marked :attr:`Substitution.ambiguous` so the document it lands in can be flagged
rather than passed off as clean.

Nothing here writes to the corpus. The replacement is copied into the output
mirror at the path the source asked for, so the original stays exactly as it is
and the substitution is visible as a file you can diff.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = [
    "Substitution",
    "assets_beside",
    "bank_search_order",
    "find_replacements",
    "unresolved_graphics",
    "unresolved_listings",
    "unresolved_packages",
    "unresolved_references",
]

#: ``sp``/``su``/``fa`` in the order a year runs, so semesters sort.
_SEASONS = {"sp": 0, "su": 1, "fa": 2}

#: A directory that is a question bank: ``questionBank`` itself, or a frozen
#: snapshot of it named for the semester it was frozen in.
_BANK = re.compile(r"^(?:([a-z]{2})(\d{2})_)?questionBank$")

#: The same, as written in an ``\input`` -- authors have used names that were
#: never real directories (``fall19_questionBank``), so this is deliberately
#: looser than :data:`_BANK`.
_BANK_REFERENCE = re.compile(r"^(?:[a-z]+\d{2}_)?questionBank$", re.IGNORECASE)


def _rank(semester: str) -> tuple[int, int] | None:
    """``fa19`` -> a sortable ``(year, season)``, or ``None`` if unparseable."""
    match = re.fullmatch(r"([a-z]{2})(\d{2})", semester)
    if not match or match.group(1) not in _SEASONS:
        return None
    return int(match.group(2)), _SEASONS[match.group(1)]


def bank_search_order(corpus_root: Path, semester: str) -> list[Path]:
    """Question banks to search, nearest-earlier first.

    Own semester, then backwards in time, then forwards, then the live bank.
    Backwards before forwards because a question is more likely to have been
    edited after an assignment used it than before; the live bank is last
    because it is the furthest thing from "how it read at the time".
    """
    here = _rank(semester)
    snapshots: list[tuple[tuple[int, int], Path]] = []
    live: list[Path] = []
    for entry in sorted(corpus_root.iterdir()):
        if not entry.is_dir():
            continue
        match = _BANK.match(entry.name)
        if not match:
            continue
        if match.group(1) is None:
            live.append(entry)
            continue
        rank = _rank(f"{match.group(1)}{match.group(2)}")
        if rank is not None:
            snapshots.append((rank, entry))

    if here is None:
        # An assignment whose directory is not a semester at all. Oldest first
        # is the least surprising order left.
        return [path for _, path in sorted(snapshots)] + live

    own = [path for rank, path in snapshots if rank == here]
    earlier = [path for rank, path in sorted(snapshots, reverse=True) if rank < here]
    later = [path for rank, path in sorted(snapshots) if rank > here]
    return own + earlier + later + live


@dataclass(slots=True)
class Substitution:
    """One missing include, and the file standing in for it."""

    #: The path exactly as the source wrote it.
    wanted: str
    #: The file that referenced it.
    referenced_by: Path
    #: The replacement, somewhere else in the corpus.
    used: Path
    #: Where it has to be placed for the source to find it unchanged.
    destination: Path
    #: A second location, when the path as written and the path with its
    #: whitespace squeezed out are not the same file.
    alias: Path | None = None
    #: True when the candidates were not all the same file. The document this
    #: lands in is not a clean conversion of anything and must not be called one.
    ambiguous: bool = False
    #: Every bank that had a copy, in the order they were searched.
    candidates: list[Path] = field(default_factory=list)
    #: True when nothing stood in because nothing exists: `used` is a generated
    #: note saying the file is gone, not a copy of the question. See
    #: :func:`write_gap_note`.
    placeholder: bool = False

    @property
    def fix(self) -> str:
        """What a person should do to the corpus so this is not needed again."""
        if self.placeholder:
            return (
                f"{self.wanted} (referenced by {self.referenced_by}) exists "
                "nowhere in the corpus; restore it from a backup, or edit the "
                "source to stop asking for it"
            )
        return (
            f"copy {self.used} to {self.wanted} "
            f"(as resolved from {self.referenced_by}), "
            "or point the \\input at the file that still exists"
        )

    def as_dict(self) -> dict:
        return {
            "wanted": self.wanted,
            "referenced_by": str(self.referenced_by),
            "used": str(self.used),
            "ambiguous": self.ambiguous,
            "placeholder": self.placeholder,
            "candidates": [str(path) for path in self.candidates],
        }


def _tail_after_bank(target: str) -> Path | None:
    """``../../../fa19_questionBank/hw/6/q_x`` -> ``hw/6/q_x`` (no suffix added).

    Components are stripped, because the corpus contains
    ``../../../fall19_questionBank /hw/6/q_mech_circuits1`` -- a space inside
    the path. TeX takes it literally and cannot find the file either, so the
    typo is one of the things this is repairing.
    """
    parts = [
        part.strip()
        for part in Path(target.strip()).parts
        if part.strip() not in ("..", ".", "")
    ]
    for index, part in enumerate(parts):
        if _BANK_REFERENCE.match(part):
            return Path(*parts[index + 1 :]) if index + 1 < len(parts) else None
    return None


@lru_cache(maxsize=4)
def _name_index(corpus_root: Path) -> dict[str, tuple[Path, ...]]:
    """``{basename: every path in the corpus with that name}``, sorted.

    One walk of a 17,600-file corpus, reused by every lookup. The alternative --
    an ``rglob`` per missing reference -- is the same walk several thousand
    times over, which turned a whole-corpus sweep from seconds into an hour.
    Sorted so the fallbacks below break ties the same way on every machine.
    """
    index: dict[str, list[Path]] = {}
    for path in corpus_root.rglob("*"):
        if path.is_file():
            index.setdefault(path.name, []).append(path)
    return {name: tuple(sorted(paths)) for name, paths in index.items()}


def _distance(source: Path, candidate: Path) -> tuple[int, int]:
    """``(hops up, hops down)`` between the directories of two files.

    The metric a person uses when they read ``\\usepackage{../ee16}`` in
    ``exams/fa15/mt1/`` and go looking for the file: climb until the paths meet,
    then descend. ``exams/ee16.sty`` is two up and none down; the corpus-root
    copy is three up; ``notes/ee16.sty`` is three up and one down. Nearest is
    the one the author most plausibly meant, and it is the one that shares the
    most history with the file asking for it.
    """
    here = source.parent.resolve().parts
    there = candidate.parent.resolve().parts
    shared = 0
    for left, right in zip(here, there):
        if left != right:
            break
        shared += 1
    return len(here) - shared, len(there) - shared


def _nearest_by_name(
    source: Path, corpus_root: Path, name: str
) -> tuple[Path | None, list[Path]]:
    """The copy of ``name`` nearest ``source``, and the copies tied with it.

    This replaced a "use it only if the whole corpus holds exactly one copy"
    rule. ``ee16.sty`` exists eleven times here and no two are identical, so
    uniqueness always answered no and always gave up -- leaving every pre-2017
    exam dead on ``File `../ee16.sty' not found``. Distance answers instead: an
    exam reaching for ``../ee16`` gets the copy in ``exams/``, not the one under
    ``notes/``.

    The second return value is only the candidates at that *same* distance --
    the ones the metric genuinely cannot separate. A caller flags a
    substitution ambiguous when those disagree in content; farther copies are
    not evidence of ambiguity, they are just farther away.
    """
    hits = _name_index(corpus_root).get(name, ())
    if not hits:
        return None, []
    ranked = sorted(hits, key=lambda path: (_distance(source, path), path))
    nearest = _distance(source, ranked[0])
    return ranked[0], [p for p in ranked if _distance(source, p) == nearest]


#: ``\\usepackage{../../../fa19_questionBank/hw/7/kbordermatrix}`` -- a package
#: loaded by path rather than by name. The include graph does not follow these,
#: so they went unseen until the build stopped on one.
_PACKAGE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*/[^}]*)\}"
)


def unresolved_packages(source: Path, *, cwd: Path | None = None) -> list[str]:
    r"""Path-qualified packages this file loads that are not on disk.

    Two things stop this from crying wolf on almost every file in the corpus.

    **Only path-qualified names are ours to find.** ``\usepackage{../ee16,
    graphicx, latexsym, epsf}`` is one match with four arguments, and three of
    them live in the TeX distribution. Checking the whole comma list against the
    filesystem reported ``graphicx`` missing 236 times.

    **Relative means relative to the build directory, not to this file.** TeX
    resolves ``../../../markup`` against the current directory -- the directory
    pdflatex was invoked in, which is the driver's -- and a shared preamble one
    level above the assignment counts its ``../`` hops from there, not from
    itself. Checking against ``source.parent`` reported ``markup``,
    ``timestamp`` and ``ee16`` missing 1,359 times each; they were never
    missing, and every one of those would have had a stand-in copied over it.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    bases = [source.parent] if cwd is None else [cwd, source.parent]
    missing = []
    for match in _PACKAGE.finditer(text):
        for argument in match.group(1).split(","):
            target = argument.strip()
            if not target or "/" not in target:
                continue
            candidate = Path(target if Path(target).suffix else f"{target}.sty")
            if not any((base / candidate).exists() for base in bases):
                missing.append(target)
    return missing


#: ``\\includegraphics[...]{path}``. A question carries its figures beside it,
#: and a stand-in copied without them builds with "using draft setting" and a
#: blank box where the circuit should be -- a quieter loss than a failed build
#: and a worse one, because the PDF looks finished.
_GRAPHIC = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")

#: What TeX will try when the source names a figure without an extension.
_GRAPHIC_SUFFIXES = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps")

#: ``\\lstinputlisting[...]{path}``: a source file typeset as a code block.
#: Always written with its extension, and always fatal when missing.
_LISTING = re.compile(r"\\lstinputlisting\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")

#: Extensions to try when a reference is written without one, in the order that
#: makes the common case right: `\\input{q_x}` wants q_x.tex, a path-qualified
#: `\\usepackage` wants .sty, and only then is it a figure. Getting this order
#: wrong puts a PNG where TeX expects a source file, which fails identically to
#: the missing file it replaced.
_SEARCH_SUFFIXES = ("", ".tex", ".sty", ".pdf", ".png", ".jpg", ".jpeg", ".eps")


#: Extensions a gap note may have to be written in, and each language's line
#: comment. A `\\lstinputlisting` target is typeset verbatim, so a LaTeX note in
#: a `.py` file would be printed as Python source rather than read as a message.
_CODE_SUFFIXES = {
    ".py": "#", ".m": "%", ".c": "//", ".cpp": "//", ".h": "//",
    ".java": "//", ".js": "//", ".sh": "#", ".txt": "#",
}

#: TeX-special characters, so a path can be printed inside a document.
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "#": r"\#", "^": r"\textasciicircum{}", "_": r"\_", "%": r"\%",
    "~": r"\textasciitilde{}",
}


def write_gap_note(destination: Path, wanted: str) -> None:
    r"""Write a visible note where a question used to be.

    Some of what this corpus asks for is simply gone -- not moved, not renamed,
    not in any snapshot: 75 targets across 68 documents exist nowhere. There are
    three things to do with those and only one of them is defensible.

    Substituting the nearest-looking file would put a *different question* into
    a document that claims to be a faithful conversion. Letting the build die
    leaves 68 documents with no accessible version at all, over a question
    nobody can recover anyway. So the document is built with the gap **stated**:
    a reader is told what is missing, in the place it is missing from, in the
    document's own reading order.

    Written into the output mirror only. The corpus keeps its broken reference,
    and the run report names every one of these so it reads as a corpus repair
    to make rather than a conversion that succeeded.
    """
    wanted = wanted.strip()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() in _CODE_SUFFIXES:
        # `\lstinputlisting` typesets the file's bytes verbatim, so the note has
        # to be a comment in THAT language or it lands in the PDF as a syntax
        # error the reader has to decode.
        comment = _CODE_SUFFIXES[destination.suffix.lower()]
        body = "\n".join(
            f"{comment} {line}"
            for line in (
                "Generated by latexally.",
                f"The corpus has no copy of {wanted}, in any semester snapshot",
                "or anywhere else, so there was nothing to stand in for it.",
            )
        ) + "\n"
    else:
        shown = "".join(_TEX_ESCAPES.get(char, char) for char in wanted)
        body = (
            "% Generated by latexally. The corpus has no copy of this file, in any\n"
            "% semester snapshot or anywhere else, so there was nothing to stand in\n"
            "% for it. The gap is stated rather than hidden. Nothing in the corpus\n"
            "% was changed; delete the output directory and this file goes with it.\n"
            "\\par\\noindent\\textbf{[Missing from the question bank: " + shown + "]}\\par\n"
            "\\noindent This question is referenced by the assignment but no copy of\n"
            "it survives in the corpus, so it could not be included.\\par\n"
        )
    destination.write_text(
        body,
        encoding="utf-8",
    )


def _unresolved_by_pattern(
    source: Path,
    pattern: re.Pattern[str],
    suffixes: tuple[str, ...],
    cwd: Path | None,
) -> list[str]:
    """Targets named by ``pattern`` in ``source`` that are not on disk.

    @param pattern: one capturing group holding the path as written
    @param suffixes: extensions TeX will try when none is written; ``""`` first
    @param cwd: the build directory, searched before the referencing file's own
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    bases = [source.parent] if cwd is None else [cwd, source.parent]
    missing = []
    for match in pattern.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        if any(
            (base / f"{target}{suffix}").is_file()
            for base in bases
            for suffix in suffixes
        ):
            continue
        missing.append(target)
    return missing


def unresolved_graphics(source: Path, *, cwd: Path | None = None) -> list[str]:
    r"""Figures this file draws that are not on disk.

    A missing figure does not stop pdflatex. It prints "using draft setting",
    leaves an empty rectangle where the circuit was, and produces a PDF that
    looks finished -- the quietest way for this pipeline to ship a page with the
    content removed, and one that no amount of tagging can make accessible.
    So a missing figure goes through the same stand-in search as a missing
    question, and the same report says which document got one.

    Resolved against the build directory as well as the referencing file, for
    the reason :func:`unresolved_packages` gives.
    """
    return _unresolved_by_pattern(source, _GRAPHIC, _GRAPHIC_SUFFIXES, cwd)


def unresolved_listings(source: Path, *, cwd: Path | None = None) -> list[str]:
    r"""Code files this document typesets with ``listings`` and cannot find.

    Fatal, unlike a missing figure: ``Package Listings Error: File `x(.py)' not
    found`` stops the build. The corpus does this for Python solutions kept
    beside a question, and when the question is substituted in from another
    semester the ``.py`` beside it has to come too.

    The path is written in full, extension included, so no suffix is guessed.
    """
    return _unresolved_by_pattern(source, _LISTING, ("",), cwd)


def unresolved_references(driver: Path, corpus_root: Path) -> list[tuple[Path, str]]:
    r"""Every ``(referencing file, target as written)`` this document cannot find.

    Walks the whole ``\input`` graph, not just the driver, because the reference
    that stops a build is almost never in the file you handed pdflatex --
    ``body.tex`` is what reaches the retired bank question three directories
    away. Path-qualified ``\usepackage`` is collected alongside, since the
    include graph does not follow package loads and a missing one stopped the
    build with nothing to point at.

    Read-only, and shared by the build (which then repairs) and the corpus sweep
    (which then counts). One answer, so a green sweep means a build that finds
    its files, rather than two implementations that agree by luck.

    The driver's own directory leads the search roots because that is pdflatex's
    current directory, and a ``../``-spelled path resolves against it -- not
    against whichever included file happens to spell it. A shared preamble one
    level above the assignment relies on exactly that.
    """
    from .texlex.includes import IncludeGraph

    build_dir = driver.parent.resolve()
    graph = IncludeGraph([build_dir, corpus_root])
    resolved, _ = graph.transitive_inputs(driver)
    unresolved: list[tuple[Path, str]] = []
    for source in [driver, *resolved]:
        try:
            _, missing = graph.direct_inputs(source)
        except OSError:
            continue
        unresolved.extend((source, target) for target in missing)
        unresolved.extend(
            (source, target) for target in unresolved_packages(source, cwd=build_dir)
        )
        unresolved.extend(
            (source, target) for target in unresolved_graphics(source, cwd=build_dir)
        )
        unresolved.extend(
            (source, target) for target in unresolved_listings(source, cwd=build_dir)
        )
    return unresolved


def assets_beside(source: Path, destination: Path) -> list[tuple[Path, Path]]:
    """``(from, to)`` for every figure ``source`` references.

    Offsets are preserved against ``destination``, the same rule the rest of
    the mirror follows: a figure written as ``fig/x.png`` next to the original
    has to be ``fig/x.png`` next to the copy.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pairs: list[tuple[Path, Path]] = []
    for match in _GRAPHIC.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        for suffix in _GRAPHIC_SUFFIXES:
            candidate = source.parent / f"{target}{suffix}"
            if candidate.is_file():
                offset = os.path.relpath(candidate, source.parent)
                pairs.append(
                    (candidate, Path(os.path.normpath(destination.parent / offset)))
                )
                break
    return pairs


def find_replacements(
    unresolved: list[tuple[Path, str]],
    *,
    corpus_root: Path,
    mirror_root: Path,
    semester: str,
    build_dir: str,
) -> list[Substitution]:
    r"""Locate a stand-in for each ``(referencing file, target as written)``.

    ``destination`` is where the file has to be placed for the source to find it
    unedited, and that is an offset from the **build directory** -- the mirrored
    assignment folder pdflatex is invoked in -- not from the referencing file.
    TeX resolves a relative path against the current directory, so a preamble
    one level up, or a bank question already substituted in from two directories
    sideways, both count their ``../`` hops from the assignment being built.

    Measuring from the referencing file instead sent every such destination
    climbing out of the mirror, where a guard below correctly refused to write
    it -- and 44 documents stayed broken because the stand-in was found and then
    thrown away.
    """
    banks = bank_search_order(corpus_root, semester)
    build_mirror = (mirror_root / build_dir).resolve()
    found: list[Substitution] = []
    for source, target in unresolved:
        tail = _tail_after_bank(target)
        # An extensionless target may be either. `\input{q_x}` wants q_x.tex;
        # `\usepackage{.../kbordermatrix}` wants kbordermatrix.sty, and forcing
        # .tex on both left every path-qualified package unresolvable.
        names = (
            [tail]
            if tail and tail.suffix
            else ([tail.with_suffix(".tex"), tail.with_suffix(".sty")] if tail else [])
        )
        candidates = [
            bank / name
            for name in names
            for bank in banks
            if (bank / name).is_file()
        ]
        used = candidates[0] if candidates else None
        if used is None:
            # Not a bank reference, or the banks do not have it. Fall back to
            # the file's own neighbourhood: `\usepackage{../ee16}` in
            # exams/fa15/mt1 means the ee16.sty that era shipped, which is in
            # exams/, not the one at the corpus root and not the one under
            # notes/. Distance decides; the whole-corpus uniqueness test is the
            # rung after, for names too rare to have a near copy.
            stem = Path(target.strip().rstrip("/")).name
            for suffix in _SEARCH_SUFFIXES:
                if suffix and Path(stem).suffix:
                    break
                used, candidates = _nearest_by_name(
                    source, corpus_root, f"{stem}{suffix}"
                )
                if used is not None:
                    break

        wanted = target.strip()
        if used is None:
            # Nothing anywhere. A missing figure is not fatal -- pdflatex draws
            # an empty box and carries on -- so it is reported and left alone.
            # A missing `\input` stops the build dead, and the document is worth
            # more with the gap stated than not existing at all.
            if Path(wanted).suffix.lower() in _GRAPHIC_SUFFIXES[1:]:
                continue
            # The extension the source wrote, because the reader of this file
            # is whatever macro asked for it: `\\input` wants LaTeX,
            # `\\lstinputlisting{x.py}` wants something Python can be shown as.
            gap = Path(
                os.path.normpath(
                    build_mirror / (wanted if Path(wanted).suffix else f"{wanted}.tex")
                )
            )
            if not gap.is_relative_to(mirror_root):
                continue
            # Recorded, not written. This function only ever decides; every
            # write is done by the caller, which is what lets the corpus sweep
            # ask "what would happen" against a mirror root that does not exist.
            found.append(
                Substitution(
                    wanted=wanted,
                    referenced_by=source,
                    used=gap,
                    destination=gap,
                    placeholder=True,
                )
            )
            continue

        # The suffix is whatever was actually found: forcing .tex put a .sty
        # file at a .tex name, which TeX could not load either.
        offset = wanted if Path(wanted).suffix else f"{wanted}{used.suffix}"
        destination = Path(os.path.normpath(build_mirror / offset))

        # `fall19_questionBank /hw/6/...` carries a space the author typed.
        # \input keeps it and looks for the spaced path; \usepackage strips it
        # and looks for the bare one. Rather than model which macro asked,
        # write both -- they are a few kilobytes and only differ where the
        # corpus has the typo.
        squeezed = "/".join(part.strip() for part in offset.split("/") if part.strip())
        alias = Path(os.path.normpath(build_mirror / squeezed))
        if alias == destination or not alias.is_relative_to(mirror_root):
            alias = None
        if not destination.is_relative_to(mirror_root):
            # The same guard mirror_dependencies uses: an assignment shallow
            # enough for its ../ hops to climb past the output root must not
            # have this tool writing copies into whatever sits above it.
            continue

        # Ambiguous means "a choice was made and it could have gone the other
        # way". The assignment's own semester is authoritative, so taking it is
        # not a guess however much the other banks disagree.
        own_bank = corpus_root / f"{semester}_questionBank"
        authoritative = used.is_relative_to(own_bank) if own_bank.is_dir() else False
        digests = {
            hashlib.md5(path.read_bytes()).hexdigest() for path in candidates
        }
        found.append(
            Substitution(
                wanted=wanted,
                referenced_by=source,
                used=used,
                destination=destination,
                alias=alias,
                ambiguous=len(digests) > 1 and not authoritative,
                candidates=candidates,
            )
        )
    return found

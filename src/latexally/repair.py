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
from pathlib import Path

__all__ = [
    "Substitution",
    "assets_beside",
    "bank_search_order",
    "find_replacements",
    "unresolved_packages",
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

    @property
    def fix(self) -> str:
        """What a person should do to the corpus so this is not needed again."""
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


def _unique_by_name(corpus_root: Path, name: str) -> Path | None:
    """Last resort: exactly one file of that name in the whole corpus.

    Only when it is unique. Nearly every question file exists in a dozen
    snapshots, so this fires for the stragglers -- a stylesheet at the corpus
    root, a figure moved one directory sideways -- and never for a question.
    """
    hits = [path for path in corpus_root.rglob(name) if path.is_file()]
    if not hits:
        return None
    # Several copies are fine when they are the same file. `kbordermatrix.sty`
    # sits in thirteen snapshots and is byte-identical in all of them; refusing
    # it on a count alone would leave a build broken over nothing.
    digests = {hashlib.md5(path.read_bytes()).hexdigest() for path in hits}
    return hits[0] if len(digests) == 1 else None


#: ``\\usepackage{../../../fa19_questionBank/hw/7/kbordermatrix}`` -- a package
#: loaded by path rather than by name. The include graph does not follow these,
#: so they went unseen until the build stopped on one.
_PACKAGE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*/[^}]*)\}"
)


def unresolved_packages(source: Path) -> list[str]:
    """Path-qualified packages this file loads that are not on disk."""
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    missing = []
    for match in _PACKAGE.finditer(text):
        for argument in match.group(1).split(","):
            target = argument.strip()
            if not target:
                continue
            candidate = Path(target if Path(target).suffix else f"{target}.sty")
            if not (source.parent / candidate).exists():
                missing.append(target)
    return missing


#: ``\\includegraphics[...]{path}``. A question carries its figures beside it,
#: and a stand-in copied without them builds with "using draft setting" and a
#: blank box where the circuit should be -- a quieter loss than a failed build
#: and a worse one, because the PDF looks finished.
_GRAPHIC = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")

#: What TeX will try when the source names a figure without an extension.
_GRAPHIC_SUFFIXES = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps")


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
) -> list[Substitution]:
    """Locate a stand-in for each ``(referencing file, target as written)``.

    ``destination`` is computed in the mirror, at the same offset from the
    referencing file's mirrored copy as the target was from the original. Place
    the file there and the source resolves it without being edited.
    """
    banks = bank_search_order(corpus_root, semester)
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
            stem = Path(target.strip().rstrip("/")).name
            for suffix in ("", ".tex", ".sty"):
                if suffix and Path(stem).suffix:
                    break
                used = _unique_by_name(corpus_root, f"{stem}{suffix}")
                if used is not None:
                    break
            if used is None:
                continue

        try:
            mirrored = mirror_root / source.relative_to(corpus_root)
        except ValueError:
            continue
        wanted = target.strip()
        # The suffix is whatever was actually found: forcing .tex put a .sty
        # file at a .tex name, which TeX could not load either.
        offset = wanted if Path(wanted).suffix else f"{wanted}{used.suffix}"
        destination = Path(os.path.normpath(mirrored.parent / offset))

        # `fall19_questionBank /hw/6/...` carries a space the author typed.
        # \input keeps it and looks for the spaced path; \usepackage strips it
        # and looks for the bare one. Rather than model which macro asked,
        # write both -- they are a few kilobytes and only differ where the
        # corpus has the typo.
        squeezed = "/".join(part.strip() for part in offset.split("/") if part.strip())
        alias = Path(os.path.normpath(mirrored.parent / squeezed))
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

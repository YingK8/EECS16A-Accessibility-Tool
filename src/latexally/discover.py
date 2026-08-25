r"""Finding the documents a run will build.

Separated from :mod:`latexally.run`, which models *what a run does*; this
models *what it does it to*. The two met only through ``iter_selected``, and
having them in one file meant every change to a config field sat in the same
1,000 lines as the include-graph walk.

The hard-won part is that a directory is not a document and an assignment is
not one document: `sol9.tex` and `prob9.tex` pull in the same body and differ
only in how `\sol` is defined, and converting one leaves the other -- which
students actually receive -- untagged.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .config import Profile
from .errors import ConfigError
from .run import RunConfig
from .texlex.includes import is_driver

__all__ = [
    "Assignment",
    "newest_first",
    "VARIANTS",
    "VARIANT_LABELS",
    "discover_assignments",
    "find_driver",
    "find_drivers",
    "group_by_kind",
    "iter_selected",
]

# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class Assignment:
    """One compilable unit of course material."""

    #: Corpus-relative directory, e.g. ``sp26/hw/9``.
    path: str
    #: Profile-declared kind: homework, discussion, exam, note…
    kind: str
    #: Driver file name within that directory, e.g. ``sol9.tex``.
    driver: str | None
    tex_files: int = 0
    #: Every buildable variant, ``{"solution": "sol9.tex", "problem": "prob9.tex"}``.
    drivers: dict[str, str] = field(default_factory=dict)

    @property
    def buildable(self) -> bool:
        return self.driver is not None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def variants_for(self, wanted: Iterable[str] | None = None) -> dict[str, str]:
        """The variants to build, ``{variant: driver}``, in declared order.

        An empty or absent selection means *everything this assignment has* --
        the honest default, since a course ships both files and a student only
        ever sees the blank one.
        """
        available = self.drivers or ({"document": self.driver} if self.driver else {})
        wanted = tuple(wanted or ())
        if not wanted:
            return dict(available)
        chosen = {name: available[name] for name in wanted if name in available}
        # Never build nothing because a filter matched nothing: an assignment
        # with only an unconventional driver still has to convert.
        return chosen or (
            {"document": available["document"]} if "document" in available else {}
        )

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "driver": self.driver,
            "drivers": dict(self.drivers),
            "tex_files": self.tex_files,
            "buildable": self.buildable,
        }


#: The variants of one assignment, in the order a person thinks about them.
#: An EECS 16A assignment is not one document: `sol9.tex` and `prob9.tex` pull
#: in the SAME body and differ only in how `\sol` is defined -- printed in blue,
#: or swallowed. Discussions add `dis09A.tex` (student handout) and `ans09A.tex`
#: (answers only). Converting just one of them leaves the other, which students
#: actually receive, untagged.
VARIANT_LABELS: tuple[tuple[str, str], ...] = (
    ("solution", "with solutions"),
    ("problem", "blank, as students receive it"),
    ("answer", "answers only"),
)
VARIANTS: tuple[str, ...] = tuple(name for name, _ in VARIANT_LABELS)

#: Filename prefix -> variant. Overridable per course via ``corpus.variants``.
DEFAULT_VARIANT_PREFIXES: dict[str, str] = {
    "sol": "solution",
    "prob": "problem",
    "dis": "problem",
    "ans": "answer",
}


def find_drivers(
    directory: Path, prefixes: dict[str, str] | None = None
) -> dict[str, str]:
    """Every buildable variant in an assignment directory, ``{variant: file}``.

    A driver is a file pdflatex can compile as it stands -- see
    :func:`latexally.texlex.includes.is_driver`, which is the authority on what
    that means and on why ``\\begin{document}`` is the wrong test. The rest of an
    assignment is ``\\input`` fragments that do not compile alone.

    Variants are recognised by the naming convention the corpus uses --
    ``<prefix><name>.tex`` -- and anything compilable that matches no prefix is
    returned under ``document`` so it is still built rather than silently
    skipped. A directory with nothing compilable in it returns ``{}``.
    """
    prefixes = prefixes or DEFAULT_VARIANT_PREFIXES
    name = directory.name
    found: dict[str, str] = {}

    for prefix, variant in prefixes.items():
        if variant in found:
            continue
        # `<prefix><dirname>.tex` and a bare `<prefix>.tex` first, then any
        # `<prefix>*.tex`: sp26/hw/15 holds sol14.tex and prob14.tex, so the
        # directory is not always named after the files inside it.
        exact = (directory / f"{prefix}{name}.tex", directory / f"{prefix}.tex")
        for candidate in (*exact, *sorted(directory.glob(f"{prefix}*.tex"))):
            if candidate.is_file() and is_driver(candidate):
                found[variant] = candidate.name
                break

    if not found:
        # No convention match: fall back to whatever really compiles.
        # Sorted, so the choice is deterministic rather than filesystem-ordered.
        for path in sorted(directory.glob("*.tex")):
            if is_driver(path):
                found["document"] = path.name
                break
    return found


def find_driver(directory: Path, prefixes: dict[str, str] | None = None) -> str | None:
    """The single most representative driver: solutions if there is one."""
    found = find_drivers(directory, prefixes)
    for variant in (*VARIANTS, "document"):
        if variant in found:
            return found[variant]
    return None


def _kind_of(relative: str, kinds: dict[str, str]) -> str:
    """Classify by the profile's pattern map, longest pattern winning.

    Longest-first matters: ``sp26/hw`` must beat a bare ``hw`` when a profile
    declares both, otherwise the answer depends on dict order.

    Each segment of a pattern is a glob, so one ``notes*`` entry covers
    ``notes``, ``notes_fa24`` and the six other snapshots a decade of renames
    left behind, instead of eight profile lines that go stale on the next one.
    """
    parts = relative.split("/")
    for pattern in sorted(kinds, key=len, reverse=True):
        needle = pattern.strip("/").split("/")
        window = range(len(parts) - len(needle) + 1)
        if any(
            all(fnmatch.fnmatch(part, glob) for part, glob in zip(parts[i:], needle))
            for i in window
        ):
            return kinds[pattern]
    return "other"


#: ``sp``/``su``/``fa`` in the order a year runs, so semesters sort.
_SEASONS = {"sp": 0, "su": 1, "fa": 2}

#: A semester anywhere in a corpus path: ``sp26/hw/9``, ``exams/fa15/final``,
#: and the frozen banks named for one -- ``su24_questionBank/hw/8``. Ranking a
#: snapshot by the semester in its name files it beside that semester's
#: assignments instead of dumping every bank alphabetically at the end.
_SEMESTER = re.compile(r"^(sp|su|fa)(\d{2})(?:_.*)?$")


def newest_first(path: str) -> tuple:
    """Sort key putting the current semester at the top and the oldest last.

    Alphabetical order buries this semester's material: ``exams`` and ``fa17``
    come before ``sp26``, so the list a person opens starts nine years ago.
    The semester can sit anywhere in the path -- ``sp26/hw/9`` but also
    ``exams/fa15/final`` -- and anything with no semester at all (``notes``,
    ``questionBank``) sorts after everything that has one, alphabetically.
    """
    for part in path.split("/"):
        match = _SEMESTER.match(part)
        if match:
            year, season = int(match.group(2)), _SEASONS[match.group(1)]
            # Negated: Python sorts ascending, and the newest has to lead.
            return (0, -year, -season, path)
    return (1, 0, 0, path)


def discover_assignments(
    profile: Profile,
    scope: str | None = None,
    *,
    kinds: dict[str, str] | None = None,
) -> list[Assignment]:
    """Every assignment directory in a scope, classified and driver-resolved.

    Works off ``profile.iter_files``, so profile excludes apply -- which is the
    whole point in a corpus where 17k of 17.6k .tex files are frozen per-semester
    snapshots nobody reads.
    """
    root = profile.corpus.root.resolve()
    kinds = kinds if kinds is not None else profile.corpus.kinds

    counts: dict[str, int] = {}
    for path in profile.iter_files(scope):
        if path.suffix.lower() != ".tex":
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        parent = relative.parent.as_posix()
        counts[parent] = counts.get(parent, 0) + 1

    prefixes = profile.corpus.variants or DEFAULT_VARIANT_PREFIXES
    found: list[Assignment] = []
    for relative, count in sorted(counts.items(), key=lambda item: newest_first(item[0])):
        # `.` is the corpus root. It holds loose files -- `circuitikz_version.tex`
        # and the like -- and calling that an assignment puts "the whole corpus"
        # in a list of homeworks.
        if relative == ".":
            continue
        directory = root / relative
        drivers = find_drivers(directory, prefixes)
        # A directory with nothing compilable in it is not an assignment. The
        # corpus is full of `figures/`, `questions/` and `*_figs/` directories
        # holding fragments; listing them as unbuildable assignments padded the
        # scope picker with a hundred rows nobody can ever select.
        if not drivers:
            continue
        found.append(
            Assignment(
                path=relative,
                kind=_kind_of(relative, kinds),
                driver=next(iter(drivers.values()), None),
                tex_files=count,
                drivers=drivers,
            )
        )
    return found


def group_by_kind(assignments: Iterable[Assignment]) -> dict[str, list[Assignment]]:
    """Assignments bucketed by kind, for the TUI's scope picker."""
    grouped: dict[str, list[Assignment]] = {}
    for assignment in assignments:
        grouped.setdefault(assignment.kind, []).append(assignment)
    return dict(sorted(grouped.items()))


def iter_selected(profile: Profile, config: RunConfig) -> Iterator[Assignment]:
    """The assignments a config names, resolved against the corpus.

    Raises rather than skipping a path that does not exist: a run that silently
    converts four of the five things you asked for is worse than one that stops.
    """
    root = profile.corpus.root.resolve()
    kinds = profile.corpus.kinds
    prefixes = profile.corpus.variants or DEFAULT_VARIANT_PREFIXES
    for relative in config.assignments:
        directory = (root / relative).resolve()
        if not directory.is_dir():
            raise ConfigError(
                f"no such assignment directory: {relative}",
                hint=f"paths are relative to the corpus root {root}",
            )
        drivers = find_drivers(directory, prefixes)
        yield Assignment(
            path=relative,
            kind=_kind_of(relative, kinds),
            driver=next(iter(drivers.values()), None),
            tex_files=len(list(directory.glob("*.tex"))),
            drivers=drivers,
        )

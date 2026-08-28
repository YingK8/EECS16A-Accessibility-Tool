"""Build and persist description catalogs.

Ties the pipeline together: scan a scope, derive skeletons deterministically,
collapse call sites onto content-addressed entries, and merge the result into
the existing Markdown worklogs without ever clobbering human text.
"""

from __future__ import annotations

import re

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import Profile
from ..errors import CatalogError
from ..describe import describe_reference
from ..scan import FigureRef, scan_corpus
from ..texlex import TexSource
from .worklog import Entry, Worklog, merge, read_worklog, write_worklog

__all__ = [
    "BANK_BUCKET",
    "CatalogResult",
    "WORKLOG_NAME",
    "build_catalog",
    "default_output_root",
    "load_entries",
    "worklog_dir",
    "worklog_path",
]


@dataclass(slots=True)
class CatalogResult:
    entries: dict[str, Entry]
    worklogs: dict[Path, Worklog]
    call_sites: int

    @property
    def unique(self) -> int:
        return len(self.entries)

    @property
    def done(self) -> int:
        return sum(1 for entry in self.entries.values() if entry.is_done)

    @property
    def outstanding(self) -> list[Entry]:
        return [entry for entry in self.entries.values() if entry.needs_human]

    def as_dict(self) -> dict:
        return {
            "call_sites": self.call_sites,
            "unique": self.unique,
            "done": self.done,
            "outstanding": len(self.outstanding),
            "worklogs": [str(path) for path in self.worklogs],
        }


#: Where a run writes when nobody says otherwise. Kept here as well as on
#: `Output.root` so a bare `scan` and a full `build` agree without one
#: importing the other.


def worklog_dir(
    profile: Profile,
    output_root: Path | None = None,
    *,
    directory: Path | None = None,
) -> Path:
    """Where description worklogs live: ``<corpus>/ally-out/descriptions``.

    The default is inside the corpus on purpose, and this reverses an earlier
    decision worth recording. Worklogs used to be forbidden there, on the
    grounds that a tool which grows directories inside somebody's course
    repository is a tool people stop trusting. What that reasoning missed is
    that the alternative put them inside the *tool's* repository, which is
    worse in the same way and additionally wrong: the descriptions are course
    content, written by course staff, outliving any checkout of this tool.
    They belong with the material they describe.

    One directory, not one per source folder: a description is
    content-addressed and serves every assignment that uses the figure, so
    scattering copies through the tree makes the shared ones ambiguous.
    """
    # An explicit directory wins outright. `in-place` re-roots a run's output
    # at the assignment's own folder, and descriptions are the one artifact
    # that must NOT follow it there: a description is content-addressed and
    # serves every assignment using the figure, so one copy per assignment
    # folder would make the shared ones ambiguous. The re-rooted `Output` pins
    # the old location and passes it through here.
    if directory is not None:
        return Path(directory).resolve()
    root = (
        Path(output_root)
        if output_root is not None
        else default_output_root(profile)
    )
    return (root / "descriptions").resolve()


def default_output_root(profile: Profile) -> Path:
    """``<corpus>/ally-out``.

    Anchored to the corpus rather than the working directory. A bare
    ``ally-out`` meant "wherever you happened to be standing", which put a
    build's output inside the tool's own checkout when run from there, and
    inside an assignment folder when run from one.
    """
    return (profile.corpus.root.resolve() / "ally-out")


#: Kept for reading worklogs written by older versions, which put one
#: ``descriptions.yaml`` in each source folder.
WORKLOG_NAME = "descriptions.yaml"

#: Semester bucket for material belonging to no single semester -- above all
#: the shared question bank, where about three quarters of this corpus's
#: graphics live. Filing it under whichever semester happened to \input it
#: would be wrong twice: the same figure would land in several semesters'
#: files, and a description written in one would be invisible to the rest.
#: Descriptions are content-addressed precisely so one serves every use.
BANK_BUCKET = "bank"

#: ``kind`` from the profile -> the slug that names its file.
_KIND_SLUGS = {
    "homework": "hw",
    "discussion": "disc",
    "exam": "exam",
    "note": "notes",
    "bank": "bank",
    "extra-credit": "extra",
    "lab": "lab",
}
_MISC_SLUG = "misc"

#: ``sp26``, ``fa25``, ``su24`` -- the shape a semester directory has here.
_SEMESTER = re.compile(r"^(?:sp|fa|su)\d{2}$")


def _semester_of(relative: str) -> str:
    """Which semester folder a source belongs under.

    The first path component shaped like a semester wins, wherever it sits, so
    ``exams/fa15/final`` files under ``fa15`` rather than under ``exams`` -- the
    exam archive is organised by semester one level down. Material with no
    semester anywhere in its path files under its top-level directory, except
    the shared bank, which gets :data:`BANK_BUCKET`.
    """
    parts = [part for part in relative.split("/") if part not in ("", ".")]
    for part in parts:
        if _SEMESTER.match(part):
            return part
    if parts and parts[0] == "questionBank":
        return BANK_BUCKET
    return parts[0] if parts else BANK_BUCKET


def _kind_slug(relative: str, profile: Profile) -> str:
    """The material type, as the short word that names its file.

    Classification is :func:`~latexally.discover._kind_of`, so the profile's
    own ``kinds`` map is the single authority -- a course that spells
    discussions ``sec`` says so in one place and both the runner's grouping and
    these filenames follow.
    """
    from ..discover import _kind_of

    # Inside the shared bank, "bank" is the bucket, not the material: every
    # file there is bank material, so classifying by it would collapse nine
    # years of homework, discussion and exam questions into one file. The
    # question's own kind is one component further in.
    parts = relative.split("/")
    if parts and parts[0] == "questionBank":
        relative = "/".join(parts[1:]) or relative
    return _KIND_SLUGS.get(_kind_of(relative, profile.corpus.kinds), _MISC_SLUG)


def worklog_path(relative: str, profile: Profile, directory: Path) -> Path:
    """``<directory>/<semester>/<kind>_fig_alt_texts.yaml``."""
    return (
        directory
        / _semester_of(relative)
        / f"{_kind_slug(relative, profile)}_fig_alt_texts.yaml"
    )


def _dir_for(reference: FigureRef, root: Path) -> str:
    """The source's own directory, relative to ``root`` -- ``""`` at the root.

    The same fact :func:`_shard_for` flattens into a name, kept unflattened so
    a worklog can be written back into that directory. Deriving both from one
    place is what keeps ``questionBank-hw-11.yaml`` and
    ``questionBank/hw/11/descriptions.yaml`` describing the same figures.
    """
    try:
        relative = reference.file.resolve().relative_to(root)
    except ValueError:
        return ""
    return relative.parent.as_posix().strip("/").lstrip(".").strip("/")


def _shard_for(reference: FigureRef, root: Path) -> str:
    """Which worklog file an entry belongs to.

    Sharding by the directory that holds the source keeps each worklog small
    enough to review in one sitting and keeps two TAs working on different
    assignments out of each other's diffs.
    """
    try:
        relative = reference.file.resolve().relative_to(root)
    except ValueError:
        return "external"
    parent = relative.parent.as_posix().strip("/")
    return parent.replace("/", "-") or "root"


def build_catalog(
    profile: Profile,
    scope: str | None = None,
    *,
    write: bool = True,
    files: list[Path] | None = None,
    output_root: Path | None = None,
    shard_root: Path | None = None,
    worklogs: Path | None = None,
) -> CatalogResult:
    """Scan a scope and refresh its worklogs.

    ``files`` scans an explicit list instead of a scope glob. That is how an
    assignment is scanned honestly: its graphics mostly are not in its own
    directory (see :func:`latexally.scan.scan_corpus`).

    ``shard_root`` is the directory worklog names are taken relative to. It
    exists for scanning the build mirror, whose layout repeats the corpus's but
    whose paths are not under it -- without it every mirrored file lands in one
    ``external`` worklog and each assignment overwrites the last.

    """
    root = (shard_root or profile.corpus.root).resolve()
    references = scan_corpus(profile, scope, files=files)

    by_id: dict[str, list[FigureRef]] = defaultdict(list)
    for reference in references:
        by_id[reference.id].append(reference)

    # Cache parsed sources: a file usually holds several figures, and the
    # describers re-read the same text.
    sources: dict[Path, TexSource] = {}

    entries: dict[str, Entry] = {}
    shard_of: dict[str, str] = {}
    dir_of: dict[str, str] = {}
    for identity, group in by_id.items():
        primary = group[0]
        source = sources.get(primary.file)
        if source is None:
            try:
                source = TexSource.from_path(primary.file)
            except Exception:  # pragma: no cover
                continue
            sources[primary.file] = source
        skeleton = describe_reference(primary, source)
        artifact_listed = any(
            primary.image_path and primary.image_path.endswith(candidate)
            for candidate in profile.figures.artifact_allowlist
        )
        entries[identity] = Entry(
            id=identity,
            kind=primary.kind,
            genre=skeleton.genre,
            disposition="artifact" if artifact_listed else "figure",
            confidence=skeleton.confidence,
            sites=[
                (reference.file.resolve().relative_to(root).as_posix(), reference.line)
                for reference in group
                if _is_within(reference.file, root)
            ],
            caption=primary.caption,
            question=primary.question,
            image_path=primary.image_path,
            inside_solution=all(reference.inside_solution for reference in group),
            missing_image=primary.missing_image,
            skeleton=skeleton,
        )
        shard_of[identity] = _shard_for(primary, root)
        dir_of[identity] = _dir_for(primary, root)

    directory = worklog_dir(profile, output_root, directory=worklogs)
    # Descriptions outlive any one run. They are content-addressed and were
    # written by a person, so the corpus catalogue is always the merge base --
    # even when `output_root` sends this run's worklogs somewhere else.
    #
    # Without this, `-o somewhere-new` starts from zero every time: a scan
    # reports "0 described, 17 outstanding" while approved descriptions for six
    # of those very figures sit in the corpus, and the build ships the figures
    # with no /Alt. The corpus stays read-only -- it is read, never written.
    baseline = worklog_dir(profile)
    grouped: dict[Path, dict[str, Entry]] = defaultdict(dict)
    for identity, entry in entries.items():
        grouped[worklog_path(dir_of[identity], profile, directory)][identity] = entry

    worklogs: dict[Path, Worklog] = {}
    for path, shard_entries in grouped.items():
        shard = path.parent.name + "/" + path.stem
        previous = read_worklog(path)
        if baseline != directory:
            # This run's own worklog wins over the corpus, so a description
            # edited inside an output directory is not reverted by an older
            # one carrying the same id.
            inherited = read_worklog(baseline / path.relative_to(directory))
            inherited.entries.update(previous.entries)
            previous = inherited
        merged = merge(previous, shard_entries)
        merged.path = path
        merged.scope = shard
        worklogs[path] = merged
        # Human text was merged back in, so refresh the in-memory entries too.
        entries.update(merged.entries)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(write_worklog(merged, scope=shard), encoding="utf-8")

    return CatalogResult(entries=entries, worklogs=worklogs, call_sites=len(references))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def load_entries(
    profile: Profile,
    output_root: Path | None = None,
    *,
    worklogs: Path | None = None,
) -> dict[str, Entry]:
    """Every description on disk, keyed by content hash.

    Read across all worklogs so a description written for one assignment is
    reused wherever the same figure appears -- the payoff of content addressing.

    """
    entries: dict[str, Entry] = {}
    directory = worklog_dir(profile, output_root, directory=worklogs)
    if not directory.is_dir():
        return entries
    # Recursive: the worklogs sit one semester folder down.
    candidates = sorted(directory.rglob("*.yaml"))
    for path in candidates:
        for identity, entry in read_worklog(path).entries.items():
            existing = entries.get(identity)
            # Prefer an approved description over a draft of the same figure.
            if existing is None or (not existing.is_done and entry.is_done):
                entries[identity] = entry
    return entries

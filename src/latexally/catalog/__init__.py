"""Build and persist description catalogs.

Ties the pipeline together: scan a scope, derive skeletons deterministically,
collapse call sites onto content-addressed entries, and merge the result into
the existing Markdown worklogs without ever clobbering human text.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import Profile
from ..errors import CatalogError
from ..describe import describe_reference
from ..scan import FigureRef, scan_corpus
from ..texlex import TexSource
from .worklog import Entry, Worklog, merge, read_worklog, write_worklog

__all__ = ["CatalogResult", "WORKLOG_NAME", "build_catalog", "load_entries", "worklog_dir"]


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
DEFAULT_OUTPUT_ROOT = Path("ally-out")


def worklog_dir(profile: Profile, output_root: Path | None = None) -> Path:
    """Where description worklogs live: under the output root, never the corpus.

    Resolved against ``output_root``, falling back to
    :data:`DEFAULT_OUTPUT_ROOT` so a bare ``latexally scan`` writes where a
    build does.

    It used to default to ``<corpus>/<catalog_dir>/descriptions`` -- beside the
    material, which reads well and is wrong. The corpus is somebody's course
    repository, and a tool that quietly grows directories inside it is a tool
    people stop trusting. Nothing this package produces belongs there.
    """
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    directory = (root / "descriptions").resolve()
    _refuse_inside_corpus(directory, profile)
    return directory


def _refuse_inside_corpus(directory: Path, profile: Profile) -> None:
    """Fail loudly rather than write into the material being converted.

    A guard, not a nicety: every other safeguard here assumes the corpus is
    read-only in mirror mode, and a redirected output root is exactly the kind
    of setting somebody points at the wrong place once.
    """
    corpus = profile.corpus.root.resolve()
    if directory == corpus or corpus in directory.parents:
        raise CatalogError(
            f"worklogs would be written inside the corpus: {directory}",
            hint=(
                "the corpus holds the course material and this tool never writes "
                "into it; point --output somewhere else"
            ),
        )



#: What a worklog is called when it lives beside the sources it describes,
#: rather than sharded by name into an output directory. One per folder, so a
#: TA opening a homework sees exactly one file to fill in.
WORKLOG_NAME = "descriptions.yaml"


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
    beside: Path | None = None,
) -> CatalogResult:
    """Scan a scope and refresh its worklogs.

    ``files`` scans an explicit list instead of a scope glob. That is how an
    assignment is scanned honestly: its graphics mostly are not in its own
    directory (see :func:`latexally.scan.scan_corpus`).

    ``shard_root`` is the directory worklog names are taken relative to. It
    exists for scanning the build mirror, whose layout repeats the corpus's but
    whose paths are not under it -- without it every mirrored file lands in one
    ``external`` worklog and each assignment overwrites the last.

    ``beside`` writes each worklog into the directory holding its sources,
    under that root, as ``descriptions.yaml`` -- rather than sharding every one
    by name into a single output directory. Only ``edit`` mode passes it, and
    only because that mode is already rewriting those very files: a folder the
    tool has just edited should carry the one file saying what is left to
    describe, not send its author to a directory elsewhere to find it.
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

    # Both are skipped entirely when `beside` is set: neither is used on that
    # path, and `worklog_dir` refuses any location inside the corpus -- which
    # is exactly where `beside` points, on purpose.
    directory = worklog_dir(profile, output_root) if beside is None else None
    # Descriptions outlive any one run. They are content-addressed and were
    # written by a person, so the corpus catalogue is always the merge base --
    # even when `output_root` sends this run's worklogs somewhere else.
    #
    # Without this, `-o somewhere-new` starts from zero every time: a scan
    # reports "0 described, 17 outstanding" while approved descriptions for six
    # of those very figures sit in the corpus, and the build ships the figures
    # with no /Alt. The corpus stays read-only -- it is read, never written.
    baseline = worklog_dir(profile) if beside is None else None
    # Keyed by both, because `beside` needs the unflattened directory and the
    # shard name is still what names the file everywhere else. They are a
    # 1:1 function of each other, so the pair never splits one folder in two.
    grouped: dict[tuple[str, str], dict[str, Entry]] = defaultdict(dict)
    for identity, entry in entries.items():
        grouped[(shard_of[identity], dir_of[identity])][identity] = entry

    worklogs: dict[Path, Worklog] = {}
    for (shard, relative_dir), shard_entries in grouped.items():
        if beside is not None:
            path = (Path(beside) / relative_dir / WORKLOG_NAME).resolve()
        else:
            path = directory / f"{shard}.yaml"
        previous = read_worklog(path)
        if beside is None and baseline != directory:
            # This run's own worklog wins over the corpus, so a description
            # edited inside an output directory is not reverted by an older
            # one carrying the same id.
            inherited = read_worklog(baseline / f"{shard}.yaml")
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
    beside: Path | None = None,
) -> dict[str, Entry]:
    """Every description on disk, keyed by content hash.

    Read across all worklogs so a description written for one assignment is
    reused wherever the same figure appears -- the payoff of content addressing.

    ``beside`` reads the ``descriptions.yaml`` files scattered through a source
    tree instead of one output directory. It has to be honoured here and not
    only on the write side: the whole point of putting the worklog next to the
    ``.tex`` is that a TA fills it in there, and a run that wrote it there but
    read from ``ally-out`` would silently ignore an afternoon of their work.
    """
    entries: dict[str, Entry] = {}
    if beside is not None:
        candidates = sorted(Path(beside).rglob(WORKLOG_NAME))
    else:
        directory = worklog_dir(profile, output_root)
        if not directory.is_dir():
            return entries
        candidates = sorted(directory.glob("*.yaml"))
    for path in candidates:
        for identity, entry in read_worklog(path).entries.items():
            existing = entries.get(identity)
            # Prefer an approved description over a draft of the same figure.
            if existing is None or (not existing.is_done and entry.is_done):
                entries[identity] = entry
    return entries

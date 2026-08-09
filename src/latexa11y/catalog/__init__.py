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
from ..describe import describe_reference
from ..scan.figures import FigureRef, scan_corpus
from ..texlex import TexSource
from .worklog import Entry, Worklog, merge, read_worklog, write_worklog

__all__ = ["CatalogResult", "build_catalog", "load_entries", "worklog_dir"]


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


def worklog_dir(profile: Profile) -> Path:
    return profile.corpus.root / profile.catalog_dir / "alt"


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
    profile: Profile, scope: str | None = None, *, write: bool = True
) -> CatalogResult:
    """Scan a scope and refresh its worklogs."""
    root = profile.corpus.root.resolve()
    references = scan_corpus(profile, scope)

    by_id: dict[str, list[FigureRef]] = defaultdict(list)
    for reference in references:
        by_id[reference.id].append(reference)

    # Cache parsed sources: a file usually holds several figures, and the
    # describers re-read the same text.
    sources: dict[Path, TexSource] = {}

    entries: dict[str, Entry] = {}
    shard_of: dict[str, str] = {}
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

    directory = worklog_dir(profile)
    grouped: dict[str, dict[str, Entry]] = defaultdict(dict)
    for identity, entry in entries.items():
        grouped[shard_of[identity]][identity] = entry

    worklogs: dict[Path, Worklog] = {}
    for shard, shard_entries in grouped.items():
        path = directory / f"{shard}.md"
        merged = merge(read_worklog(path), shard_entries)
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


def load_entries(profile: Profile) -> dict[str, Entry]:
    """Every description on disk, keyed by content hash.

    Read across all worklogs so a description written for one assignment is
    reused wherever the same figure appears -- the payoff of content addressing.
    """
    entries: dict[str, Entry] = {}
    directory = worklog_dir(profile)
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob("*.md")):
        for identity, entry in read_worklog(path).entries.items():
            existing = entries.get(identity)
            # Prefer an approved description over a draft of the same figure.
            if existing is None or (not existing.is_done and entry.is_done):
                entries[identity] = entry
    return entries

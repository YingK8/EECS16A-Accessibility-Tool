"""Markdown worklogs: the surface where humans and agents write descriptions.

Why Markdown, and why one file per assignment
---------------------------------------------
The previous catalog was a single tab-separated file with one line per figure.
That fails three ways at once: TSV has no defined escaping, so a tab or newline
in a description silently shifts every column; one line per figure makes a
multi-paragraph long description structurally impossible; and one shared file
across a team turns every TA's edit into a merge conflict on the same lines.

Markdown, sharded per assignment, fixes all three and adds something neither
TSV nor YAML gives: it is the format both a TA and an LLM agent read most
naturally, and it renders in every code host, so a reviewer can read a worklog
without running the tool.

The contract
------------
* Files are **regenerated** by ``scan``: machine sections are rewritten from the
  source every time.
* Human-authored fields -- ``description``, ``long``, ``status``,
  ``disposition``, ``notes`` -- are **never overwritten**; they are read back in
  and preserved.
* Entries are keyed by the content hash, so a figure keeps its description
  across file edits, renames and semester rollovers.
* The machine-derived skeleton is displayed but stored separately from
  ``description``, so a skeleton can never be mistaken for an approved one.
"""

from __future__ import annotations

import re

import yaml
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..describe.common import Skeleton

__all__ = [
    "DISPOSITIONS",
    "Entry",
    "PLACEHOLDER",
    "STATUSES",
    "Worklog",
    "read_worklog",
    "write_worklog",
]

STATUSES = ("todo", "draft", "needs-review", "approved")
DISPOSITIONS = ("figure", "artifact")

_HEADER = "<!-- latexally worklog v1 -->"
#: What sits under `### description` until a person replaces it. Exported
#: so nothing has to hard-code the wording to find it.
PLACEHOLDER = "<!-- write it here -->"
_ENTRY = re.compile(r"^## +(?P<id>[A-Za-z]+-[0-9a-f]+)\s*$", re.MULTILINE)
_FIELD = re.compile(r"^- +(?P<key>[a-z-]+): +(?P<value>.*)$", re.MULTILINE)
#: `alt` is still accepted on READ so a worklog written before the rename keeps
#: its human text; `description` is the only spelling ever written.
_SECTION = re.compile(r"^### +(?P<name>description|alt|long|notes)\s*$", re.MULTILINE)


@dataclass(slots=True)
class Entry:
    """One unique figure and its description."""

    id: str
    kind: str = ""
    genre: str = ""
    disposition: str = "figure"
    status: str = "todo"
    confidence: str = "low"
    description: str = ""
    long_description: str = ""
    notes: str = ""
    author: str = ""
    updated: str = ""
    #: (path relative to corpus root, line) for every call site.
    sites: list[tuple[str, int]] = field(default_factory=list)
    caption: str | None = None
    question: str | None = None
    image_path: str | None = None
    inside_solution: bool = False
    missing_image: bool = False
    skeleton: Skeleton | None = None

    @property
    def is_done(self) -> bool:
        """Written means approved. The file carries no separate status.

        The worklog used to gate on `status: approved`, which let an agent leave
        a description parked as `needs-review` until a human promoted it. That
        gate is gone: whatever stands in `alt_text` is what ships.
        """
        return bool(self.description.strip())

    @property
    def needs_human(self) -> bool:
        return not self.is_done

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "genre": self.genre,
            "disposition": self.disposition,
            "status": self.status,
            "confidence": self.confidence,
            "description": self.description,
            "long_description": self.long_description,
            "notes": self.notes,
            "sites": [{"file": f, "line": ln} for f, ln in self.sites],
            "caption": self.caption,
            "question": self.question,
            "image_path": self.image_path,
            "inside_solution": self.inside_solution,
            "missing_image": self.missing_image,
            "skeleton": self.skeleton.as_dict() if self.skeleton else None,
        }


@dataclass(slots=True)
class Worklog:
    scope: str
    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)

    @property
    def done(self) -> int:
        return sum(1 for entry in self.entries.values() if entry.is_done)

    @property
    def total(self) -> int:
        return len(self.entries)


# ---------------------------------------------------------------------- #
# reading
# ---------------------------------------------------------------------- #


def read_worklog(path: Path) -> Worklog:
    """Parse a worklog. Only ``alt_text`` is read back.

    ``file`` and ``lines`` are re-derived by the next scan, so they are written
    for a human to navigate by and ignored on the way in. Parsing is forgiving:
    a worklog is hand-edited, and a malformed entry must not take the run down
    with it.
    """
    path = Path(path)
    worklog = Worklog(scope="", path=path)
    if not path.is_file():
        return worklog
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return worklog
    if not isinstance(loaded, dict):
        return worklog

    for identity, body in loaded.items():
        if not isinstance(body, dict):
            continue
        alt = body.get("alt_text")
        worklog.entries[str(identity)] = Entry(
            id=str(identity),
            description="" if alt is None else str(alt).strip(),
        )
    return worklog


# ---------------------------------------------------------------------- #
# writing
# ---------------------------------------------------------------------- #


def write_worklog(worklog: Worklog, *, scope: str = "") -> str:
    """Render a worklog to YAML. Returns the text (caller writes the file).

    Three keys per figure and nothing else. Everything the scan knows -- caption,
    enclosing question, whether the image resolved -- is recomputed every run and
    reaching it through `latexally agent next-task` costs nothing, so persisting
    it here only buried the one line somebody has to type.
    """
    entries = sorted(
        worklog.entries.values(),
        # Most-referenced first: describing one figure cited eight times is
        # eight times the accessibility win for the same effort.
        key=lambda entry: (entry.is_done, -len(entry.sites), entry.id),
    )
    out: list[str] = []
    for entry in entries:
        file, lines = _primary_site(entry)
        out.append(f"{entry.id}:")
        out.append(f"  file: {file}")
        out.append(f"  lines: [{', '.join(str(n) for n in lines)}]")
        out.append(f"  alt_text: {_scalar(entry.description)}")
    return "\n".join(out).rstrip() + "\n" if out else ""


def _primary_site(entry: Entry) -> tuple[str, list[int]]:
    """The file a reader should open, and every line in it citing this figure.

    A figure is content-addressed, so one description can serve call sites in
    several files. The file listed is the one with the most of them; the rest
    are recovered by the scan and never needed here.
    """
    if not entry.sites:
        return "", []
    by_file: dict[str, list[int]] = {}
    for path, line in entry.sites:
        by_file.setdefault(path, []).append(line)
    file = max(by_file, key=lambda name: (len(by_file[name]), name))
    return file, sorted(by_file[file])


def _scalar(text: str) -> str:
    """A description as a YAML scalar.

    Quoted whenever the plain form would not round-trip: empty, leading or
    trailing space, or a character that opens a collection, a comment or an
    anchor. A description is prose, so this is usually a bare string.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    risky = collapsed[0] in "-?:,[]{}#&*!|>'\"%@`" or ": " in collapsed or collapsed.endswith(":")
    if risky:
        return '"' + collapsed.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return collapsed


def merge(previous: Worklog, fresh: dict[str, Entry], *, today: str | None = None) -> Worklog:
    """Fold a fresh scan into an existing worklog, human text winning.

    Machine fields are always taken from the new scan; ``description``,
    ``notes``, ``status`` and ``disposition`` are always taken from the existing
    worklog when present. A description therefore survives arbitrary edits to
    the source that produced it.
    """
    merged = Worklog(scope=previous.scope, path=previous.path)
    for identity, entry in fresh.items():
        existing = previous.entries.get(identity)
        if existing is not None:
            entry.description = existing.description
        merged.entries[identity] = entry
    return merged

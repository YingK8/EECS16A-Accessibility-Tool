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
* Human-authored fields -- ``alt``, ``long``, ``status``, ``disposition``,
  ``notes`` -- are **never overwritten**; they are read back in and preserved.
* Entries are keyed by the content hash, so a figure keeps its description
  across file edits, renames and semester rollovers.
* The machine-derived skeleton is displayed but stored separately from ``alt``,
  so a skeleton can never be mistaken for an approved description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..describe.common import Skeleton
from ..errors import CatalogError

__all__ = ["Entry", "Worklog", "read_worklog", "write_worklog", "STATUSES", "DISPOSITIONS"]

STATUSES = ("todo", "draft", "needs-review", "approved")
DISPOSITIONS = ("figure", "artifact")

_HEADER = "<!-- latexa11y worklog v1 -->"
_ENTRY = re.compile(r"^## +(?P<id>[A-Za-z]+-[0-9a-f]+)\s*$", re.MULTILINE)
_FIELD = re.compile(r"^- +(?P<key>[a-z-]+): +(?P<value>.*)$", re.MULTILINE)
_SECTION = re.compile(r"^### +(?P<name>alt|long|notes)\s*$", re.MULTILINE)


@dataclass(slots=True)
class Entry:
    """One unique figure and its description."""

    id: str
    kind: str = ""
    genre: str = ""
    disposition: str = "figure"
    status: str = "todo"
    confidence: str = "low"
    alt: str = ""
    long: str = ""
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
        if self.disposition == "artifact":
            return True
        return self.status == "approved" and bool(self.alt.strip())

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
            "alt": self.alt,
            "long": self.long,
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
    """Parse a worklog, keeping only what a human may have written.

    Machine fields are re-derived on the next scan, so parsing is deliberately
    forgiving: an unrecognised field is ignored rather than fatal, because a
    worklog is a document people hand-edit.
    """
    path = Path(path)
    worklog = Worklog(scope="", path=path)
    if not path.is_file():
        return worklog
    text = path.read_text(encoding="utf-8")

    matches = list(_ENTRY.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:stop]
        entry = Entry(id=match.group("id"))

        # Field list, up to the first ### section.
        head = block[: _SECTION.search(block).start()] if _SECTION.search(block) else block
        for field_match in _FIELD.finditer(head):
            key, value = field_match.group("key"), field_match.group("value").strip()
            if key == "status" and value in STATUSES:
                entry.status = value
            elif key == "disposition" and value in DISPOSITIONS:
                entry.disposition = value
            elif key == "author":
                entry.author = value
            elif key == "updated":
                entry.updated = value

        for name, body in _sections(block).items():
            if name == "alt":
                entry.alt = body
            elif name == "long":
                entry.long = body
            elif name == "notes":
                entry.notes = body
        worklog.entries[entry.id] = entry
    return worklog


def _sections(block: str) -> dict[str, str]:
    """Body text of each ``### alt`` / ``### long`` / ``### notes`` section."""
    found: dict[str, str] = {}
    marks = list(_SECTION.finditer(block))
    for index, match in enumerate(marks):
        start = match.end()
        stop = marks[index + 1].start() if index + 1 < len(marks) else len(block)
        body = block[start:stop]
        # Strip the guidance comment the writer generates, and any HTML comment.
        body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        found[match.group("name")] = body.strip()
    return found


# ---------------------------------------------------------------------- #
# writing
# ---------------------------------------------------------------------- #


def write_worklog(worklog: Worklog, *, scope: str = "") -> str:
    """Render a worklog to Markdown. Returns the text (caller writes the file)."""
    entries = sorted(
        worklog.entries.values(),
        # Most-referenced first: describing one figure cited eight times is
        # eight times the accessibility win for the same effort.
        key=lambda entry: (entry.is_done, -len(entry.sites), entry.id),
    )
    done = sum(1 for entry in entries if entry.is_done)
    lines: list[str] = [
        f"# Alt-text worklog — {scope or worklog.scope}",
        "",
        _HEADER,
        "",
        f"**Progress:** {done} of {len(entries)} figures described "
        f"({(100 * done / len(entries)) if entries else 100:.0f}%).",
        "",
        "Fill in the `### alt` section of each entry below. Everything else is",
        "regenerated by `latexa11y scan`, so edits outside `alt`, `long`, `notes`,",
        "`status:` and `disposition:` will be overwritten.",
        "",
        "Rules, in short: write plain words, never LaTeX; do not begin with",
        '"image of"; do not repeat the caption (a reader announces it separately);',
        "convey the data, not the drawing. See docs/ALT_TEXT_SPEC.md.",
        "",
    ]

    for entry in entries:
        lines.extend(_render_entry(entry))
    return "\n".join(lines).rstrip() + "\n"


def _render_entry(entry: Entry) -> list[str]:
    lines = ["---", "", f"## {entry.id}", ""]
    lines.append(f"- kind: {entry.kind}")
    lines.append(f"- genre: {entry.genre}")
    lines.append(f"- disposition: {entry.disposition}")
    lines.append(f"- status: {entry.status}")
    lines.append(f"- confidence: {entry.confidence}")
    lines.append(f"- call-sites: {len(entry.sites)}")
    if entry.inside_solution:
        lines.append("- solution-only: yes")
    if entry.image_path:
        lines.append(f"- image: {entry.image_path}")
    if entry.missing_image:
        lines.append("- WARNING: image file not found on disk")
    if entry.author:
        lines.append(f"- author: {entry.author}")
    if entry.updated:
        lines.append(f"- updated: {entry.updated}")
    lines.append("")

    if entry.sites:
        lines.append("**Appears in**")
        lines.append("")
        for path, line in entry.sites[:8]:
            lines.append(f"- `{path}:{line}`")
        if len(entry.sites) > 8:
            lines.append(f"- …and {len(entry.sites) - 8} more")
        lines.append("")

    if entry.question:
        lines.append("**Question being asked**")
        lines.append("")
        lines.append(f"> {_quote(entry.question)}")
        lines.append("")
    if entry.caption:
        lines.append("**Caption** (already read aloud separately — do not repeat it)")
        lines.append("")
        lines.append(f"> {_quote(entry.caption)}")
        lines.append("")

    if entry.skeleton and (entry.skeleton.summary or entry.skeleton.details):
        lines.append("**Machine-derived facts** — a starting point, not alt text")
        lines.append("")
        if entry.skeleton.summary:
            lines.append(f"> {_quote(entry.skeleton.summary)}")
        for detail in entry.skeleton.details[:10]:
            lines.append(f"> {_quote(detail)}")
        lines.append("")
    if entry.skeleton and entry.skeleton.table:
        lines.append(
            f"**Extracted data** ({len(entry.skeleton.table)} rows) — too much for an "
            "alt string; emit it as a table in the document body"
        )
        lines.append("")
        header = entry.skeleton.table_header or ("x", "y")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in entry.skeleton.table[:20]:
            lines.append("| " + " | ".join(row) + " |")
        if len(entry.skeleton.table) > 20:
            lines.append(f"| …{len(entry.skeleton.table) - 20} more | |")
        lines.append("")
    if entry.skeleton and entry.skeleton.needs:
        lines.append("**Still needed from you**")
        lines.append("")
        for need in entry.skeleton.needs:
            lines.append(f"- {need}")
        lines.append("")

    lines.append("### alt")
    lines.append("")
    lines.append(entry.alt if entry.alt else "<!-- write the short description here -->")
    lines.append("")
    lines.append("### long")
    lines.append("")
    lines.append(
        entry.long
        if entry.long
        else "<!-- optional: only when the short description cannot carry it all -->"
    )
    lines.append("")
    if entry.notes:
        lines.append("### notes")
        lines.append("")
        lines.append(entry.notes)
        lines.append("")
    return lines


def _quote(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def merge(previous: Worklog, fresh: dict[str, Entry], *, today: str | None = None) -> Worklog:
    """Fold a fresh scan into an existing worklog, human text winning.

    Machine fields are always taken from the new scan; ``alt``, ``long``,
    ``notes``, ``status`` and ``disposition`` are always taken from the existing
    worklog when present. A description therefore survives arbitrary edits to
    the source that produced it.
    """
    merged = Worklog(scope=previous.scope, path=previous.path)
    for identity, entry in fresh.items():
        existing = previous.entries.get(identity)
        if existing is not None:
            entry.alt = existing.alt
            entry.long = existing.long
            entry.notes = existing.notes
            entry.status = existing.status
            entry.disposition = existing.disposition
            entry.author = existing.author
            entry.updated = existing.updated or (today or date.today().isoformat())
        merged.entries[identity] = entry
    return merged

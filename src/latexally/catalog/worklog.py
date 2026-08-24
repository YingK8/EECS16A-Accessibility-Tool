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
        if self.disposition == "artifact":
            return True
        return self.status == "approved" and bool(self.description.strip())

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
            if name in ("description", "alt"):
                entry.description = body
            elif name == "long":
                entry.long_description = body
            elif name == "notes":
                entry.notes = body
        worklog.entries[entry.id] = entry
    return worklog


def _sections(block: str) -> dict[str, str]:
    """Body text of each ``### description`` / ``### long`` / ``### notes`` section."""
    found: dict[str, str] = {}
    marks = list(_SECTION.finditer(block))
    for index, match in enumerate(marks):
        start = match.end()
        stop = marks[index + 1].start() if index + 1 < len(marks) else len(block)
        body = block[start:stop]
        # Strip the guidance comment the writer generates, and any HTML comment.
        body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        # The block's LAST section runs to the end of the block, which ends with
        # the `---` rule separating entries. That rule is punctuation of the
        # file, not content: without this an empty `### long` parses as "---",
        # and anything writing the long description into a document emits it.
        body = re.sub(r"\n?-{3,}[ \t]*$", "", body.rstrip())
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
        f"# Description worklog — {scope or worklog.scope}",
        "",
        _HEADER,
        "",
        f"**{done} of {len(entries)} described.** Write the description under each",
        "heading, then change `status: todo` to `status: approved`. Only approved",
        "text is written into the PDF.",
        "",
        "Plain words, never LaTeX. Do not open with \"image of\". Do not repeat the",
        "caption -- a reader announces it separately. Convey the data, not the",
        "drawing. Full guidance: docs/ALT_TEXT_SPEC.md.",
        "",
    ]

    for entry in entries:
        lines.extend(_render_entry(entry))
    return "\n".join(lines).rstrip() + "\n"


def _render_entry(entry: Entry) -> list[str]:
    """One entry: where it is, what is known, and a blank to fill in.

    Deliberately short. An earlier version printed kind, genre, confidence,
    call-site counts and three prose sections per figure -- forty lines of
    scaffolding around one blank line, which made a worklog of seventeen
    figures read as a document rather than a form.

    Only `status`, `disposition` and the prose sections survive a re-scan;
    everything else here is regenerated, so it is context, not data.
    """
    lines = ["---", "", f"## {entry.id}", ""]

    for path, line in entry.sites[:8]:
        lines.append(f"`{path}:{line}`")
    if len(entry.sites) > 8:
        lines.append(f"…and {len(entry.sites) - 8} more")
    lines.append("")

    lines.append(f"- status: {entry.status}")
    lines.append(f"- disposition: {entry.disposition}")
    if entry.author:
        lines.append(f"- author: {entry.author}")
    if entry.updated:
        lines.append(f"- updated: {entry.updated}")
    if entry.missing_image:
        lines.append(f"- WARNING: {entry.image_path} not found on disk")
    lines.append("")

    context: list[str] = []
    if entry.question:
        context.append(f"Question: {_quote(entry.question)}")
    if entry.caption:
        context.append(f"Caption: {_quote(entry.caption)}")
    if entry.skeleton and (entry.skeleton.summary or entry.skeleton.details):
        facts = [entry.skeleton.summary, *entry.skeleton.details[:10]]
        context.append("Facts: " + "; ".join(_quote(f) for f in facts if f))
    if entry.skeleton and entry.skeleton.needs:
        context.append("Needs: " + "; ".join(entry.skeleton.needs))
    if context:
        lines.extend(context)
        lines.append("")

    # A table is the one thing worth its length: it is the data the description
    # cannot carry, and it belongs in the document body rather than in /Alt.
    if entry.skeleton and entry.skeleton.table:
        header = entry.skeleton.table_header or ("x", "y")
        lines.append(f"Extracted data, {len(entry.skeleton.table)} rows -- too much "
                     "for an alt string; emit it as a table in the body:")
        lines.append("")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in entry.skeleton.table[:20]:
            lines.append("| " + " | ".join(row) + " |")
        if len(entry.skeleton.table) > 20:
            lines.append(f"| …{len(entry.skeleton.table) - 20} more | |")
        lines.append("")

    lines.append("### description")
    lines.append("")
    lines.append(entry.description or PLACEHOLDER)
    lines.append("")
    # Rendered only when used. Always emitting them cost two headings and a
    # placeholder per figure; omitting them when empty loses nothing, because
    # anything already written is read back before this runs.
    if entry.long_description:
        lines.extend(["### long", "", entry.long_description, ""])
    if entry.notes:
        lines.extend(["### notes", "", entry.notes, ""])
    return lines


def _quote(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
            entry.long_description = existing.long_description
            entry.notes = existing.notes
            entry.status = existing.status
            entry.disposition = existing.disposition
            entry.author = existing.author
            entry.updated = existing.updated or (today or date.today().isoformat())
        merged.entries[identity] = entry
    return merged

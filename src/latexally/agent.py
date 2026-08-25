"""Task API for LLM agents and scripted workflows.

Design principles, learned from what goes wrong when an agent edits a corpus:

1. **A task is self-contained.** Everything needed to write one description --
   the machine-derived facts, the question being asked, the caption, whether the
   figure is solution-only, and the rules -- travels with the task. An agent
   that has to go hunting for context guesses instead.

2. **Validation happens before the write, not after.** ``submit`` rejects a
   description that breaks the authoring spec and says exactly which rule failed,
   so the agent can correct itself in one turn.

3. **A submission ships as written.** There is no review gate. The worklog
   carries ``file``, ``lines`` and ``alt_text`` and nothing else, so a
   description has nowhere to sit as a draft: whatever ``submit`` writes is what
   reaches the PDF on the next build.

   This was a deliberate simplification, not an oversight, and it removed the
   only thing standing between an unread machine-written sentence and a
   student. ``validate_description`` is now the sole automatic check, and it
   catches malformed text, not wrong text. Read what agents submit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .catalog import WORKLOG_NAME, build_catalog, worklog_dir
from .catalog.worklog import read_worklog, write_worklog
from .config import Profile
from .errors import LatexAllyError

__all__ = ["Task", "Rejection", "next_tasks", "submit", "validate_description", "AUTHORING_RULES"]

AUTHORING_RULES: tuple[str, ...] = (
    "Write plain words. No LaTeX, no $, no backslashes, no braces.",
    'Never open with "image of", "figure showing", "a diagram of" and the like; '
    "a screen reader already announces that it is a graphic.",
    "Never repeat the caption. It is announced separately, so repeating it makes "
    "the reader say the same sentence twice.",
    "Convey the data and the relationships, never the drawing. Say what the "
    "figure means, not where things sit on the page.",
    "Do not identify anything by colour alone.",
    "For circuits, traverse electrically: source, then branches, then ground. "
    "Name every labelled component and every ground.",
    "For plots, give the axis names and ranges, then the actual data points or "
    "the equation.",
    "For a figure a student sees in the problem, never give away the answer. "
    "Problem and solution builds share one source.",
    "Aim for one sentence. If it needs more than about 200 characters, put the "
    "detail in the `long` section instead.",
)

_BANNED_OPENERS = (
    "image of", "an image of", "picture of", "a picture of", "photo of",
    "photograph of", "figure showing", "figure of", "this figure", "this diagram",
    "diagram showing", "a diagram of", "graphic of", "screenshot of", "shows a",
)
_FILENAME = re.compile(r"\.(?:png|jpg|jpeg|pdf|eps|svg)\b", re.IGNORECASE)
_MARKUP = re.compile(r"[\\${}]|\\\w+")


@dataclass(slots=True)
class Rejection:
    rule: str
    message: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": self.message}


@dataclass(slots=True)
class Task:
    """One unit of description work."""

    id: str
    kind: str
    genre: str
    confidence: str
    call_sites: int
    files: list[str] = field(default_factory=list)
    question: str | None = None
    caption: str | None = None
    image_path: str | None = None
    image_absolute: str | None = None
    inside_solution: bool = False
    missing_image: bool = False
    machine_facts: list[str] = field(default_factory=list)
    still_needed: list[str] = field(default_factory=list)
    data_table: list[list[str]] = field(default_factory=list)
    worklog: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "genre": self.genre,
            "confidence": self.confidence,
            "call_sites": self.call_sites,
            "files": self.files,
            "question": self.question,
            "caption": self.caption,
            "image_path": self.image_path,
            "image_absolute": self.image_absolute,
            "inside_solution": self.inside_solution,
            "missing_image": self.missing_image,
            "machine_facts": self.machine_facts,
            "still_needed": self.still_needed,
            "data_table": self.data_table,
            "worklog": self.worklog,
            "rules": list(AUTHORING_RULES),
            "instructions": (
                "Write a one-sentence description. Use `long` only if the figure "
                "genuinely cannot be conveyed in about 200 characters. Submit with: "
                "latexally agent submit --id <id> --description '<text>'. What "
                "you submit is what ships; there is no review stage."
            ),
        }


def validate_description(text: str, *, caption: str | None = None) -> list[Rejection]:
    """Check a description against the authoring spec.

    Returns the reasons it is unacceptable; an empty list means it passes.
    """
    problems: list[Rejection] = []
    stripped = " ".join(text.split())
    if not stripped:
        problems.append(Rejection("DESCRIPTION-EMPTY", "the description is empty"))
        return problems

    lowered = stripped.lower()
    for opener in _BANNED_OPENERS:
        if lowered.startswith(opener):
            problems.append(
                Rejection(
                    "DESCRIPTION-OPENER",
                    f"do not open with {opener!r}; a reader already says 'graphic'",
                )
            )
            break
    if _MARKUP.search(stripped):
        problems.append(
            Rejection(
                "DESCRIPTION-MARKUP",
                "contains LaTeX markup; /Alt is a plain string and a reader would "
                "announce '$\\frac{1}{2}$' as 'dollar backslash f-r-a-c'",
            )
        )
    if _FILENAME.search(stripped):
        problems.append(Rejection("DESCRIPTION-FILENAME", "a file name is not a description"))
    if caption and " ".join(caption.split()).lower() == lowered:
        problems.append(
            Rejection(
                "DESCRIPTION-CAPTION",
                "identical to the caption, which is announced separately; the reader "
                "would hear the same sentence twice",
            )
        )
    if len(stripped) > 400:
        problems.append(
            Rejection(
                "DESCRIPTION-LENGTH",
                f"{len(stripped)} characters is too long for an atomic /Alt string; "
                "move the detail into `long`",
            )
        )
    return problems



def _files_for(profile: Profile, scope: str) -> list[Path] | None:
    """Every ``.tex`` the scope's assignments actually reach, or None.

    None when the scope names no assignment -- a bare glob rather than a
    document -- which is the signal for the caller to fall back to the glob.
    """
    from .build import source_files_for
    from .discover import discover_assignments

    files: list[Path] = []
    try:
        for assignment in discover_assignments(profile, scope):
            files.extend(source_files_for(assignment, profile))
    except LatexAllyError:
        return None
    return sorted(set(files)) or None


def next_tasks(
    profile: Profile,
    *,
    limit: int = 1,
    genre: str | None = None,
    refresh: bool = False,
    beside: Path | None = None,
    scope: str | None = None,
) -> list[Task]:
    """The highest-value outstanding descriptions.

    Ordered by call-site count: describing a figure cited eight times is eight
    times the benefit for the same effort.

    ``scope`` narrows it to one part of the corpus. Without it this always
    answered for the whole corpus, which under ``--here`` meant standing in
    ``sp26/hw/10`` and being handed a circuit from another semester -- a
    perfectly good next task, and not the one that was asked for.
    """
    # The scope's REAL file set, not a glob of its directory. An assignment's
    # figures overwhelmingly are not in its own folder -- `sp26/hw/10` draws
    # from `questionBank/hw/11` -- so globbing the directory reported "nothing
    # outstanding" for a folder with five undescribed figures. `scan` learned
    # this already; this command had not.
    files = _files_for(profile, scope) if scope else None
    if refresh:
        build_catalog(profile, scope, files=files, write=True, beside=beside)
    result = build_catalog(profile, scope, files=files, write=False, beside=beside)
    root = profile.corpus.root.resolve()

    candidates = [entry for entry in result.entries.values() if entry.needs_human]
    if genre:
        candidates = [entry for entry in candidates if entry.genre == genre]
    candidates.sort(key=lambda entry: (-len(entry.sites), entry.id))

    tasks: list[Task] = []
    for entry in candidates[:limit]:
        absolute = None
        if entry.image_path and entry.sites:
            guess = (root / entry.sites[0][0]).parent / entry.image_path
            absolute = str(guess) if guess.exists() else None
        skeleton = entry.skeleton
        tasks.append(
            Task(
                id=entry.id,
                kind=entry.kind,
                genre=entry.genre,
                confidence=entry.confidence,
                call_sites=len(entry.sites),
                files=[f"{path}:{line}" for path, line in entry.sites[:10]],
                question=entry.question,
                caption=entry.caption,
                image_path=entry.image_path,
                image_absolute=absolute,
                inside_solution=entry.inside_solution,
                missing_image=entry.missing_image,
                machine_facts=(
                    ([skeleton.summary] if skeleton and skeleton.summary else [])
                    + (skeleton.details if skeleton else [])
                ),
                still_needed=skeleton.needs if skeleton else [],
                data_table=[list(row) for row in (skeleton.table if skeleton else [])],
                worklog=str((beside or worklog_dir(profile)) / "…"),
            )
        )
    return tasks


def submit(
    profile: Profile,
    identity: str,
    *,
    description: str,
    long_description: str = "",
    notes: str = "",
    author: str = "agent",
    disposition: str | None = None,
    beside: Path | None = None,
) -> dict:
    """Record a proposed description. Never marks it approved.

    Raises :class:`LatexAllyError` when the id is unknown, and returns the
    rejection list without writing when validation fails.
    """
    # `beside` searches the worklogs scattered through the corpus rather than
    # one output directory -- the layout `--here` and `--edit` produce.
    if beside is not None:
        candidates = sorted(Path(beside).rglob(WORKLOG_NAME))
    else:
        candidates = sorted(worklog_dir(profile).glob("*.md"))
    target: Path | None = None
    for path in candidates:
        if identity in read_worklog(path).entries:
            target = path
            break
    if target is None:
        raise LatexAllyError(
            f"unknown figure id {identity!r}",
            hint="run `latexally scan` first, or check the id from `agent next-task`",
        )

    worklog = read_worklog(target)
    entry = worklog.entries[identity]
    problems = validate_description(description, caption=entry.caption)
    if problems:
        return {
            "accepted": False,
            "id": identity,
            "rejections": [problem.as_dict() for problem in problems],
        }

    entry.description = " ".join(description.split())
    entry.long_description = long_description.strip()
    if notes:
        entry.notes = notes.strip()
    if disposition in ("figure", "artifact"):
        entry.disposition = disposition
    entry.author = author
    entry.updated = date.today().isoformat()

    target.write_text(write_worklog(worklog, scope=worklog.scope), encoding="utf-8")
    return {
        "accepted": True,
        "id": identity,
        "worklog": str(target),
        "note": "written; this text reaches the PDF on the next build, unreviewed",
    }

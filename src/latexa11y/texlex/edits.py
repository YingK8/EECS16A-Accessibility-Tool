"""Byte-faithful, reviewable edits to LaTeX sources.

Design rule: **never reconstruct a file from a parse tree.** Every change is a
``(start, end, replacement)`` splice against the original text, applied
right-to-left so earlier offsets stay valid. That keeps ``git diff`` minimal,
makes every automated change reviewable by a TA, and makes rollback a `git
checkout` rather than a pile of `.bak` files.

Edits are collected, validated as a set, and only then applied. Overlapping
edits raise instead of silently clobbering each other, because a tool that
rewrites thousands of files must fail loudly on ambiguity.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import EditConflictError

__all__ = ["Edit", "EditBuffer"]


@dataclass(frozen=True, slots=True, order=True)
class Edit:
    """A single splice. ``start == end`` is a pure insertion."""

    start: int
    end: int
    replacement: str = field(compare=False)
    reason: str = field(default="", compare=False)
    rule: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid edit span [{self.start}, {self.end})")

    @property
    def is_insertion(self) -> bool:
        return self.start == self.end


class EditBuffer:
    """Accumulates edits for one source file and applies them atomically."""

    __slots__ = ("_edits", "path")

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._edits: list[Edit] = []

    def __len__(self) -> int:
        return len(self._edits)

    def __bool__(self) -> bool:
        return bool(self._edits)

    @property
    def edits(self) -> tuple[Edit, ...]:
        return tuple(sorted(self._edits))

    # ------------------------------------------------------------------ #
    # recording
    # ------------------------------------------------------------------ #

    def replace(
        self, start: int, end: int, replacement: str, *, reason: str = "", rule: str | None = None
    ) -> None:
        self._edits.append(Edit(start, end, replacement, reason, rule))

    def insert(
        self, pos: int, text: str, *, reason: str = "", rule: str | None = None
    ) -> None:
        self._edits.append(Edit(pos, pos, text, reason, rule))

    def wrap(
        self,
        start: int,
        end: int,
        prefix: str,
        suffix: str,
        *,
        reason: str = "",
        rule: str | None = None,
    ) -> None:
        """Insert ``prefix`` before a span and ``suffix`` after it.

        Recorded as two insertions rather than one replacement so the wrapped
        content never passes through Python — a wrapper can therefore never
        corrupt the thing it wraps.
        """
        self.insert(start, prefix, reason=reason, rule=rule)
        self.insert(end, suffix, reason=reason, rule=rule)

    def delete(self, start: int, end: int, *, reason: str = "", rule: str | None = None) -> None:
        self._edits.append(Edit(start, end, "", reason, rule))

    # ------------------------------------------------------------------ #
    # application
    # ------------------------------------------------------------------ #

    def _validate(self, length: int) -> list[Edit]:
        ordered = sorted(self._edits)
        previous: Edit | None = None
        for edit in ordered:
            if edit.end > length:
                raise EditConflictError(
                    f"edit span [{edit.start}, {edit.end}) exceeds source length {length}",
                    hint="the source changed after it was scanned; re-run the scan",
                )
            if previous is not None and edit.start < previous.end:
                raise EditConflictError(
                    f"overlapping edits at [{previous.start}, {previous.end}) "
                    f"and [{edit.start}, {edit.end})"
                    + (f" in {self.path}" if self.path else ""),
                    hint=(
                        f"{previous.rule or 'rule?'} ({previous.reason}) collides with "
                        f"{edit.rule or 'rule?'} ({edit.reason}); "
                        "run the stages separately or narrow one rule's scope"
                    ),
                )
            # Two insertions at the same point are fine and keep their order.
            if previous is None or edit.end > previous.end:
                previous = edit
        return ordered

    def apply(self, original: str) -> str:
        """Return ``original`` with every edit applied. The buffer is unchanged."""
        ordered = self._validate(len(original))
        result = original
        for edit in reversed(ordered):
            result = result[: edit.start] + edit.replacement + result[edit.end :]
        return result

    def diff(self, original: str, *, context: int = 3, label: str | None = None) -> str:
        """Unified diff of the pending edits, for dry-run review."""
        updated = self.apply(original)
        if updated == original:
            return ""
        name = label or (str(self.path) if self.path else "<source>")
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
                n=context,
            )
        )

    def summary(self) -> dict[str, int]:
        """Edit count per rule, for run reports."""
        counts: dict[str, int] = {}
        for edit in self._edits:
            key = edit.rule or "unattributed"
            counts[key] = counts.get(key, 0) + 1
        return counts

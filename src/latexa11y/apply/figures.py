"""Write accessibility markup into ``.tex`` sources.

Two properties matter more than anything else here, because this code edits
thousands of files that people depend on:

**Nothing is ever written from an unapproved description.** A description that
is still a draft, or empty, is skipped. The failure this prevents is specific
and severe: the previous tooling injected ``<<ALT:f-1a2b3c4d>>`` placeholders
into the source, and an unfilled one shipped into the PDF as a real ``/Alt``
string -- which *passes* a naive "every Figure has /Alt" check and passes
veraPDF, producing a silent false claim of conformance.

**The wrapped content never passes through Python.** Wrapping is recorded as two
insertions, before and after the span, so a bug in this module can misplace a
wrapper but can never corrupt the figure it wraps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog.worklog import Entry
from ..config import Profile
from ..errors import LatexA11yError
from ..scan.figures import FigureRef, scan_file
from ..texlex import EditBuffer, TexSource

__all__ = ["ApplyPlan", "plan_file", "apply_scope", "escape_description", "DescriptionRejected"]

_TEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}


class DescriptionRejected(LatexA11yError):
    """A description cannot be written into LaTeX as-is."""


def escape_description(text: str) -> str:
    """Make a description safe to place inside a LaTeX argument.

    Braces are escaped rather than rejected. The previous tool refused any
    description containing ``{`` or ``}``, which in a linear-algebra course
    rules out most natural phrasings; the fix is to escape properly, not to
    forbid the characters.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        raise DescriptionRejected("empty description")
    out: list[str] = []
    for char in collapsed:
        if char == "{":
            out.append(r"\{")
        elif char == "}":
            out.append(r"\}")
        elif char in _TEX_SPECIALS:
            out.append(_TEX_SPECIALS[char])
        else:
            out.append(char)
    return "".join(out)


@dataclass(slots=True)
class ApplyPlan:
    path: Path
    buffer: EditBuffer
    wrapped: int = 0
    artifacts: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    original: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.buffer)

    def diff(self) -> str:
        return self.buffer.diff(self.original, label=self.path.name)

    def write(self, source: TexSource) -> bool:
        if not self.changed:
            return False
        updated = self.buffer.apply(self.original)
        self.path.write_bytes(source.encode(updated))
        return True


def plan_file(path: Path, profile: Profile, entries: dict[str, Entry]) -> ApplyPlan:
    """Compute the edits for one file without touching it."""
    source = TexSource.from_path(path)
    plan = ApplyPlan(path=path, buffer=EditBuffer(path), original=source.text)

    for reference in scan_file(path, profile):
        entry = entries.get(reference.id)
        if entry is None:
            plan.skipped.append((reference.id, "not in any worklog; run scan first"))
            continue
        if reference.already_described:
            continue  # idempotent: a second run changes nothing
        if entry.disposition == "artifact":
            _wrap_decorative(plan, reference)
            plan.artifacts += 1
            continue
        if not entry.is_done:
            plan.skipped.append(
                (reference.id, f"status is {entry.status!r}; only approved text is written")
            )
            continue
        try:
            alt = escape_description(entry.description)
        except DescriptionRejected as exc:
            plan.skipped.append((reference.id, str(exc)))
            continue
        _wrap_described(plan, reference, alt)
        plan.wrapped += 1
    return plan


def _wrap_described(plan: ApplyPlan, reference: FigureRef, alt: str) -> None:
    if reference.is_raster:
        # Inline form: an \includegraphics usually sits inside running text or a
        # centring group, where a display-level environment would change layout.
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\described{{{alt}}}{{%\n",
            "}",
            reason="figure alt text",
            rule="APPLY-DESCRIBED-INLINE",
        )
    else:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\begin{{Described}}{{{alt}}}\n",
            "\n\\end{Described}",
            reason="figure alt text",
            rule="APPLY-DESCRIBED-BLOCK",
        )


def _wrap_decorative(plan: ApplyPlan, reference: FigureRef) -> None:
    plan.buffer.wrap(
        reference.start,
        reference.end,
        "\\begin{Decorative}\n",
        "\n\\end{Decorative}",
        reason="decorative graphic marked as an artifact",
        rule="APPLY-ARTIFACT",
    )


def apply_scope(
    profile: Profile,
    scope: str | None,
    entries: dict[str, Entry],
    *,
    dry_run: bool = True,
) -> list[ApplyPlan]:
    """Plan (and optionally write) edits across a scope."""
    plans: list[ApplyPlan] = []
    for path in profile.iter_files(scope):
        if path.suffix.lower() != ".tex":
            continue
        try:
            plan = plan_file(path, profile, entries)
        except Exception:  # pragma: no cover - one bad file must not stop a sweep
            continue
        if not plan.changed and not plan.skipped:
            continue
        if not dry_run and plan.changed:
            plan.write(TexSource.from_path(path))
        plans.append(plan)
    return plans

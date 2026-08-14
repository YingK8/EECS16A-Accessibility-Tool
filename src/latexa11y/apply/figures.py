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
    placeholders: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: (figure id, line) for each placeholder written, so the run can log
    #: exactly what a person still has to fill in and where it sits.
    pending: list[tuple[str, int]] = field(default_factory=list)
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


#: Marker written for a figure nobody has described yet. It is deliberately one
#: of the strings ``latexa11y-core.sty`` refuses to accept as alt text, so a
#: document still carrying one CANNOT be built in strict mode. See
#: ``_wrap_placeholder`` for why that inversion is the whole safety argument.
PLACEHOLDER = "<<TODO:{id}>>"


def plan_file(
    path: Path,
    profile: Profile,
    entries: dict[str, Entry],
    *,
    placeholders: bool = False,
) -> ApplyPlan:
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
            if placeholders:
                _wrap_placeholder(plan, reference)
                plan.placeholders += 1
                plan.pending.append((reference.id, reference.line))
                continue
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


def _wrap_placeholder(plan: ApplyPlan, reference: FigureRef) -> None:
    """Mark an undescribed figure visibly, in a way that cannot ship.

    The obvious objection to writing placeholders into source is exactly right,
    and it is the failure this package was built to prevent: the previous
    generation of this tooling injected ``<<ALT:f-1a2b3c4d>>`` markers, and an
    unfilled one shipped into a PDF as a real ``/Alt`` string -- which *passes*
    a naive "every Figure has /Alt" check and *passes* veraPDF, producing a
    silent false claim of conformance on material carrying a legal obligation.

    What makes the option safe here is that the guarantee is inverted. The
    marker is one of the strings ``latexa11y-core.sty`` recognises as a
    placeholder, and in strict mode -- the default -- that is a hard LaTeX
    **error**, not a warning. A document with an unfilled placeholder therefore
    does not build at all. The marker cannot reach a PDF, so it cannot lie about
    one; the worst case is a build failure naming the file and the figure.
    """
    marker = PLACEHOLDER.format(id=reference.id)
    if reference.is_raster:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\described{{{marker}}}{{%\n",
            "}",
            reason="undescribed figure marked for a human",
            rule="APPLY-PLACEHOLDER-INLINE",
        )
    else:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\begin{{Described}}{{{marker}}}\n",
            "\n\\end{Described}",
            reason="undescribed figure marked for a human",
            rule="APPLY-PLACEHOLDER-BLOCK",
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
    placeholders: bool = False,
    files: list[Path] | None = None,
) -> list[ApplyPlan]:
    """Plan (and optionally write) edits across a scope.

    ``files`` overrides the scope glob, for the same reason
    :func:`~latexa11y.scan.figures.scan_corpus` accepts one: an assignment's
    figures overwhelmingly are not in its own directory.
    """
    plans: list[ApplyPlan] = []
    candidates = files if files is not None else profile.iter_files(scope)
    for path in candidates:
        if path.suffix.lower() != ".tex":
            continue
        try:
            plan = plan_file(path, profile, entries, placeholders=placeholders)
        except Exception:  # pragma: no cover - one bad file must not stop a sweep
            continue
        if not plan.changed and not plan.skipped:
            continue
        if not dry_run and plan.changed:
            plan.write(TexSource.from_path(path))
        plans.append(plan)
    return plans

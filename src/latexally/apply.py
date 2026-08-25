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

from dataclasses import dataclass, field
from pathlib import Path

from .catalog.worklog import Entry
from .config import Profile
from .errors import LatexAllyError
from .scan import FigureRef, scan_file
from .texlex import EditBuffer, TexSource

__all__ = ["ApplyPlan", "plan_file", "apply_scope", "escape_description", "DescriptionRejected"]

#: Characters LaTeX cannot take literally in an argument, and the words that
#: replace them. Words, NOT escapes: tagpdf writes `alt`/`actualtext` with a
#: byte-level \str_set_convert:Noon (tagpdf-mc-code-generic.sty:392), so a
#: `\%` reaches the PDF as the four characters `\%` and a screen reader says
#: "backslash percent". Escaping is therefore not merely unnecessary here, it
#: is the bug. Spelling `_` as " sub " also matches how the math speech layer
#: already renders subscripts ("R sub 1"), so a figure and a formula that name
#: the same quantity sound the same.
_TEX_SPECIALS = {
    "\\": " ",
    "&": " and ",
    "%": " percent",
    "$": " ",
    "#": " number ",
    "_": " sub ",
    "^": " to the power ",
    "~": " ",
    "{": " ",
    "}": " ",
}


class DescriptionRejected(LatexAllyError):
    """A description cannot be written into LaTeX as-is."""


def escape_description(text: str) -> str:
    r"""Reduce a description to prose that is safe in LaTeX *and* in the PDF.

    The string has to survive two readers with incompatible rules. LaTeX must
    parse it as a macro argument, so a bare ``%`` or ``#`` is impossible. tagpdf
    then writes it into ``/Alt`` byte for byte, with no ``\pdfstringdef``-style
    expansion, so a LaTeX *escape* is impossible too -- ``\%`` arrives at the
    screen reader as "backslash percent".

    Nothing satisfies both except a string containing no specials at all, which
    is what ``docs/ALT_TEXT_SPEC.md`` rule 1 already asks authors for ("Plain
    words only. No ``$``, no backslashes, no braces"). This enforces the rule
    rather than papering over it, so a description that ignores it degrades to
    readable speech instead of shipping visible markup.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        raise DescriptionRejected("empty description")
    out = "".join(_TEX_SPECIALS.get(char, char) for char in collapsed)
    # The substitutions introduce spacing of their own ("R_1" -> "R sub 1"),
    # and doubled or trailing space is audible as a pause.
    spoken = " ".join(out.split())
    if not spoken:
        raise DescriptionRejected("description is only punctuation")
    return spoken


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
#: of the strings ``latexally-core.sty`` refuses to accept as alt text, so a
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
        _wrap_described(plan, reference, alt, entry.long_description)
        plan.wrapped += 1
    return plan


def _continues_line(plan: ApplyPlan, reference: FigureRef) -> bool:
    r"""True when the figure's own line carries content after it.

    The block form opens with ``\par``, so it needs vertical mode. A figure
    whose line continues is not in vertical mode: the usual case is a trailing
    ``\\`` ending a `center` line or a tabular row, and ``\end{Described}``
    before it strands that ``\\`` with no line to end -- a hard build failure.
    The inline ``\described`` is an ``\mbox``; it leaves the surrounding mode,
    and the ``\\``, exactly as the author wrote them.
    """
    line_end = plan.original.find("\n", reference.end)
    tail = plan.original[reference.end : len(plan.original) if line_end < 0 else line_end]
    return bool(tail.strip())


def _wrap_described(
    plan: ApplyPlan, reference: FigureRef, alt: str, long: str = ""
) -> None:
    continues = _continues_line(plan, reference)

    # The long description is ordinary body text placed after the figure, so it
    # needs vertical mode just as the block form does. A figure sharing its line
    # has nowhere safe to put it; say so rather than emit a \par into a tabular
    # cell.
    tail = ""
    if long.strip():
        if continues:
            plan.skipped.append(
                (reference.id, "long description not written: figure shares its line")
            )
        else:
            tail = f"\n\\LongDescription{{{escape_description(long)}}}"

    if reference.is_raster or continues:
        # Inline form: an \includegraphics usually sits inside running text or a
        # centring group, where a display-level environment would change layout.
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\described{{{alt}}}{{%\n",
            "}" + tail,
            reason="figure alt text",
            rule="APPLY-DESCRIBED-INLINE",
        )
    else:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\begin{{Described}}{{{alt}}}\n",
            "\n\\end{Described}" + tail,
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
    marker is one of the strings ``latexally-core.sty`` recognises as a
    placeholder, and in strict mode -- the default -- that is a hard LaTeX
    **error**, not a warning. A document with an unfilled placeholder therefore
    does not build at all. The marker cannot reach a PDF, so it cannot lie about
    one; the worst case is a build failure naming the file and the figure.
    """
    marker = PLACEHOLDER.format(id=reference.id)
    if reference.is_raster or _continues_line(plan, reference):
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
    :func:`~latexally.scan.scan_corpus` accepts one: an assignment's
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

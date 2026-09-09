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

from .catalog.worklog import Entry
from .config import Profile
from .errors import LatexAllyError
from .scan import FigureRef, is_artifact_listed, scan_file
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
    captioned: int = 0
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
    captions: bool = False,
) -> ApplyPlan:
    """Compute the edits for one file without touching it."""
    source = TexSource.from_path(path)
    plan = ApplyPlan(path=path, buffer=EditBuffer(path), original=source.text)

    for reference in scan_file(path, profile):
        # Captions first, and deliberately before the worklog lookup: a caption
        # is not a description and does not need one to have been written, so
        # `caption` mode works with no worklog on disk at all.
        if captions and _add_caption(plan, reference, source.text):
            plan.captioned += 1
        # Before the worklog, because a decorative graphic never gets an entry
        # to look up: the worklog carries `alt_text` and nothing else, so there
        # is nowhere in it to say "this one is an ornament". The profile is
        # where that decision is recorded, and it is read straight from there.
        if is_artifact_listed(reference, profile) and not reference.already_described:
            _wrap_decorative(plan, reference)
            plan.artifacts += 1
            continue
        entry = entries.get(reference.id)
        if entry is None:
            if not captions:
                plan.skipped.append(
                    (reference.id, "not in any worklog; run scan first")
                )
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
                (reference.id, "no description written yet")
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


def _indent_of(plan: ApplyPlan, reference: FigureRef) -> str:
    r"""The whitespace the figure's own line starts with, or ``""``.

    The wrapper opens with a newline, so without this the wrapped
    ``\begin{tikzpicture}`` and the closing ``\end{Described}`` both land in
    column 0 while the figure around them stays indented -- correct LaTeX that
    reads as damage in a diff, and the author has to re-indent it by hand.

    Interior lines are left alone deliberately: they keep the indentation the
    author gave them, and reindenting them would mean passing the wrapped
    content through Python, which ``EditBuffer.wrap`` exists not to do. So the
    wrapper matches the figure's own column rather than nesting one level in.

    Empty when the figure does not start its line -- there is text before it,
    and its leading whitespace is that text's, not the figure's.
    """
    line_start = plan.original.rfind("\n", 0, reference.start) + 1
    lead = plan.original[line_start : reference.start]
    return lead if not lead.strip() else ""


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
    indent = _indent_of(plan, reference)

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
            tail = f"\n{indent}\\LongDescription{{{escape_description(long)}}}"

    if reference.is_raster or continues:
        # Inline form: an \includegraphics usually sits inside running text or a
        # centring group, where a display-level environment would change layout.
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\described{{{alt}}}{{%\n{indent}",
            "}" + tail,
            reason="figure alt text",
            rule="APPLY-DESCRIBED-INLINE",
        )
    else:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\begin{{Described}}{{{alt}}}\n{indent}",
            f"\n{indent}\\end{{Described}}" + tail,
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
    indent = _indent_of(plan, reference)
    if reference.is_raster or _continues_line(plan, reference):
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\described{{{marker}}}{{%\n{indent}",
            "}",
            reason="undescribed figure marked for a human",
            rule="APPLY-PLACEHOLDER-INLINE",
        )
    else:
        plan.buffer.wrap(
            reference.start,
            reference.end,
            f"\\begin{{Described}}{{{marker}}}\n{indent}",
            f"\n{indent}\\end{{Described}}",
            reason="undescribed figure marked for a human",
            rule="APPLY-PLACEHOLDER-BLOCK",
        )


#: Float environments `\caption` is legal inside. A caption anywhere else is a
#: LaTeX error ("\caption outside float"), so a figure with none of these
#: already around it gets one added -- see `_add_caption`.
_FLOATS = ("figure", "figure*", "table", "table*")

_CAPTION_CALL = re.compile(r"\\caption\*?\s*(?:\[[^\]]*\])?\s*\{")


def _enclosing_float(text: str, reference: FigureRef) -> tuple[int, int] | None:
    r"""``(begin_start, end_start)`` of the float wrapping the figure, or None.

    Scans outward from the graphic rather than parsing the file: the nearest
    unclosed ``\begin{figure}`` before it, and the ``\end{figure}`` that closes
    that one. Nested floats are not legal LaTeX, so depth counting is enough.
    """
    for name in _FLOATS:
        opener = f"\\begin{{{name}}}"
        closer = f"\\end{{{name}}}"
        start = text.rfind(opener, 0, reference.start)
        if start == -1:
            continue
        end = text.find(closer, reference.end)
        if end == -1:
            continue
        # Another float of the same kind opening and closing in between would
        # mean this one is not the enclosing environment.
        if text.count(opener, start + len(opener), reference.start) != text.count(
            closer, start, reference.start
        ):
            continue
        return start, end
    return None


#: pgfplots computes a 3D `axis`'s box from its declared ranges through the
#: view transform when `height` is unset, and in this corpus that computed box
#: is far taller than what actually renders -- confirmed by bisection: loading
#: both `algorithm` and `algpseudocode` (for pseudocode elsewhere in the same
#: document) is what triggers it, and it reproduces on a plain `\begin{axis}`
#: with no other figure content involved. An explicit `height` sidesteps the
#: bad computation entirely, regardless of the value -- but a flat constant
#: looks disproportionate on an axis whose own `width` differs from the one it
#: was tuned against, so this scales to whatever `width` the axis itself
#: already declares (``\linewidth`` when it declares none) rather than
#: reusing one number everywhere.
#:
#: The proportional value is written as ``0.4\textwidth``, not
#: ``\dimexpr 0.4*\textwidth\relax``: the latter compiles fine in isolation but
#: hard-fails ("Illegal unit of measure") once actually placed in this
#: corpus's real preamble -- **[verified]** on ``fa26/dis/02A/sol02A.tex``, a
#: `\dimexpr`/pgfmath parsing conflict somewhere in that much larger package
#: set. ``<factor>\macro`` is the standard TikZ/PGF idiom for a fraction of
#: another length and needs no eTeX primitive at all, so it is used
#: unconditionally when the axis's own width is itself a macro (``\textwidth``,
#: ``\linewidth``, the only shapes ever seen here) -- an explicit unit-bearing
#: width (``8cm``) instead goes through PGF's own braced arithmetic, which
#: does not touch `\dimexpr` either.
#:
#: 0.6, not 0.4: measured directly against a real render of
#: ``fa26/dis/02A/sol02A.tex`` -- **[verified]** -- 0.4 read as visibly
#: squashed once ``trim axis left/right`` (below) was also in place.
_AXIS_HEIGHT_FACTOR = "0.6"
_AXIS_OPEN = re.compile(r"\\begin\{axis\}\s*\[")
_AXIS_HEIGHT_KEY = re.compile(r"\bheight\s*=")
_AXIS_WIDTH_KEY = re.compile(r"\bwidth\s*=\s*([^,\]]+)")
#: The exact shapes this function itself generates for `height=`'s value --
#: a bare coefficient prefix on a macro, or a braced `coefficient*value`.
#: Matching a driver an earlier run of this tool already gave a height (with
#: whatever the factor was then) lets that value track a changed
#: `_AXIS_HEIGHT_FACTOR` on the next run, the same migration
#: `_fix_uncolored_captions_in_wrappers` needs for its own stale patches. An
#: author's own `height=4cm` matches neither shape and is still never touched.
_TOOL_HEIGHT = re.compile(r"height=((?:\d+(?:\.\d+)?\\[A-Za-z]+)|(?:\{\d+(?:\.\d+)?\*[^}]*\}))")

#: A 3D `axis` with `axis lines=middle` draws its lines out to the full
#: declared xmin/xmax/ymin/ymax, not to where the data actually ends --
#: `q_span_basics.tex`'s v1/v2 vectors reach roughly x=2 inside an
#: xmin=-2/xmax=5 range, for instance. `\centering` correctly centers the
#: *bounding box* (checkable independently of this: the caption directly
#: below sits exactly on the page's true center), but that box includes the
#: mostly-empty axis-line extent past the data, so the visibly inked content
#: reads as off-center even though the box is not. `trim axis left/right` is
#: pgfplots' own answer to exactly this -- **[verified]** moved the measured
#: midpoint of the visible content markedly closer to the page's true center
#: on a real render, not just in theory.
_TIKZ_OPEN = re.compile(r"\\begin\{tikzpicture\}(\s*\[)?")
_TRIM_AXIS_KEY = re.compile(r"\btrim axis (?:left|right)\b")


def _matching_bracket(text: str, open_pos: int) -> int | None:
    """Index of the ``]`` matching the ``[`` at ``open_pos``, or None."""
    depth = 0
    for index in range(open_pos, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _ensure_axis_height(plan: ApplyPlan, reference: FigureRef) -> None:
    r"""Give every ``\begin{axis}[...]`` in this figure an explicit height,
    proportional to whatever width it already declares.

    An author's own `height` is never overridden -- but a height this same
    function generated on an earlier run, with whatever `_AXIS_HEIGHT_FACTOR`
    was then, is upgraded to the current one rather than read as the
    author's, the same migration `_fix_uncolored_captions_in_wrappers` needs
    for its own stale patches. Safe to call on any figure: a plot without
    pgfplots' `axis` environment (a plain `tikzpicture`, `circuitikz`,
    `includegraphics`) simply has nothing for ``_AXIS_OPEN`` to match.
    """
    text = plan.original
    for match in _AXIS_OPEN.finditer(text, reference.start, reference.end):
        open_bracket = match.end() - 1
        close_bracket = _matching_bracket(text, open_bracket)
        options = text[open_bracket:close_bracket] if close_bracket else ""
        width_match = _AXIS_WIDTH_KEY.search(options)
        width = width_match.group(1).strip() if width_match else "\\linewidth"
        if width.startswith("\\"):
            height = f"{_AXIS_HEIGHT_FACTOR}{width}"
        else:
            height = f"{{{_AXIS_HEIGHT_FACTOR}*{width}}}"

        stale = _TOOL_HEIGHT.search(options)
        if stale is not None:
            if stale.group(1) == height:
                continue  # already exactly this
            start = open_bracket + stale.start()
            end = open_bracket + stale.end()
            plan.buffer.replace(
                start,
                end,
                f"height={height}",
                reason="pgfplots axis height upgraded to the current factor",
                rule="APPLY-AXIS-HEIGHT",
            )
            continue
        if _AXIS_HEIGHT_KEY.search(options):
            continue  # an author's own height, in a shape this tool never writes
        plan.buffer.insert(
            match.end(),
            f"height={height}, ",
            reason=(
                "pgfplots axis height set explicitly, proportional to its own "
                "width, so its caption sits close"
            ),
            rule="APPLY-AXIS-HEIGHT",
        )


def _ensure_axis_trim(plan: ApplyPlan, reference: FigureRef) -> None:
    r"""Trim a pgfplots `axis`'s unused line-extent from its `tikzpicture`,
    so `\centering` centers the visibly inked content, not empty axis line.

    Only touches a `tikzpicture` that actually contains a pgfplots `axis` --
    `trim axis left`/`right` are pgfplots' own keys, registered only once
    pgfplots is loaded, so adding them to a plain `tikzpicture` (a node
    diagram, a `circuitikz`) with no `axis` inside risks an unknown-key error
    for a package that was never asked to be there.
    """
    text = plan.original
    if not _AXIS_OPEN.search(text, reference.start, reference.end):
        return
    match = _TIKZ_OPEN.match(text, reference.start)
    if match is None:
        return
    if match.group(1):  # already has its own [...] options
        open_bracket = match.end() - 1
        close_bracket = _matching_bracket(text, open_bracket)
        options = text[open_bracket:close_bracket] if close_bracket else ""
        if _TRIM_AXIS_KEY.search(options):
            return
        insertion = "trim axis left, trim axis right, "
    else:
        insertion = "[trim axis left, trim axis right]"
    plan.buffer.insert(
        match.end(),
        insertion,
        reason="pgfplots axis trimmed so \\centering centers the inked content",
        rule="APPLY-AXIS-TRIM",
    )


def _add_caption(plan: ApplyPlan, reference: FigureRef, text: str) -> bool:
    r"""Give a figure with no caption one for an author to fill in.

    Returns whether an edit was recorded. Unlike the alt-text marker, this one
    is *read on the page*: the point of choosing captions over a silent
    ``/Alt`` placeholder is that an unfilled marker is impossible to miss in
    the PDF -- the same guarantee strict mode used to buy with a build failure,
    bought instead with ink.

    A figure with no enclosing float gets one -- ``\caption`` is a hard error
    outside a float, so giving every undescribed figure a caption (the
    ``caption`` mode's whole promise, see ``AltChoice``) means giving the ones
    with nowhere to put it somewhere to put it.
    """
    # NOT `reference.caption`: the scan attributes any `\caption` within 400
    # characters *after* the graphic, so in a file of back-to-back figures the
    # first one inherits the second one's caption. Good enough as context for a
    # describer, useless as "does this figure have a caption" -- which is what
    # the bounded search below actually answers.
    span = _enclosing_float(text, reference)
    if span is None:
        return _float_and_caption(plan, reference)
    begin, end = span
    # Independent of the caption check below on purpose: a figure captioned by
    # an older run of this tool -- before `_ensure_axis_height`/`_ensure_axis_trim`
    # existed -- must still get them fixed on a later run, not be skipped as
    # "already done" because idempotency here is about the caption only.
    _ensure_axis_height(plan, reference)
    _ensure_axis_trim(plan, reference)
    if _CAPTION_CALL.search(text, begin, end):
        return False  # idempotent: a captioned float is left alone
    caption = f"\\caption{{{PLACEHOLDER.format(id=reference.id)}}}"
    # The graphic's own indentation, not the `\end{figure}` line's: LaTeX style
    # puts the caption with the float's body, and a float whose `\end` sits at
    # column 0 would otherwise get a caption at column 0 too.
    indent = _indent_of(plan, reference)
    line_start = text.rfind("\n", 0, end) + 1
    before_end = text[line_start:end]
    if before_end.strip():
        # `\end{figure}` shares its line with content. Open a line for the
        # caption rather than appending to whatever that content is.
        plan.buffer.insert(
            end,
            f"{caption}\n{indent}",
            reason="figure caption marked for a human",
            rule="APPLY-CAPTION",
        )
    else:
        plan.buffer.insert(
            line_start,
            f"{indent}{caption}\n",
            reason="figure caption marked for a human",
            rule="APPLY-CAPTION",
        )
    return True


def _float_and_caption(plan: ApplyPlan, reference: FigureRef) -> bool:
    r"""Wrap a floatless figure in ``figure`` so a caption can legally sit on it.

    Only for a figure that already stands on its own line. An inline
    ``\includegraphics`` mid-sentence cannot be floated without breaking the
    sentence around it -- the same distinction ``_wrap_described`` and
    ``_wrap_placeholder`` already draw between their block and inline forms --
    so that case is left with a caption unwritten and a reason logged, same as
    before this existed.
    """
    if reference.is_raster or _continues_line(plan, reference):
        plan.skipped.append(
            (
                reference.id,
                "inline in running text; floating it would break the "
                "sentence around it, so no caption was added",
            )
        )
        return False
    _ensure_axis_height(plan, reference)
    _ensure_axis_trim(plan, reference)
    caption = f"\\caption{{{PLACEHOLDER.format(id=reference.id)}}}"
    indent = _indent_of(plan, reference)
    # `\centering`, matching how every hand-written `\begin{figure}` in this
    # corpus already sets one up -- an auto-wrapped one left-aligned would read
    # as damage, not as a figure some author placed there deliberately.
    plan.buffer.wrap(
        reference.start,
        reference.end,
        f"\\begin{{figure}}[h!]\n{indent}\\centering\n{indent}",
        f"\n{indent}{caption}\n{indent}\\end{{figure}}",
        reason="figure floated and captioned so the caption compiles",
        rule="APPLY-CAPTION-FLOAT",
    )
    return True


def _wrap_decorative(plan: ApplyPlan, reference: FigureRef) -> None:
    indent = _indent_of(plan, reference)
    plan.buffer.wrap(
        reference.start,
        reference.end,
        f"\\begin{{Decorative}}\n{indent}",
        f"\n{indent}\\end{{Decorative}}",
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
    captions: bool = False,
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
            plan = plan_file(
                path, profile, entries, placeholders=placeholders, captions=captions
            )
        except Exception:  # pragma: no cover - one bad file must not stop a sweep
            continue
        if not plan.changed and not plan.skipped:
            continue
        if not dry_run and plan.changed:
            plan.write(TexSource.from_path(path))
        plans.append(plan)
    return plans

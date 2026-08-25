r"""Fix the four source constructs that LaTeX's own tagging cannot compile.

These are not accessibility findings. Each one is a piece of LaTeX that
pdfLaTeX has always accepted and that `\DocumentMetadata{testphase={tagpdf}}`
rejects -- so they are reported by ``doctor``'s tagging tier, not by ``check``,
and they are fixed here.

The rewrites are read out of the same regexes ``check.rules`` detects with,
imported rather than restated. Those patterns carry seventy lines of
measurement notes explaining why each is shaped the way it is; a second copy
here would drift from the evidence within one semester.

What the corpus actually contains
---------------------------------

Every count below was measured over 17,677 ``.tex`` files, and two of them
contradict the hint the detecting rule prints:

============  =====  ================================================
Rule          Sites  Fix
============  =====  ================================================
ALLY-SRC-040    667  ``\AllyEnumLabel`` before the list
ALLY-SRC-041    357  invert the nesting; **do not** delete the array
ALLY-SRC-042    306  ``\mbox{}`` before the break
ALLY-SRC-043     30  only 2 are mechanisable at all
============  =====  ================================================

``ALLY-SRC-041``'s hint says "the inner array is almost always there for
column alignment the matrix already provides; delete it and keep the matrix."
**257 of the 357 carry a ``|`` in their column spec.** They are augmented
matrices, and on a linear-algebra course the bar is the difference between a
system of equations and a 2 by 3 matrix. Deleting the array deletes the bar. So
the nesting is inverted instead -- ``\begin{bmatrix}\begin{array}{cc|c}``
becomes ``\left[\begin{array}{cc|c}`` -- which keeps the bar, keeps the
brackets, and removes the nested table that tagging chokes on. It also fixes
the MathML: ``latex2mathml`` reads the nested form as three rows, not two.

``ALLY-SRC-043``'s hint says "close it with ``\)`` to match the opening
delimiter." That is right for 2 of the 30 sites and actively harmful for the
other 28, which are not mismatched formulas at all -- they are ``\(`` written
where a literal ``(`` was meant::

    \(1) put 4 resistors in series, and let it be $R$
    \(\textit{Hint: Similar to the last part, express $x$ ...
    \(http://inst.eecs.berkeley.edu/~ee16a/sp19/hw-practice).

Closing those with ``\)`` would pull whole paragraphs into math mode. What the
author meant is a question only the author can answer, so they stay
report-only, and :data:`SAFE_INLINE_MATH` is deliberately narrow enough to
prove it: no whitespace and no ``)`` between the delimiters.

What it costs on the page
-------------------------

A fix that repaginates is not a fix. Measured with :func:`build.compare_pdfs`
at 110 dpi, untagged original against untagged rewritten, so the number is the
rewrite alone and not the 2.6% that enabling tagging costs on any route:

======================================  ==========  ========
File                                    Sites       Diff
======================================  ==========  ========
``hw/12/q_romeo_juliet_simplified``     041 x10     0.0000%
``q_ct``, ``q_ct_complex_exp_potpourri``040 x4 each 0.0000%
``q_syllabus``                          040 x5      0.0048%
======================================  ==========  ========

The 0.0048% is enumitem: ``[label=...]`` sizes the label box from the label,
while ``\labelenumN`` uses the standard ``\labelwidth``, so one list indents by
a hair. It is two orders of magnitude below the 0.42% that deleting a line
break was measured to cost, and below the 2.596% that tagging costs anyway.

Nothing here writes to the corpus by itself. ``build`` rewrites the output
mirror; ``doctor --tagging --fix --write`` is the only path that touches the
source tree, and it is guarded by a clean git worktree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .check.rules import (
    _ARRAY_IN_MATRIX,
    _BLANK_IN_DISPLAY_BRACKET,
    _BLANK_IN_DISPLAY_ENV,
    _BREAK_AFTER_DISPLAY,
    _CONTENT_FREE_MATH,
    _ENUMITEM_LABEL,
    _ENUMITEM_STAR_MARGIN,
    _MISMATCHED_INLINE_MATH,
    _SETLIST_LABEL,
    inline_math_spans,
)
from .texlex import EditBuffer, TexSource

__all__ = [
    "FIXED_BY_TAGGING",
    "RULES",
    "RewritePlan",
    "Skipped",
    "plan_rewrites",
    "rewrite_files",
]

#: Rules the LaTeX kernel itself now handles once `tagging=on` is available, so
#: rewriting them is churn rather than repair.
#:
#: Measured on the kernel that introduced the switch, against the same document
#: untagged, at list depths 1, 2 and 3: `[label=(\roman*)]` renders identically
#: and the structure tree is right too -- `L`, `LI`, `Lbl`, `LBody` all present
#: and correctly nested. So `\AllyEnumLabel` buys nothing there.
#:
#: **ALLY-SRC-041 is deliberately NOT in this set even though LaTeX compiles it
#: now.** The rewrite is not only about compiling: `latex2mathml` reads the
#: nested form as a 1x1 table wrapping a 2x3 one, and MathCAT then says "the 1
#: by 1 matrix with entry the 2 by 3 augmented matrix". Inverting the nesting is
#: what makes the `/Alt` say what the matrix is.
FIXED_BY_TAGGING = frozenset({"ALLY-SRC-040"})

#: The rules this module can fix, in the order a report should list them.
RULES = (
    "ALLY-SRC-040",
    "ALLY-SRC-041",
    "ALLY-SRC-042",
    "ALLY-SRC-043",
    "ALLY-SRC-045",
    "ALLY-SRC-046",
)

#: Matrix environment -> the delimiters ``\left``/``\right`` need to reproduce
#: it. ``matrix`` itself has none, and ``\left.`` is how TeX spells that.
_MATRIX_DELIMITERS = {
    "matrix": (".", "."),
    "pmatrix": ("(", ")"),
    "bmatrix": ("[", "]"),
    "Bmatrix": (r"\{", r"\}"),
    "vmatrix": ("|", "|"),
    "Vmatrix": (r"\|", r"\|"),
}

#: The body of a `\(...$` that is safe to reclose. Narrow on purpose: a formula
#: has no spaces at this size and no bare `)`, and everything else in this
#: corpus turned out to be prose the author never meant to set in math.
SAFE_INLINE_MATH = re.compile(r"\A[^\s)]*\Z")

#: A counter macro written with enumitem's `*`, which resolves to the wrong
#: counter under tagging. The `*` is what has to go; which counter replaces it
#: is a question only LaTeX can answer, at the depth the list actually opens.
_STARRED_COUNTER = re.compile(r"\\(arabic|roman|Roman|alph|Alph)\s*\*")

#: One `key=value` of an enumitem option list, at top level. Commas inside
#: `{...}` belong to the value -- `label={Step \arabic*.}` is a real corpus
#: shape -- so they must not split.
_OPTION_SEPARATOR = re.compile(r",")


@dataclass(frozen=True, slots=True)
class Skipped:
    """A site that was found and deliberately not rewritten."""

    rule: str
    line: int
    reason: str


@dataclass(slots=True)
class RewritePlan:
    """Every rewrite for one file, plus what was left alone and why."""

    path: Path
    source: TexSource
    buffer: EditBuffer
    skipped: list[Skipped] = field(default_factory=list)
    #: ``rule -> sites fixed``. Counted as sites rather than derived from the
    #: buffer, because two of the four rewrites are a *pair* of edits at one
    #: site -- ALLY-SRC-041 splices both ends of the matrix, ALLY-SRC-040
    #: inserts a label and deletes an option. Counting edits reports this
    #: corpus's 357 augmented matrices as 714 of them.
    sites: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.buffer)

    def _fixed(self, rule: str) -> None:
        self.sites[rule] = self.sites.get(rule, 0) + 1

    def counts(self) -> dict[str, int]:
        """``rule -> number of sites fixed``."""
        return dict(self.sites)

    def result(self) -> str:
        return self.buffer.apply(self.source.text)

    def diff(self, *, context: int = 3) -> str:
        return self.buffer.diff(self.source.text, context=context, label=self.path.name)

    def write(self) -> bool:
        """Apply to disk. Returns whether anything changed."""
        if not self.changed:
            return False
        self.path.write_bytes(self.source.encode(self.result()))
        return True


# ---------------------------------------------------------------------- #
# ALLY-SRC-042 -- a line break with no line to end
# ---------------------------------------------------------------------- #


def _break_after_display(plan: RewritePlan) -> None:
    r"""``\end{align*}\\`` -> ``\end{align*}\mbox{}\\``.

    Measured on sp26/hw/3: ``\mbox{}`` moves 0.002% of pixels, while deleting
    the break -- the other obvious fix -- removes a blank line and repaginates
    0.42% of them.
    """
    for match in _BREAK_AFTER_DISPLAY.finditer(plan.source.masked):
        plan.buffer.insert(
            match.start("brk"),
            r"\mbox{}",
            reason="give the break a line to end",
            rule="ALLY-SRC-042",
        )
        plan._fixed("ALLY-SRC-042")


# ---------------------------------------------------------------------- #
# ALLY-SRC-043 -- inline math opened with \( and closed with $
# ---------------------------------------------------------------------- #


def _mismatched_inline_math(plan: RewritePlan) -> None:
    r"""``\(b_j$`` -> ``\(b_j\)``, and nothing else.

    See the module docstring: 28 of this corpus's 30 sites are a literal ``(``
    written as ``\(``, where reclosing the math would swallow the paragraph.
    """
    source = plan.source
    for match in _MISMATCHED_INLINE_MATH.finditer(source.masked):
        body = source.text[match.start() + 2 : match.end() - 1]
        if SAFE_INLINE_MATH.match(body):
            plan.buffer.replace(
                match.end() - 1,
                match.end(),
                r"\)",
                reason="close inline math with the delimiter it was opened with",
                rule="ALLY-SRC-043",
            )
            plan._fixed("ALLY-SRC-043")
            continue
        plan.skipped.append(
            Skipped(
                "ALLY-SRC-043",
                source.line_of(match.start()),
                "the text after \\( reads as prose, not a formula; reclosing it "
                "with \\) would set the paragraph in math mode. Decide whether "
                "the author meant a literal ( or a formula",
            )
        )


# ---------------------------------------------------------------------- #
# ALLY-SRC-041 -- array nested inside a matrix
# ---------------------------------------------------------------------- #


def _array_in_matrix(plan: RewritePlan) -> None:
    r"""``\begin{bmatrix}\begin{array}{cc|c}`` -> ``\left[\begin{array}{cc|c}``.

    The array is kept because its column spec is where the augmentation bar
    lives. What goes is the nested tabular environment, which is what the table
    tagging module fails on with "Misplaced \crcr".
    """
    source = plan.source
    if not _ARRAY_IN_MATRIX.search(source.masked):
        return
    arrays = source.environments({"array", "tabular"})
    for matrix in source.environments(set(_MATRIX_DELIMITERS)):
        inner = [a for a in arrays if matrix.body_start <= a.start and a.end <= matrix.body_end]
        if not inner:
            continue
        line = source.line_of(matrix.start)
        if len(inner) > 1:
            plan.skipped.append(
                Skipped("ALLY-SRC-041", line, "more than one table inside the matrix"))
            continue
        array = inner[0]
        if array.name == "tabular":
            # `\left...\right` is a math-mode construct and `tabular` is not.
            # Zero corpus sites, so this is a guard rather than a case.
            plan.skipped.append(
                Skipped("ALLY-SRC-041", line, "tabular, not array; it is not math mode"))
            continue
        if source.text[matrix.body_start : array.start].strip():
            plan.skipped.append(
                Skipped("ALLY-SRC-041", line, "the matrix holds more than the array"))
            continue
        if source.text[array.end : matrix.body_end].strip():
            plan.skipped.append(
                Skipped("ALLY-SRC-041", line, "the matrix holds more than the array"))
            continue
        if source.match_optional(array.body_start) is not None:
            # `\left...\right` centres on the maths axis; `[t]` does not.
            plan.skipped.append(
                Skipped("ALLY-SRC-041", line, "the array carries a [t]/[b] alignment"))
            continue
        left, right = _MATRIX_DELIMITERS[matrix.name]
        plan.buffer.replace(
            matrix.start,
            matrix.body_start,
            f"\\left{left}",
            reason="keep the delimiters, drop the nested table",
            rule="ALLY-SRC-041",
        )
        plan.buffer.replace(
            matrix.body_end,
            matrix.end,
            f"\\right{right}",
            reason="keep the delimiters, drop the nested table",
            rule="ALLY-SRC-041",
        )
        plan._fixed("ALLY-SRC-041")


# ---------------------------------------------------------------------- #
# ALLY-SRC-040 -- enumitem label options
# ---------------------------------------------------------------------- #


def _split_options(text: str) -> list[tuple[int, int]]:
    """Top-level ``key=value`` spans of an enumitem option list.

    Commas inside braces belong to the value: ``label={Step \\arabic*.}`` and
    ``itemsep=20ex, ,label=\\alph*)`` are both real corpus shapes.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            spans.append((start, index))
            start = index + 1
        index += 1
    spans.append((start, len(text)))
    return spans


def _enumitem_label(plan: RewritePlan) -> None:
    r"""Move the label out of the option list and onto ``\AllyEnumLabel``.

    Why not ``\renewcommand{\labelenumi}``, which is what the rule's hint says:
    483 of the 667 sites are at least two ``enumerate``\ s deep *within their
    own file*, and that is a lower bound, because every question file is
    ``\input`` inside the driver's own ``\begin{enumerate}[series=qn]``. The
    depth is not knowable from the source, so ``\AllyEnumLabel`` asks LaTeX for
    it at the moment the list opens.
    """
    source = plan.source
    for match in _ENUMITEM_LABEL.finditer(source.masked):
        line = source.line_of(match.start())
        begin = re.match(r"\\begin\s*\{\w+\}", source.text[match.start() :])
        if begin is None:  # pragma: no cover - the pattern guarantees it
            continue
        options = source.match_optional(match.start() + begin.end())
        if options is None:  # pragma: no cover - likewise
            continue
        inner = source.text[options.inner]
        label: tuple[int, int] | None = None
        for start, end in _split_options(inner):
            if re.match(r"\s*label\s*=", inner[start:end]):
                label = (start, end)
                break
        if label is None:
            plan.skipped.append(
                Skipped("ALLY-SRC-040", line, "the counter is not in a label= key"))
            continue
        spec = inner[label[0] : label[1]].split("=", 1)[1].strip()
        if not _STARRED_COUNTER.search(spec):
            # A non-starred `\roman{enumi}` names its counter outright, and at
            # any depth but the first that name is already wrong. Rewriting it
            # would preserve the bug in a new place.
            plan.skipped.append(
                Skipped(
                    "ALLY-SRC-040",
                    line,
                    "the label names a counter explicitly; check which depth it "
                    "is meant for before changing it",
                )
            )
            continue
        # `{Step \arabic*.}` -- enumitem strips one level of braces, so the
        # label the list actually gets is the inside.
        if spec.startswith("{") and spec.endswith("}"):
            spec = spec[1:-1]
        body = _STARRED_COUNTER.sub(r"\\\1{\\allyenum}", spec)
        plan.buffer.insert(
            match.start(),
            f"\\AllyEnumLabel{{{body}}}%\n{_indent_before(source.text, match.start())}",
            reason="set the label at the depth the list actually opens",
            rule="ALLY-SRC-040",
        )
        _delete_option(plan, options.inner_start, inner, label)
        plan._fixed("ALLY-SRC-040")


def _indent_before(text: str, pos: int) -> str:
    """The whitespace between ``pos`` and the start of its line.

    The label insert pushes ``\\begin{enumerate}`` onto a new line. Without
    reproducing the indent, a nested list loses six spaces and the reviewer
    reads a whitespace change instead of the fix.
    """
    line_start = text.rfind("\n", 0, pos) + 1
    prefix = text[line_start:pos]
    return prefix if not prefix.strip() else ""


def _delete_option(plan: RewritePlan, base: int, inner: str, span: tuple[int, int]) -> None:
    """Remove one key from an option list, and its separating comma."""
    start, end = span
    # Take the comma that separated this key from its neighbour, whichever side
    # it is on, so `[label=\roman*, itemsep=1ex]` does not become `[, itemsep=1ex]`.
    if end < len(inner):
        end += 1
    elif start > 0:
        start -= 1
    if inner[:start].strip() or inner[end:].strip():
        plan.buffer.delete(
            base + start,
            base + end,
            reason="the label moved to \\AllyEnumLabel",
            rule="ALLY-SRC-040",
        )
        return
    # Nothing else in the group: take the brackets with it.
    plan.buffer.delete(
        base - 1,
        base + len(inner) + 1,
        reason="the label moved to \\AllyEnumLabel",
        rule="ALLY-SRC-040",
    )


def _starred_lengths(plan: RewritePlan) -> None:
    r"""``leftmargin=*`` and friends: reported, never rewritten.

    ``*`` means "as wide as the widest label", which no static length
    reproduces. Substituting one moves the text, and this tool's whole claim is
    that it does not.
    """
    for match in _ENUMITEM_STAR_MARGIN.finditer(plan.source.masked):
        plan.skipped.append(
            Skipped(
                "ALLY-SRC-040",
                plan.source.line_of(match.start()),
                "a starred length such as leftmargin=* has no static equivalent; "
                "set an explicit length by hand, or drop the option",
            )
        )
    for match in _SETLIST_LABEL.finditer(plan.source.masked):
        plan.skipped.append(
            Skipped(
                "ALLY-SRC-040",
                plan.source.line_of(match.start()),
                "a \\setlist label applies to lists it never names; move it onto "
                "the lists that need it",
            )
        )


# ---------------------------------------------------------------------- #
# driving
# ---------------------------------------------------------------------- #

#: A run of newlines that makes a blank line, inside a display formula.
_BLANK_LINE = re.compile(r"\n([ \t]*\n)+")


def _blank_in_display(plan: RewritePlan) -> None:
    r"""Collapse blank lines inside display math.

    TeX ignores blank lines in maths, so deleting one cannot move the page --
    and the line was never valid anyway. Untagged pdfLaTeX says "Missing $
    inserted", recovers and still writes a PDF, which is exactly why 59 of
    these are sitting in the corpus unnoticed. Under tagging the `\par` ends
    the `equation*` argument and there is no PDF at all.
    """
    source = plan.source
    for pattern in (_BLANK_IN_DISPLAY_BRACKET, _BLANK_IN_DISPLAY_ENV):
        for match in pattern.finditer(source.masked):
            fixed = False
            # One display may hold more than one blank line; each is its own
            # edit so the diff shows every line that moved.
            for blank in _BLANK_LINE.finditer(source.masked, match.start(), match.end()):
                plan.buffer.delete(
                    blank.start() + 1,
                    blank.end(),
                    reason="a blank line ends the formula's argument under tagging",
                    rule="ALLY-SRC-045",
                )
                fixed = True
            if fixed:
                plan._fixed("ALLY-SRC-045")
    # Inline math is found by toggling, not by pattern: see inline_math_spans.
    for start, end in inline_math_spans(source.masked):
        body = source.text[start + 1 : end - 1]
        if body.strip() and _CONTENT_FREE_MATH.match(body):
            # Unwrap: drop the dollars, keep the spacing. Two end-splices, so
            # the content is copied rather than rebuilt.
            plan.buffer.delete(
                start, start + 1, reason="spacing is not maths", rule="ALLY-SRC-046"
            )
            plan.buffer.delete(
                end - 1, end, reason="spacing is not maths", rule="ALLY-SRC-046"
            )
            plan._fixed("ALLY-SRC-046")
            continue
        fixed = False
        for blank in _BLANK_LINE.finditer(source.masked, start, end):
            plan.buffer.delete(
                blank.start() + 1,
                blank.end(),
                reason="a blank line breaks the formula in two",
                rule="ALLY-SRC-045",
            )
            fixed = True
        if fixed:
            plan._fixed("ALLY-SRC-045")


_PASSES = (
    _break_after_display,
    _blank_in_display,
    _mismatched_inline_math,
    _array_in_matrix,
    _enumitem_label,
    _starred_lengths,
)


def plan_rewrites(path: Path, *, skip: frozenset[str] = frozenset()) -> RewritePlan:
    """Everything this module would change in one file, without writing it.

    ``skip`` names rules to leave alone -- see :data:`FIXED_BY_TAGGING`. A
    skipped rule is dropped from the plan silently rather than recorded as a
    :class:`Skipped`, because "your toolchain already handles this" is not a
    finding a reader needs per site; ``doctor --tagging`` says it once.
    """
    source = TexSource.from_path(path)
    plan = RewritePlan(path=Path(path), source=source, buffer=EditBuffer(Path(path)))
    for rewrite in _PASSES:
        rewrite(plan)
    if skip:
        plan.buffer.drop(lambda edit: edit.rule in skip)
        plan.skipped = [item for item in plan.skipped if item.rule not in skip]
        for rule in skip:
            plan.sites.pop(rule, None)
    return plan


def rewrite_files(
    paths: list[Path], *, write: bool = False, skip: frozenset[str] = frozenset()
) -> list[RewritePlan]:
    """Plan -- and optionally apply -- rewrites across a set of files.

    Files that cannot be read are skipped rather than raised on: a corpus of
    17,677 files has a few that are not valid UTF-8, and one of them must not
    stop the other 17,676 from being fixed.
    """
    plans: list[RewritePlan] = []
    for path in paths:
        try:
            plan = plan_rewrites(Path(path), skip=skip)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not plan.changed and not plan.skipped:
            continue
        if write:
            plan.write()
        plans.append(plan)
    return plans

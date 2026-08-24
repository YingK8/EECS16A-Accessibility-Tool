"""Conformance rules.

Every rule has a stable id and cites the standard it enforces, so a finding can
be argued about, suppressed deliberately, or handed to an agent to fix. The
three tiers answer different questions:

* **SRC** rules read the LaTeX. Fast, and the only tier that can point at a
  file and line -- which is what a human or an agent needs in order to act.
* **LOG** rules read the build log. This is where tagpdf reports problems the
  PDF cannot show you afterwards, and where a *silently untagged* build is
  caught: no "Finalizing the tagging structure" line means nothing was tagged.
* **PDF** rules read the artefact. Authoritative, and the only tier that can
  confirm the alt text a reader will actually hear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Profile
from ..texlex import TexSource
from .contrast import contrast_ratio, find_colors, minimum_conforming, resolve_named

__all__ = [
    "Finding",
    "Severity",
    "check_source",
    "check_tagging",
    "check_log",
    "check_pdf_structure",
]


class Severity:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(slots=True)
class Finding:
    rule: str
    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    standard: str = ""
    hint: str = ""
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "standard": self.standard,
            "hint": self.hint,
            "data": self.data,
        }


# ---------------------------------------------------------------------- #
# source rules
# ---------------------------------------------------------------------- #

_DOCUMENTCLASS = re.compile(r"\\documentclass\b")
_DOCUMENTMETADATA = re.compile(r"\\DocumentMetadata\b")
_PICTURE_BOX = re.compile(r"\\framebox\s*\(\s*\d+\s*,\s*\d+\s*\)")
_EPSF = re.compile(r"\\(?:epsffile|epsfbox)\b|\\usepackage\s*(?:\[[^\]]*\])?\s*\{epsf\}")
_LONGTABLE = re.compile(r"\\begin\s*\{longtable\}")
_ULINE_BLANK = re.compile(r"\\uline\s*\{\s*\\hfill\s*\}|\\underline\s*\{\s*\\hspace")
_RAW_FONT = re.compile(r"\\font\s*\\[A-Za-z@]+\s*=\s*([A-Za-z0-9]+)")
_TABULAR = re.compile(r"\\begin\s*\{tabular\}")
_DATATABLE = re.compile(r"\\begin\s*\{(?:DataTable|LayoutTable)\}")


#: An `\\input` whose target is not on disk. The single most common way a
#: build in this corpus ends: "Emergency stop", no PDF, three minutes gone.
#:
#: It is an archival hazard rather than a typo. `fa17/hw/10`, `/12` and `/13`
#: each `\\input{../../../questionBank/hw/N/q_....tex}` -- the *live* shared
#: bank -- for questions that now exist only in the frozen per-semester
#: snapshots (`fa19_questionBank/`, `fa23_questionBank/`, ...). The assignment
#: compiled the year it was written and has been dead ever since; nothing
#: noticed, because nobody rebuilds old homeworks until a conversion tool does.
def _missing_inputs(path: Path, profile: Profile, name: str) -> list[Finding]:
    from ..texlex.includes import IncludeGraph

    # `root` is a Path on a loaded profile but a bare string on one built by
    # hand, and a lint rule is the wrong place to die over that.
    root = Path(profile.corpus.root).resolve()
    graph = IncludeGraph([root])
    try:
        _, unresolved = graph.direct_inputs(path)
    except OSError:
        return []
    from ..repair import find_replacements

    semester = ""
    try:
        semester = path.resolve().relative_to(root).parts[0]
    except (ValueError, IndexError):
        pass
    replacements = {
        item.wanted: item
        for item in find_replacements(
            [(path, target) for target in dict.fromkeys(unresolved)],
            corpus_root=root,
            mirror_root=root,
            semester=semester,
        )
    }

    findings = []
    for target in dict.fromkeys(unresolved):
        stand_in = replacements.get(target.strip())
        if stand_in is None:
            message = (
                f"\\input target {target!r} does not exist anywhere in the "
                "corpus; any build that reaches this file stops here with "
                "'Emergency stop' and produces no PDF"
            )
            hint = (
                "write the question, or delete the \\input. Nothing in the "
                "corpus can stand in for it."
            )
            severity = Severity.ERROR
        elif stand_in.ambiguous:
            message = (
                f"\\input target {target!r} does not exist; the build will "
                f"stand in {stand_in.used.name} from "
                f"{stand_in.used.parent.parent.parent.name}, but the "
                f"{len(stand_in.candidates)} banks that have it DISAGREE — it "
                "may not be the question this assignment asked"
            )
            hint = stand_in.fix
            severity = Severity.ERROR
        else:
            message = (
                f"\\input target {target!r} does not exist; the build will "
                f"stand in the copy from {stand_in.used.parent.parent.parent.name}"
            )
            hint = stand_in.fix
            severity = Severity.WARNING
        findings.append(
            Finding(
                rule="ALLY-SRC-050",
                severity=severity,
                message=message,
                file=name,
                line=None,
                standard="broken source (not a WCAG or PDF/UA rule)",
                hint=hint,
            )
        )
    return findings


def check_source(path: Path, profile: Profile) -> list[Finding]:
    """Lint one ``.tex`` file."""
    source = TexSource.from_path(path)
    findings: list[Finding] = []
    name = str(path)
    findings.extend(_missing_inputs(path, profile, name))

    def add(rule, severity, message, position=None, standard="", hint="", **data):
        findings.append(
            Finding(
                rule=rule,
                severity=severity,
                message=message,
                file=name,
                line=source.line_of(position) if position is not None else None,
                standard=standard,
                hint=hint,
                data=data,
            )
        )

    is_document = source.search(_DOCUMENTCLASS) is not None
    if is_document and source.search(_DOCUMENTMETADATA) is None:
        add(
            "ALLY-SRC-001",
            Severity.ERROR,
            "document has no \\DocumentMetadata, so nothing in it will be tagged",
            0,
            standard="PDF/UA-1",
            hint=(
                "add \\DocumentMetadata{...} as the FIRST line, before "
                "\\documentclass; without it the accessibility layer is inert and "
                "the PDF is untagged while still compiling cleanly"
            ),
        )

    for match in source.finditer(_PICTURE_BOX):
        add(
            "ALLY-SRC-020",
            Severity.ERROR,
            "picture-mode \\framebox(w,h){} carries no semantics for a screen reader",
            match.start(),
            standard="WCAG 1.3.1",
            hint="replace with \\answerbox, which announces itself as an answer region",
        )

    for match in source.finditer(_ULINE_BLANK):
        add(
            "ALLY-SRC-021",
            Severity.WARNING,
            "rule-based fill-in blank is invisible to assistive technology",
            match.start(),
            standard="WCAG 1.3.1",
            hint="replace with \\answerblank",
        )

    for match in source.finditer(_EPSF):
        add(
            "ALLY-SRC-022",
            Severity.ERROR,
            "epsf image inclusion produces no Figure element and no alt text",
            match.start(),
            standard="PDF/UA-1, Matterhorn 13-004",
            hint="convert \\epsffile to \\includegraphics so the graphic can be described",
        )

    for match in source.finditer(_LONGTABLE):
        add(
            "ALLY-SRC-023",
            Severity.WARNING,
            "longtable tagging is implemented for LuaTeX only",
            match.start(),
            standard="PDF/UA-1",
            hint="convert to tabular/tabularx, or build this document with LuaLaTeX",
        )

    for match in source.finditer(_RAW_FONT):
        font = match.group(1)
        add(
            "ALLY-SRC-024",
            Severity.ERROR,
            f"raw \\font selection of {font!r} produces text with no Unicode mapping",
            match.start(),
            standard="PDF/UA-1, Matterhorn 10/31",
            hint=(
                "bitmap-era fonts such as cmdunh10 carry no ToUnicode map, so every "
                "glyph set in them is unextractable and unspeakable regardless of "
                "tagging; switch to an NFSS font family"
            ),
            font=font,
        )

    # Tables: flag only those not already classified, so the finding disappears
    # as soon as an author wraps the table.
    if source.search(_DATATABLE) is None:
        for match in source.finditer(_TABULAR):
            add(
                "ALLY-SRC-030",
                Severity.WARNING,
                "tabular is neither declared a data table nor a layout table",
                match.start(),
                standard="PDF/UA-1, Matterhorn checkpoint 15",
                hint=(
                    "wrap it in DataTable[table/header-rows={1}] if it carries data, "
                    "or LayoutTable if the grid is only for positioning"
                ),
            )

    findings.extend(_check_contrast(source, profile, name))
    return findings


def _check_contrast(source: TexSource, profile: Profile, name: str) -> list[Finding]:
    findings: list[Finding] = []
    background = resolve_named("white") or (1.0, 1.0, 1.0)
    if profile.colors.background.startswith("#"):
        # run.hex_to_rgb, not an inline reimplementation: this one used to skip
        # the validation, so a malformed background silently became a wrong
        # contrast baseline rather than an error anybody saw.
        from ..run import hex_to_rgb

        background = hex_to_rgb(profile.colors.background)

    # Only colours actually applied to text can fail a text-contrast rule.
    # pgfplots defines dozens of colours per document for plot lines and fills
    # (steelblue31119180 and friends); judging those against a text threshold
    # produces pure noise and buries the real findings.
    #: name -> position of its first use, so a finding can point at a line.
    used_at: dict[str, int] = {}
    for match in re.finditer(
        r"\\(?:text)?color\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}", source.masked
    ):
        used_at.setdefault(match.group(1), match.start())
    for match in re.finditer(r"\\colorlet\s*\{([^{}]+)\}", source.masked):
        used_at.setdefault(match.group(1), match.start())
    text_colors = set(used_at)

    defined_here = set()
    for definition in find_colors(source.masked, source.line_of):
        defined_here.add(definition.name)
        if definition.rgb is None:
            continue
        if text_colors and definition.name not in text_colors:
            continue
        ratio = contrast_ratio(definition.rgb, background)
        threshold = (
            profile.colors.min_contrast_large
            if definition.name in profile.colors.large_text_colors
            else profile.colors.min_contrast_normal
        )
        if ratio < threshold:
            findings.append(
                Finding(
                    rule="ALLY-SRC-010",
                    severity=Severity.ERROR,
                    message=(
                        f"colour {definition.name!r} has contrast {ratio:.2f}:1 against "
                        f"the page, below the {threshold}:1 minimum"
                    ),
                    file=name,
                    line=definition.line,
                    standard="WCAG 2.1 SC 1.4.3 (AA)",
                    hint=_darken_hint(definition.rgb, background, threshold),
                    data={"color": definition.name, "ratio": round(ratio, 2)},
                )
            )

    # A colour never defined here, only used: \color{red}. xcolor supplies the
    # value, so there is no \definecolor to read and the loop above cannot see
    # it -- which let the corpus's \edit{} macro and its tally boxes set body
    # text in pure red at 4.00:1 with the checker reporting nothing at all.
    # Reported once per name, at first use: `red` appears dozens of times in a
    # document and forty identical findings are forty reasons to ignore the rule.
    for used, position in used_at.items():
        if used in defined_here:
            continue  # the loop above already judged it, with its real value
        rgb = resolve_named(used)
        if rgb is None:
            continue  # unknown to xcolor by name, or an expression like red!60
        threshold = (
            profile.colors.min_contrast_large
            if used in profile.colors.large_text_colors
            else profile.colors.min_contrast_normal
        )
        ratio = contrast_ratio(rgb, background)
        if ratio < threshold:
            findings.append(
                Finding(
                    rule="ALLY-SRC-010",
                    severity=Severity.ERROR,
                    message=(
                        f"built-in colour {used!r} has contrast {ratio:.2f}:1 against "
                        f"the page, below the {threshold}:1 minimum"
                    ),
                    file=name,
                    line=source.line_of(position),
                    standard="WCAG 2.1 SC 1.4.3 (AA)",
                    hint=_darken_hint(rgb, background, threshold),
                    data={"color": used, "ratio": round(ratio, 2), "builtin": True},
                )
            )

    return findings


# ---------------------------------------------------------------------- #
# constructs LaTeX's own tagging cannot handle
# ---------------------------------------------------------------------- #
#
# These are not this package's bugs and not the author's mistakes: they are
# current limitations of latex-lab that turn a working document into a build
# FAILURE the moment tagging is switched on. Both were found by building the
# real corpus and then repeating the build with latexally removed entirely --
# the errors were byte-identical, so tagging alone is responsible.
#
# They are worth detecting in source because the alternative is a three-minute
# compile ending in a wall of "Missing number, treated as zero" pointing at a
# line that is perfectly valid LaTeX, in a shared question file the person
# converting an assignment has probably never opened.

#: An enumitem label that latex-lab will get wrong.
#:
#: Bisected rather than guessed, and re-measured on TeX Live 2025 / LaTeX2e
#: 2024-11-01 pl2, where the picture is worse than it was:
#:
#:   phase-I / phase-II / tagpdf alone .............. renders (i) (ii), 0 errors
#:   phase-II + table / graphic / firstaid .......... renders (i) (ii), 0 errors
#:   phase-II + math ................................ WRONG, 2 errors
#:   phase-III (any combination) .................... WRONG, 2 errors
#:   phase-IV ....................................... renders (i) (ii) -- but
#:       produces NO structure tree at all on this format, so it is not a route
#:       to a tagged PDF and not a fix.
#:
#: What "wrong" means, measured on `[label=...]` under phase-III:
#:
#:   label=\arabic*.  -> "0. one"  "0. two"   2 errors
#:   label=\roman*:   -> ": one"   ": two"    2 errors
#:   label=\arabic{enumi}. -> "0. one" "0. two"   ZERO errors
#:
#: The counter reads zero for every item. `\roman{0}` and `\alph{0}` expand to
#: nothing, which is why the starred forms look like a missing label; `\arabic`
#: shows the zero. **The non-starred form corrupts the numbering silently** --
#: no error, no warning, a PDF that opens and paginates and is simply wrong. It
#: is the more dangerous of the two and used to go undetected here.
#:
#: The `\setlist` workaround this rule used to recommend NO LONGER WORKS:
#: `\setlist[enumerate,1]{label=(\roman*)}` renders "(i) (ii)" untagged and
#: "1. 2." under phase-III -- the label is dropped and the default silently
#: takes over. Only `\renewcommand{\labelenumi}{(\roman{enumi})}` still
#: renders identically to the untagged original.
_ENUMITEM_LABEL = re.compile(
    r"\\begin\s*\{(?:enumerate|itemize|description)\}\s*\[[^\]]*"
    r"\\(?:arabic|roman|Roman|alph|Alph|value)\s*(?:\*|\{)"
)

#: The same damage, set up in the preamble instead of on the environment.
_SETLIST_LABEL = re.compile(
    r"\\setlist\b[^\n]*?\blabel\s*=[^\n]*?"
    r"\\(?:arabic|roman|Roman|alph|Alph|value)\s*(?:\*|\{)"
)

#: `leftmargin=*` fails the same way (3 errors), and for the same reason.
_ENUMITEM_STAR_MARGIN = re.compile(
    r"\\begin\s*\{(?:enumerate|itemize|description)\}\s*\[[^\]]*"
    r"(?:leftmargin|labelwidth|labelsep|widest)\s*=\s*\*"
)

#: A tabular-family environment nested inside a matrix environment. Measured on
#: sp26/hw/13: `\begin{bmatrix}\begin{array}{r}` gives "Paragraph ended before
#: ... was complete", "Misplaced \crcr" and four more, and no PDF.
#: Inline math opened with `\(` and closed with `$`. Untagged pdfLaTeX accepts
#: it -- `$` ends math mode whichever token started it -- so the corpus has
#: carried it for years without a single error. latex-lab's grabber scans for a
#: literal `\)`, runs past the intended end, and swallows the closing brace of
#: the enclosing `\ans{...}`: "Argument of \__math_grab_inline:w has an extra }".
#: `\\[^)]` consumes an escape pair, so `\$` inside the formula is not mistaken
#: for a delimiter, and it can never cross the `\)` that would end the group
#: legitimately.
_MISMATCHED_INLINE_MATH = re.compile(r"\\\((?:\\[^)]|[^\\$])*?\$")

#: A forced line break straight after a question or solution macro. Harmless
#: while those macros are inline; once `question_tags` makes them emit a real
#: H2 the heading closes the paragraph first, and `\newline` then has nothing
#: to end: "LaTeX Error: There's no line here to end." The largest of the
#: tagging blockers in this corpus by an order of magnitude.
_BREAK_AFTER_HEADING = re.compile(
    r"\\(?:qns|q|qitem|sol|ans|solans)\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*(?:\\newline\b|\\\\)"
)

_ARRAY_IN_MATRIX = re.compile(
    r"\\begin\s*\{[bBpvV]?matrix\}(?:(?!\\end\s*\{[bBpvV]?matrix\}).){0,400}?"
    r"\\begin\s*\{(?:array|tabular)\}",
    re.DOTALL,
)


#: A line break asked for where no line is open. Display math ends in vertical
#: mode under tagging, so `\end{align*}` followed by `\newline` raises "There's
#: no line here to end" and the build produces no PDF. The untagged build
#: tolerates it, which is why the construct is in the corpus at all.
#: All three spellings of display math count. An earlier version matched only
#: \\end{align} and missed `$$ ... $$\\\\`, which is what actually broke
#: sp26/hw/3 -- the rule reported the file clean and the build failed anyway.
_BREAK_AFTER_DISPLAY = re.compile(
    r"(?:"
    r"\\end\s*\{(?:align|equation|gather|multline|eqnarray|flalign)\*?\}"
    r"|\\\]"
    r"|\$\$"
    # Named, because `rewrite.py` inserts `\mbox{}` immediately before the
    # break and needs its offset, not the offset of the display math.
    r")\s*(?P<brk>\\newline\b|\\\\)"
)


def _darken_hint(
    rgb: tuple[float, float, float],
    background: tuple[float, float, float],
    threshold: float,
) -> str:
    """Name the exact colour the runner would propose, not a vague "darken it".

    The previous wording read a fixed replacement out of the profile, which
    overshot the floor badly -- #3399E6 was answered with #0645AD, nearly twice
    the required contrast and visibly harder to read. This is the same
    computation the runner does, so the hint and the run cannot disagree.
    """
    proposed = minimum_conforming(rgb, background=background, target=threshold)
    if proposed is None:  # pragma: no cover - callers only reach here on failure
        return "already meets the floor"
    ratio = contrast_ratio(
        tuple(int(proposed[1:][i : i + 2], 16) / 255 for i in (0, 2, 4)), background
    )
    return (
        f"darken to {proposed} ({ratio:.2f}:1) -- the smallest change to this "
        f"colour that reaches {threshold}:1, hue and saturation unchanged"
    )


def check_tagging(path: Path, *, name: str | None = None) -> list[Finding]:
    r"""Constructs LaTeX's tagging cannot compile. **Not** accessibility rules.

    Every one of these is LaTeX that pdfLaTeX has always accepted and that
    ``\DocumentMetadata{testphase={tagpdf}}`` rejects, so each carries
    ``standard="latex-lab limitation"`` and none of them maps to a WCAG success
    criterion or a Matterhorn condition.

    They used to be reported by :func:`check_source`, which made ``check``
    answer two different questions at once: "is this document accessible" and
    "will this document build at all". They are now the tagging tier of
    ``doctor``, which can be run -- and fixed, via ``latexally.rewrite`` -- on
    its own, without a build and without the accessibility tiers.
    """
    source = TexSource.from_path(path)
    return _tagging_incompatibilities(source, name or str(path))


def _tagging_incompatibilities(source: TexSource, name: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _BREAK_AFTER_DISPLAY.finditer(source.masked):
        findings.append(
            Finding(
                rule="ALLY-SRC-042",
                severity=Severity.ERROR,
                message=(
                    "line break immediately after display math; under tagging "
                    "this fails with \"There's no line here to end\""
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "give the break a line to end -- \\mbox{}\\\\ -- which "
                    "compiles and keeps the spacing. Measured: DELETING the "
                    "break instead removes a blank line and repaginated "
                    "sp26/hw/3 (0.42% of pixels), while \\mbox{} left it at "
                    "0.002%"
                ),
            )
        )
    for pattern, detail in (
        (_ENUMITEM_LABEL, "a counter in the list's own label option"),
        (_SETLIST_LABEL, "a counter in a \\setlist label"),
        (_ENUMITEM_STAR_MARGIN, "a starred length such as leftmargin=*"),
    ):
        for match in pattern.finditer(source.masked):
            findings.append(
                Finding(
                    rule="ALLY-SRC-040",
                    severity=Severity.ERROR,
                    message=(
                        f"enumitem list options use {detail}; under tagging the "
                        "counter reads zero for every item, so the numbering is "
                        "wrong -- and with a non-starred counter it is wrong "
                        "with no error in the log"
                    ),
                    file=name,
                    line=source.line_of(match.start()),
                    standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                    hint=(
                        "set the label before the list, not in its options. Not "
                        "\\labelenumi, which names a depth: 483 of this corpus's "
                        "667 sites are two enumerates deep or more, and every "
                        "question file is \\input inside the driver's own list. "
                        "latexally-core provides \\AllyEnumLabel, which asks "
                        "LaTeX for the depth; `doctor --tagging --fix` applies it"
                    ),
                )
            )
    for match in _ARRAY_IN_MATRIX.finditer(source.masked):
        findings.append(
            Finding(
                rule="ALLY-SRC-041",
                severity=Severity.ERROR,
                message=(
                    "array or tabular nested inside a matrix environment; the "
                    "table tagging module fails on this with 'Misplaced \\crcr'"
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "invert the nesting -- \\left[ ... \\right] around the "
                    "array -- and keep the array. Measured: 257 of this corpus's "
                    "357 sites have a | in the column spec, so they are augmented "
                    "matrices and deleting the array deletes the divider. "
                    "`latexally doctor --tagging --fix` does this"
                ),
            )
        )
    for match in _MISMATCHED_INLINE_MATH.finditer(source.masked):
        findings.append(
            Finding(
                rule="ALLY-SRC-043",
                severity=Severity.ERROR,
                message=(
                    "inline math opened with \\( and closed with $; tagging fails "
                    "with 'Argument of \\__math_grab_inline:w has an extra }'"
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "if the text between the delimiters is a formula, close it "
                    "with \\). Read it first: 28 of this corpus's 30 sites are a "
                    "literal ( written as \\( -- '\\(1) put 4 resistors' -- "
                    "where reclosing the math swallows the paragraph. Untagged "
                    "pdfLaTeX accepts either, so no ordinary build reports it"
                ),
            )
        )
    for match in _BREAK_AFTER_HEADING.finditer(source.masked):
        findings.append(
            Finding(
                rule="ALLY-SRC-044",
                severity=Severity.ERROR,
                message=(
                    "forced line break immediately after a question macro; with "
                    "question H2 tags on the heading has already ended the "
                    "paragraph and this fails with \"There's no line here to end\""
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "delete the \\newline -- the heading supplies the break "
                    "itself; turning question tags off also clears it, at the "
                    "cost of the H2 that makes questions navigable"
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------- #
# log rules
# ---------------------------------------------------------------------- #

_LOG_PATTERNS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "ALLY-LOG-002",
        r"Parent-Child",
        Severity.ERROR,
        "invalid tag nesting reported by tagpdf",
        "a structure element appears somewhere the PDF parent-child rules forbid",
    ),
    (
        "ALLY-LOG-003",
        r"There are still open structures on the stack",
        Severity.ERROR,
        "structure stack left open at end of document",
        "a \\tagstructbegin without its matching end; the tag tree is corrupt",
    ),
    (
        "ALLY-LOG-004",
        r"nested marked content found",
        Severity.ERROR,
        "marked content was nested, which PDF forbids",
        "usually an element opened inside a Figure or an unbalanced push/pop",
    ),
    (
        "ALLY-LOG-005",
        r"paragraph hook count wrong",
        Severity.ERROR,
        "paragraph tagging desynchronised",
        "a package changed \\par behaviour behind the tagging hooks",
    ),
    (
        "ALLY-LOG-006",
        r"LaTeX-lab package '([^']+)' not found",
        Severity.ERROR,
        "a requested testphase module is not installed",
        "the build continued and produced an UNTAGGED PDF; run latexally doctor",
    ),
    (
        "ALLY-LOG-007",
        r"WARNING: mathml missing for hash",
        Severity.WARNING,
        "an equation has no MathML and will fall back to its /Alt string",
        "re-run the math conversion step",
    ),
)

_FINALIZE = re.compile(
    r"Finalizing the tagging structure.*?Writing out ~?(\d+) structure objects", re.DOTALL
)


def check_log(log_path: Path) -> list[Finding]:
    """Read a LaTeX log for tagging failures."""
    if not log_path.is_file():
        return [
            Finding(
                "ALLY-LOG-000",
                Severity.ERROR,
                "no build log found",
                file=str(log_path),
                hint="build the document before checking it",
            )
        ]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    findings: list[Finding] = []

    match = _FINALIZE.search(text)
    if match is None or int(match.group(1)) == 0:
        findings.append(
            Finding(
                "ALLY-LOG-001",
                Severity.ERROR,
                "tagging did not run: no structure objects were written",
                file=str(log_path),
                standard="PDF/UA-1",
                hint=(
                    "this is the cheapest and most important check there is -- the "
                    "document compiled cleanly and produced an untagged PDF"
                ),
            )
        )
    else:
        findings.append(
            Finding(
                "ALLY-LOG-100",
                Severity.INFO,
                f"tagging wrote {match.group(1)} structure objects",
                file=str(log_path),
            )
        )

    for rule, pattern, severity, message, hint in _LOG_PATTERNS:
        hits = re.findall(pattern, text)
        if hits:
            findings.append(
                Finding(
                    rule,
                    severity,
                    f"{message} ({len(hits)} occurrence{'s' if len(hits) > 1 else ''})",
                    file=str(log_path),
                    hint=hint,
                    data={"examples": [str(hit) for hit in hits[:5]]},
                )
            )
    return findings


# ---------------------------------------------------------------------- #
# PDF rules
# ---------------------------------------------------------------------- #

_PLACEHOLDER = re.compile(r"<<ALT:|\bTODO\b|\bFIXME\b|\bPLACEHOLDER\b", re.IGNORECASE)
_FILENAME = re.compile(r"\.(?:png|jpg|jpeg|pdf|eps|svg)\s*$", re.IGNORECASE)
#: latex-lab's *default* math alt text is the LaTeX source, wrapped in a fixed
#: template (`latex-lab-math.ltx`, \l__math_content_template_tl). It is enabled
#: automatically the moment PDF/UA-1 is declared, so a document acquires an /Alt
#: on every Formula without anyone asking for one -- and that /Alt is announced
#: as "backslash f-r-a-c one". It satisfies veraPDF. Catching it is the whole
#: point of ALLY-PDF-041.
_RAW_LATEX = re.compile(r"LaTeX formula (?:starts|ends)|[\\$]")


def _bookmark_navigation(structure, name: str) -> list[Finding]:
    """Do the bookmarks actually go anywhere?

    Counting bookmarks does not answer this, and neither does reading their
    titles or their nesting. A bookmark whose destination was never created
    still appears in the outline, still nests correctly, still counts -- and
    clicking it lands on page 1. That shipped here: 48 entries, all correct, all
    pointing at the top of the document.

    Two failures are worth separating. A destination that resolves nowhere is
    dead. One that resolves to a page-level ``/Fit`` navigates to the right page
    but not to the heading, which on a 13-page document is most of the value.
    """
    targets = getattr(structure, "outline_targets", None)
    if not targets:
        return []

    findings: list[Finding] = []
    dead = [title for title, page, _ in targets if page is None]
    if dead:
        findings.append(
            Finding(
                "ALLY-PDF-023",
                Severity.ERROR,
                f"{len(dead)} of {len(targets)} bookmarks have no destination "
                f"and do not navigate (e.g. {dead[0]!r})",
                file=name,
                standard="WCAG 2.1 AA SC 2.4.5 (technique PDF2)",
                hint=(
                    "an outline entry needs an anchor at the heading; "
                    "\\bookmark[dest=...] only references one, \\pdfbookmark creates it"
                ),
            )
        )

    resolved = [(title, page, kind) for title, page, kind in targets if page is not None]
    positional = [item for item in resolved if item[2] == "/XYZ"]
    if resolved and not positional:
        findings.append(
            Finding(
                "ALLY-PDF-024",
                Severity.WARNING,
                "every bookmark uses a page-level destination, so none scrolls "
                "to its heading",
                file=name,
                standard="WCAG 2.1 AA SC 2.4.5",
                hint="use \\pdfbookmark, which anchors at the current position (/XYZ)",
            )
        )

    # All destinations on one page of a multi-page document is the exact
    # signature of anchors that were never placed where the headings are.
    if structure.page_count > 1 and len(resolved) > 2:
        pages = {page for _, page, _ in resolved}
        if len(pages) == 1:
            findings.append(
                Finding(
                    "ALLY-PDF-025",
                    Severity.ERROR,
                    f"all {len(resolved)} bookmarks point at page {pages.pop()} of "
                    f"{structure.page_count}; the outline cannot navigate",
                    file=name,
                    standard="WCAG 2.1 AA SC 2.4.5",
                    hint=(
                        "the destinations were created somewhere other than at the "
                        "headings -- check that each heading places its own anchor"
                    ),
                )
            )
    return findings


def check_pdf_structure(pdf_path: Path, *, require_bookmarks: bool = True) -> list[Finding]:
    """Assert the structural contract on a built PDF."""
    from .content import read_page_content
    from .structure import read_structure

    findings: list[Finding] = []
    name = str(pdf_path)
    structure = read_structure(pdf_path)

    if not structure.tagged:
        return [
            Finding(
                "ALLY-PDF-001",
                Severity.ERROR,
                "PDF has no structure tree; it is not a tagged PDF",
                file=name,
                standard="PDF/UA-1",
                hint="check \\DocumentMetadata and run latexally doctor",
            )
        ]

    for figure in structure.of_tag("Figure"):
        if not figure.alt:
            findings.append(
                Finding(
                    "ALLY-PDF-002",
                    Severity.ERROR,
                    "Figure element has no /Alt",
                    file=name,
                    standard="PDF/UA-1, Matterhorn 13-004",
                    hint="describe the figure, or mark it decorative with Decorative",
                )
            )
            continue
        if _PLACEHOLDER.search(figure.alt):
            findings.append(
                Finding(
                    "ALLY-PDF-003",
                    Severity.ERROR,
                    f"Figure /Alt is an unfilled placeholder: {figure.alt[:60]!r}",
                    file=name,
                    standard="PDF/UA-1, Matterhorn 13-004",
                    hint=(
                        "a placeholder passes a naive 'has /Alt' check and veraPDF, so "
                        "this would be a silent false conformance claim"
                    ),
                )
            )
        if _FILENAME.search(figure.alt):
            findings.append(
                Finding(
                    "ALLY-PDF-004",
                    Severity.ERROR,
                    f"Figure /Alt is a file name: {figure.alt[:60]!r}",
                    file=name,
                    standard="PDF/UA-1, Matterhorn 13-004",
                    hint="latex-lab uses the file name as its default alt; describe the image",
                )
            )
        if figure.actual_text:
            findings.append(
                Finding(
                    "ALLY-PDF-005",
                    Severity.WARNING,
                    "Figure carries /ActualText as well as /Alt",
                    file=name,
                    standard="Matterhorn 13-005",
                    hint="/ActualText is a character replacement and is unreliable across readers",
                )
            )

    for formula in structure.of_tag("Formula"):
        if not formula.alt:
            findings.append(
                Finding(
                    "ALLY-PDF-040",
                    Severity.ERROR,
                    "Formula element has no /Alt",
                    file=name,
                    standard="PDF/UA-1, Matterhorn 17-003",
                    hint="run the math conversion step so every formula carries speech text",
                )
            )
        elif _RAW_LATEX.search(formula.alt):
            findings.append(
                Finding(
                    "ALLY-PDF-041",
                    Severity.ERROR,
                    f"Formula /Alt is LaTeX source, not speech: {formula.alt[:60]!r}",
                    file=name,
                    standard="WCAG 2.1 A SC 1.1.1, PDF/UA-1",
                    hint=(
                        "this is latex-lab's default template; a reader announces it "
                        "character by character while veraPDF reports the file clean"
                    ),
                )
            )

    levels = structure.heading_levels
    if levels:
        if levels[0] != 1:
            findings.append(
                Finding(
                    "ALLY-PDF-010",
                    Severity.ERROR,
                    f"first heading is H{levels[0]}, not H1",
                    file=name,
                    standard="Matterhorn 14-002",
                )
            )
        for previous, current in zip(levels, levels[1:]):
            if current > previous + 1:
                findings.append(
                    Finding(
                        "ALLY-PDF-011",
                        Severity.ERROR,
                        f"heading level skips from H{previous} to H{current}",
                        file=name,
                        standard="Matterhorn 14-003",
                    )
                )
                break
    else:
        findings.append(
            Finding(
                "ALLY-PDF-012",
                Severity.WARNING,
                "document has no headings, so it cannot be navigated by structure",
                file=name,
                standard="WCAG 2.4.6",
            )
        )

    if not structure.title:
        findings.append(
            Finding(
                "ALLY-PDF-020",
                Severity.ERROR,
                "no document title in the metadata",
                file=name,
                standard="Matterhorn 06-003 (dc:title)",
                hint="call \\accesstitle{...} or \\title{...}",
            )
        )
    if not structure.lang:
        findings.append(
            Finding(
                "ALLY-PDF-021",
                Severity.ERROR,
                "document language is not set",
                file=name,
                standard="Matterhorn 11-001",
                hint="add lang=en-US to \\DocumentMetadata",
            )
        )
    if require_bookmarks and not structure.outline:
        findings.append(
            Finding(
                "ALLY-PDF-022",
                Severity.WARNING,
                "PDF has no bookmark outline",
                file=name,
                standard="WCAG 2.1 AA SC 2.4.5 (technique PDF2)",
                hint=(
                    "tagging never writes /Outlines -- load the bookmark package; "
                    "PDF/UA does not require an outline but WCAG expects one for "
                    "multi-page documents"
                ),
            )
        )

    findings.extend(_bookmark_navigation(structure, name))

    # The Described contract, checked on the artefact rather than trusted.
    for page in range(structure.page_count):
        content = read_page_content(pdf_path, page)
        if content.untagged_text:
            findings.append(
                Finding(
                    "ALLY-PDF-030",
                    Severity.ERROR,
                    f"page {page + 1} draws text outside any marked content",
                    file=name,
                    line=page + 1,
                    standard="Matterhorn checkpoint 01",
                    data={"sample": content.untagged_text[:120]},
                )
            )
        for region in content.regions:
            if region.tag != "Artifact" or region.subtype != "Layout" or not region.text:
                continue
            findings.append(
                Finding(
                    "ALLY-PDF-032",
                    Severity.WARNING,
                    (
                        f"decorative region on page {page + 1} contains text: "
                        f"{region.text[:60]!r}"
                    ),
                    file=name,
                    line=page + 1,
                    standard="WCAG 1.1.1",
                    hint=(
                        "the artifact mechanism hides this from readers that walk "
                        "the tag tree, but a reader that extracts text by position "
                        "-- macOS Preview among them -- announces it anyway, and "
                        "tagpdf's artifact API cannot carry /ActualText to stop it; "
                        "describe the figure instead of marking it decorative"
                    ),
                    data={"text": region.text[:100]},
                )
            )
        figure_ids = {
            (region.tag, region.mcid) for region in content.regions if region.tag == "Figure"
        }
        for region in content.regions:
            if region.tag in ("Figure", "Artifact") or not region.text:
                continue
            if any(ancestor in figure_ids for ancestor in region.ancestors):
                findings.append(
                    Finding(
                        "ALLY-PDF-031",
                        Severity.ERROR,
                        (
                            f"readable <{region.tag}> nested inside a Figure on page "
                            f"{page + 1}: its text is spoken despite the alt text"
                        ),
                        file=name,
                        line=page + 1,
                        standard="PDF/UA-1",
                        hint=(
                            "the alt-only region opened after its content was already "
                            "typeset; open the Figure before the body, not after"
                        ),
                        data={"text": region.text[:100]},
                    )
                )
    return findings

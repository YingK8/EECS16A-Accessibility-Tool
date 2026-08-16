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
from .contrast import contrast_ratio, find_colors, resolve_named

__all__ = ["Finding", "Severity", "check_source", "check_log", "check_pdf_structure"]


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


def check_source(path: Path, profile: Profile) -> list[Finding]:
    """Lint one ``.tex`` file."""
    source = TexSource.from_path(path)
    findings: list[Finding] = []
    name = str(path)

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
            "A11Y-SRC-001",
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
            "A11Y-SRC-020",
            Severity.ERROR,
            "picture-mode \\framebox(w,h){} carries no semantics for a screen reader",
            match.start(),
            standard="WCAG 1.3.1",
            hint="replace with \\answerbox, which announces itself as an answer region",
        )

    for match in source.finditer(_ULINE_BLANK):
        add(
            "A11Y-SRC-021",
            Severity.WARNING,
            "rule-based fill-in blank is invisible to assistive technology",
            match.start(),
            standard="WCAG 1.3.1",
            hint="replace with \\answerblank",
        )

    for match in source.finditer(_EPSF):
        add(
            "A11Y-SRC-022",
            Severity.ERROR,
            "epsf image inclusion produces no Figure element and no alt text",
            match.start(),
            standard="PDF/UA-1, Matterhorn 13-004",
            hint="convert \\epsffile to \\includegraphics so the graphic can be described",
        )

    for match in source.finditer(_LONGTABLE):
        add(
            "A11Y-SRC-023",
            Severity.WARNING,
            "longtable tagging is implemented for LuaTeX only",
            match.start(),
            standard="PDF/UA-1",
            hint="convert to tabular/tabularx, or build this document with LuaLaTeX",
        )

    for match in source.finditer(_RAW_FONT):
        font = match.group(1)
        add(
            "A11Y-SRC-024",
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
                "A11Y-SRC-030",
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
        raw = profile.colors.background.lstrip("#")
        background = tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[assignment]

    # Only colours actually applied to text can fail a text-contrast rule.
    # pgfplots defines dozens of colours per document for plot lines and fills
    # (steelblue31119180 and friends); judging those against a text threshold
    # produces pure noise and buries the real findings.
    text_colors = {
        match.group(1)
        for match in re.finditer(
            r"\\(?:text)?color\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}", source.masked
        )
    } | {
        match.group(1)
        for match in re.finditer(r"\\colorlet\s*\{([^{}]+)\}", source.masked)
    }

    for definition in find_colors(source.masked, source.line_of):
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
                    rule="A11Y-SRC-010",
                    severity=Severity.ERROR,
                    message=(
                        f"colour {definition.name!r} has contrast {ratio:.2f}:1 against "
                        f"the page, below the {threshold}:1 minimum"
                    ),
                    file=name,
                    line=definition.line,
                    standard="WCAG 2.1 SC 1.4.3 (AA)",
                    hint=(
                        f"replacement suggested in the course profile: "
                        f"{profile.colors.replace.get(definition.name, 'darken the colour')}"
                    ),
                    data={"color": definition.name, "ratio": round(ratio, 2)},
                )
            )

    findings.extend(_tagging_incompatibilities(source, name))
    return findings


# ---------------------------------------------------------------------- #
# constructs LaTeX's own tagging cannot handle
# ---------------------------------------------------------------------- #
#
# These are not this package's bugs and not the author's mistakes: they are
# current limitations of latex-lab that turn a working document into a build
# FAILURE the moment tagging is switched on. Both were found by building the
# real corpus and then repeating the build with latexa11y removed entirely --
# the errors were byte-identical, so tagging alone is responsible.
#
# They are worth detecting in source because the alternative is a three-minute
# compile ending in a wall of "Missing number, treated as zero" pointing at a
# line that is perfectly valid LaTeX, in a shared question file the person
# converting an assignment has probably never opened.

#: enumitem's STARRED counter in a per-instance label, e.g. `\roman*`.
#:
#: Bisected rather than guessed. The trigger is not `label=` and not enumitem:
#: it is the starred counter in an optional argument on the environment, and it
#: breaks as soon as latex-lab's `math` module or `phase-III` is loaded. Both
#: are essential here, and every other combination was tested:
#:
#:   phase-I / phase-II / tagpdf alone .............. renders (i) (ii), 0 errors
#:   phase-II + table / graphic / firstaid .......... renders (i) (ii), 0 errors
#:   phase-II + math ................................ renders ()  (),  2 errors
#:   phase-III (any combination) .................... renders ()  (),  2 errors
#:   \setlist[enumerate,1]{label=(\roman*)} ......... renders (i) (ii), 0 errors
#:   \renewcommand{\labelenumi}{(\roman{enumi})} .... renders (i) (ii), 0 errors
#:
#: The last two are the fixes, and both are verified to render identically to the
#: untagged original. Note the *rendering*: this is not only a build failure. The
#: labels come out EMPTY -- `() first () second` -- so a document that ignored
#: the errors would ship visibly wrong list numbering.
_ENUMITEM_STARRED = re.compile(
    r"\\begin\s*\{(?:enumerate|itemize|description)\}\s*\[[^\]]*"
    r"\\(?:arabic|roman|Roman|alph|Alph|value)\s*\*"
)

#: `leftmargin=*` fails the same way (3 errors), and for the same reason.
_ENUMITEM_STAR_MARGIN = re.compile(
    r"\\begin\s*\{(?:enumerate|itemize|description)\}\s*\[[^\]]*"
    r"(?:leftmargin|labelwidth|labelsep|widest)\s*=\s*\*"
)

#: A tabular-family environment nested inside a matrix environment. Measured on
#: sp26/hw/13: `\begin{bmatrix}\begin{array}{r}` gives "Paragraph ended before
#: ... was complete", "Misplaced \crcr" and four more, and no PDF.
_ARRAY_IN_MATRIX = re.compile(
    r"\\begin\s*\{[bBpvV]?matrix\}(?:(?!\\end\s*\{[bBpvV]?matrix\}).){0,400}?"
    r"\\begin\s*\{(?:array|tabular)\}",
    re.DOTALL,
)


#: A line break asked for where no line is open. Display math ends in vertical
#: mode under tagging, so `\end{align*}` followed by `\newline` raises "There's
#: no line here to end" and the build produces no PDF. The untagged build
#: tolerates it, which is why the construct is in the corpus at all.
#: Measured: 12 occurrences in 8 live files.
_BREAK_AFTER_DISPLAY = re.compile(
    r"\\end\s*\{(?:align|equation|gather|multline|eqnarray|flalign)\*?\}"
    r"\s*(?:\\newline\b|\\\\)"
)


def _tagging_incompatibilities(source: TexSource, name: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _BREAK_AFTER_DISPLAY.finditer(source.masked):
        findings.append(
            Finding(
                rule="A11Y-SRC-042",
                severity=Severity.ERROR,
                message=(
                    "line break immediately after display math; under tagging "
                    "this fails with \"There's no line here to end\""
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "delete the \\newline: display math already ends the line, "
                    "and a blank line gives the paragraph break if one is wanted"
                ),
            )
        )
    for pattern, detail in (
        (_ENUMITEM_STARRED, "a starred counter such as \\roman* in the label"),
        (_ENUMITEM_STAR_MARGIN, "a starred length such as leftmargin=*"),
    ):
        for match in pattern.finditer(source.masked):
            findings.append(
                Finding(
                    rule="A11Y-SRC-040",
                    severity=Severity.ERROR,
                    message=(
                        f"enumitem list options use {detail}; under tagging this "
                        "fails with 'Missing number, treated as zero' AND renders "
                        "the labels empty"
                    ),
                    file=name,
                    line=source.line_of(match.start()),
                    standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                    hint=(
                        "move the option to the preamble as "
                        "\\setlist[enumerate,1]{label=(\\roman*)}, or drop enumitem "
                        "for this list and use \\renewcommand{\\labelenumi}"
                        "{(\\roman{enumi})} before it -- both verified to render "
                        "identically to the untagged original"
                    ),
                )
            )
    for match in _ARRAY_IN_MATRIX.finditer(source.masked):
        findings.append(
            Finding(
                rule="A11Y-SRC-041",
                severity=Severity.ERROR,
                message=(
                    "array or tabular nested inside a matrix environment; the "
                    "table tagging module fails on this with 'Misplaced \\crcr'"
                ),
                file=name,
                line=source.line_of(match.start()),
                standard="latex-lab limitation (not a WCAG or PDF/UA rule)",
                hint=(
                    "the inner array is almost always there for column alignment "
                    "the matrix already provides; delete it and keep the matrix"
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------- #
# log rules
# ---------------------------------------------------------------------- #

_LOG_PATTERNS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "A11Y-LOG-002",
        r"Parent-Child",
        Severity.ERROR,
        "invalid tag nesting reported by tagpdf",
        "a structure element appears somewhere the PDF parent-child rules forbid",
    ),
    (
        "A11Y-LOG-003",
        r"There are still open structures on the stack",
        Severity.ERROR,
        "structure stack left open at end of document",
        "a \\tagstructbegin without its matching end; the tag tree is corrupt",
    ),
    (
        "A11Y-LOG-004",
        r"nested marked content found",
        Severity.ERROR,
        "marked content was nested, which PDF forbids",
        "usually an element opened inside a Figure or an unbalanced push/pop",
    ),
    (
        "A11Y-LOG-005",
        r"paragraph hook count wrong",
        Severity.ERROR,
        "paragraph tagging desynchronised",
        "a package changed \\par behaviour behind the tagging hooks",
    ),
    (
        "A11Y-LOG-006",
        r"LaTeX-lab package '([^']+)' not found",
        Severity.ERROR,
        "a requested testphase module is not installed",
        "the build continued and produced an UNTAGGED PDF; run latexa11y doctor",
    ),
    (
        "A11Y-LOG-007",
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
                "A11Y-LOG-000",
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
                "A11Y-LOG-001",
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
                "A11Y-LOG-100",
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
                "A11Y-PDF-023",
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
                "A11Y-PDF-024",
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
                    "A11Y-PDF-025",
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
                "A11Y-PDF-001",
                Severity.ERROR,
                "PDF has no structure tree; it is not a tagged PDF",
                file=name,
                standard="PDF/UA-1",
                hint="check \\DocumentMetadata and run latexa11y doctor",
            )
        ]

    for figure in structure.of_tag("Figure"):
        if not figure.alt:
            findings.append(
                Finding(
                    "A11Y-PDF-002",
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
                    "A11Y-PDF-003",
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
                    "A11Y-PDF-004",
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
                    "A11Y-PDF-005",
                    Severity.WARNING,
                    "Figure carries /ActualText as well as /Alt",
                    file=name,
                    standard="Matterhorn 13-005",
                    hint="/ActualText is a character replacement and is unreliable across readers",
                )
            )

    levels = structure.heading_levels
    if levels:
        if levels[0] != 1:
            findings.append(
                Finding(
                    "A11Y-PDF-010",
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
                        "A11Y-PDF-011",
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
                "A11Y-PDF-012",
                Severity.WARNING,
                "document has no headings, so it cannot be navigated by structure",
                file=name,
                standard="WCAG 2.4.6",
            )
        )

    if not structure.title:
        findings.append(
            Finding(
                "A11Y-PDF-020",
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
                "A11Y-PDF-021",
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
                "A11Y-PDF-022",
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
                    "A11Y-PDF-030",
                    Severity.ERROR,
                    f"page {page + 1} draws text outside any marked content",
                    file=name,
                    line=page + 1,
                    standard="Matterhorn checkpoint 01",
                    data={"sample": content.untagged_text[:120]},
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
                        "A11Y-PDF-031",
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

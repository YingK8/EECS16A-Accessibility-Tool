"""The page must not move.

The single loudest piece of feedback during this work was "the format is
completely different, including font size typeface spacing etc" -- said about a
class that had drifted from the house style while every structural test stayed
green. Tag trees, bookmarks and /Alt strings are all invisible on the page, so
nothing in the rest of the suite could have caught it.

This compares rendered pixels. The reference is a plain LaTeX2e preamble holding
the house spec verbatim -- the raw dimen assignments and font choices copied from
the course's own ee16.sty -- against the same content typeset by
``latexally-assignment``. Reproducing the spec inline rather than loading
ee16.sty keeps the test self-contained: ee16.sty is course material and does not
live in this repository.

Skipped when pdflatex or PyMuPDF is missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEXDIR = REPO / "tex"

pytest.importorskip("pymupdf", reason="pixel comparison needs the [tui] extra")
pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not installed"
)

#: 96/255 at 110 dpi, greyscale: the settings used for every fidelity number
#: quoted in the docs. A lower threshold counts antialiasing as a difference.
DPI = 110
THRESHOLD = 96

#: The body, written once and typeset twice. Deliberately exercises the things
#: that actually drifted: the masthead, question numbering, part labels, an
#: inline solution and a framed answer box.
BODY = r"""
\begin{qunlist}
\qns{Noisy Images}
We model the measurement as a linear system, and ask what it takes to invert it.

\begin{enumerate}
\qitem
Express the image vector in terms of the measurement matrix.

\sol{Invert the measurement matrix and apply it to the observations.}

\qitem
State the condition under which that inverse exists.

\sol{The matrix must be square and full rank.}
\end{enumerate}

\qns{A Second Question}
Some more running text, long enough to wrap onto a second line so that any
change in the text block width or the baseline skip shows up immediately.

\begin{enumerate}
\qitem
Write your answer in the box.

\answerbox{1in}
\end{enumerate}
\end{qunlist}
"""

#: The house spec, read verbatim from the course's ee16.sty (lines 77-95).
#: Raw dimen assignments, NOT the geometry package -- geometry computes margins
#: differently and moves the text block by a few points.
REFERENCE_PREAMBLE = r"""
\documentclass[11pt]{article}
\usepackage{times}
\usepackage{mathptmx}
\usepackage[T1]{fontenc}
\usepackage{xcolor}
\usepackage{amsmath}
\textheight=9in
\textwidth=6.5in
\addtolength{\voffset}{-1in}
\oddsidemargin=0in
\evensidemargin=-0.25in
\parindent=0pt
\parskip=5pt
\itemsep=-1pt
\renewcommand{\baselinestretch}{1.0}
\font\dunhbb=cmdunh10 scaled \magstep3
\newcounter{sparectr}
\newenvironment{qunlist}{\begin{list}{{\bf\arabic{sparectr}.}}%
   {\usecounter{sparectr}\setlength{\leftmargin}{0pt}}}{\end{list}}
\def\qns#1{{\bf\item #1}}
\newcommand{\qitem}{\item}
\newcommand{\sol}[1]{{\color{blue}\textbf{Solution: } #1}}
\newcommand{\answerbox}[1]{\par\framebox[\linewidth]{\rule{0pt}{#1}}\par}
\renewcommand{\labelenumi}{(\alph{enumi})}
"""

#: Where the course's real ee16.sty lives, when the corpus is on this machine.
#: The masthead is compared against that file and nothing else: a hand-written
#: approximation of \@maketitle tests the approximation, not the house style --
#: which is exactly what an earlier version of this test did, reporting a 50pt
#: drift that came entirely from the fake title block.
def _real_ee16() -> Path | None:
    from latexally.config import load_profile

    try:
        profile = load_profile(REPO / "profiles" / "eecs16a.yaml")
    except Exception:
        return None
    candidate = profile.corpus.root / "ee16.sty"
    return candidate if candidate.is_file() else None


def _compile(work: Path, name: str, text: str) -> Path:
    (work / f"{name}.tex").write_text(text, encoding="utf-8")
    for _ in range(3):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-file-line-error", f"{name}.tex"],
            cwd=work,
            env={"TEXINPUTS": f"{TEXDIR}:", "PATH": os.environ["PATH"]},
            capture_output=True,
            timeout=180,
            check=False,
        )
    pdf = work / f"{name}.pdf"
    assert pdf.is_file(), f"{name} produced no PDF"
    return pdf


def strongly_differing_fraction(left: Path, right: Path, *, gray: bool = True) -> float:
    """Fraction of pixels differing by more than the threshold, 0..1.

    Greyscale measures *layout*, which is what usually drifts. It is blind to
    colour by construction: #0645AD and pure blue land 33 grey levels apart, well
    under the threshold, so a colour-only change reads as zero. Pass
    ``gray=False`` when the change under test is the colour itself.
    """
    import pymupdf

    space = pymupdf.csGRAY if gray else pymupdf.csRGB
    a, b = pymupdf.open(left), pymupdf.open(right)
    assert a.page_count == b.page_count, (
        f"page count differs: {a.page_count} vs {b.page_count}"
    )
    differing = total = 0
    for index in range(a.page_count):
        left_px = a[index].get_pixmap(dpi=DPI, colorspace=space).samples
        right_px = b[index].get_pixmap(dpi=DPI, colorspace=space).samples
        assert len(left_px) == len(right_px), "page geometry differs"
        differing += sum(1 for x, y in zip(left_px, right_px) if abs(x - y) > THRESHOLD)
        total += len(left_px)
    return differing / total if total else 0.0


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> tuple[Path, Path]:
    """(reference, class-built) PDFs of identical BODY content, no masthead.

    The title block is deliberately excluded from both sides and checked
    separately against the real ee16.sty. What this pair isolates is everything
    the house spec states exactly -- text block, type size, leading, list labels,
    inline solution, answer box -- so a failure here means the class drifted, not
    that the fixture guessed.

    Built back to back with tagging OFF on both sides: tagging repaginates by
    ~2.6%, which would swamp the layout difference being measured. That cost is
    reported separately, per assignment, by the runner.
    """
    work = tmp_path_factory.mktemp("fidelity")
    reference = _compile(
        work,
        "reference",
        REFERENCE_PREAMBLE
        + "\\begin{document}\\pagestyle{empty}\n"
        + BODY
        + "\n\\end{document}\n",
    )
    produced = _compile(
        work,
        "produced",
        "\\documentclass[sol]{latexally-assignment}\n"
        "\\usepackage{latexally-compat-ee16}\n"
        # \answerbox is a Described region, and Described paints an invisible
        # copy of its description into the text layer for viewers that ignore
        # tags. That is deliberate non-visible content: it changes what text
        # extraction returns without moving a single visible mark (see
        # test_text_layer_does_not_change_layout). This fixture measures
        # layout, so switch it off rather than measure it as drift.
        "\\accesssetup{text-layer=false}\n"
        "\\begin{document}\\pagestyle{empty}\n" + BODY + "\n\\end{document}\n",
    )
    return reference, produced


def test_class_matches_the_house_layout(rendered):
    """The regression guard the class refactor never had.

    A threshold rather than an exact match: the two differ in the solution colour
    (conforming blue vs the course's `blue`) and in antialiased edges. 3% leaves
    room for that while catching any change to the text block, the type size or
    the leading -- each of which moves far more than 3% of the page.
    """
    reference, produced = rendered
    fraction = strongly_differing_fraction(reference, produced)
    assert fraction < 0.03, (
        f"{100 * fraction:.2f}% of pixels differ from the house style; "
        "the class has drifted (check \\textwidth, \\parskip and the font family)"
    )


def test_masthead_matches_the_real_ee16(tmp_path_factory):
    """The title block, against the course's own ee16.sty and nothing else.

    Skipped when the corpus is not on this machine -- ee16.sty is course
    material and is deliberately not vendored into this repository.
    """
    ee16 = _real_ee16()
    if ee16 is None:
        pytest.skip("ee16.sty not available; needs the course corpus")

    work = tmp_path_factory.mktemp("masthead")
    shutil.copy(ee16, work / "ee16.sty")
    for extra in ("timestamp.sty", "markup.sty"):
        candidate = ee16.parent / extra
        if candidate.is_file():
            shutil.copy(candidate, work / extra)

    reference = _compile(
        work,
        "ee16-masthead",
        "\\documentclass[11pt]{article}\n"
        "\\usepackage{ee16}\n"
        "\\begin{document}\n"
        "\\def\\title{Homework 9}\n\\maketitle\n"
        "Body text under the masthead.\n"
        "\\end{document}\n",
    )
    produced = _compile(
        work,
        "class-masthead",
        "\\documentclass{latexally-assignment}\n"
        "\\usepackage{latexally-compat-ee16}\n"
        "\\begin{document}\n"
        "\\def\\title{Homework 9}\n\\maketitle\n"
        "Body text under the masthead.\n"
        "\\end{document}\n",
    )
    fraction = strongly_differing_fraction(reference, produced)
    assert fraction < 0.03, (
        f"{100 * fraction:.2f}% of pixels differ from ee16.sty's own masthead"
    )


def test_both_sides_are_a_single_page(rendered):
    """Pagination is the most visible drift there is, so assert it separately."""
    import pymupdf

    reference, produced = rendered
    assert pymupdf.open(reference).page_count == pymupdf.open(produced).page_count == 1


def test_text_block_geometry_is_identical(rendered):
    """Compare the ink bounding box: catches a margin change a threshold hides.

    A uniform 2mm shift of the whole page could stay under a pixel-fraction
    threshold on a sparse page while being glaringly wrong to a reader.
    """
    import pymupdf

    reference, produced = rendered
    boxes = []
    for path in (reference, produced):
        page = pymupdf.open(path)[0]
        blocks = page.get_text("blocks")
        assert blocks, "no text found"
        boxes.append(
            (
                round(min(b[0] for b in blocks)),
                round(min(b[1] for b in blocks)),
                round(max(b[2] for b in blocks)),
                round(max(b[3] for b in blocks)),
            )
        )
    left, right = boxes
    for name, a, b in zip(("x0", "y0", "x1", "y1"), left, right):
        assert abs(a - b) <= 2, f"{name} differs by {abs(a - b)}pt: {left} vs {right}"


def test_house_colors_option_restores_the_course_palette(tmp_path_factory):
    """The [housecolors] option was implemented and had never been exercised.

    Conforming colours are the default because the course's solution blue
    measures 3.07:1 against a 4.5:1 requirement. The escape hatch has to work,
    and it has to actually change the pixels -- an option that silently does
    nothing is worse than no option.
    """
    work = tmp_path_factory.mktemp("housecolors")
    document = (
        "\\documentclass[{options}]{{latexally-assignment}}\n"
        "\\usepackage{{latexally-compat-ee16}}\n"
        "\\begin{{document}}\n"
        "\\def\\title{{Colours}}\n\\maketitle\n"
        "\\begin{{qunlist}}\\qns{{Q}}\n"
        "\\sol{{Coloured solution text.}}\n\\ans{{Coloured answer text.}}\n"
        "\\end{{qunlist}}\n\\end{{document}}\n"
    )
    accessible = _compile(work, "accessible", document.format(options="ans"))
    house = _compile(work, "house", document.format(options="ans,housecolors"))

    # The exact ink colour, not a pixel fraction: reading the span colour is
    # both stricter than a pixel count and independent of its threshold.
    #
    # ANSWER text, not solution text, and that is the whole point of the
    # rewrite. The course defines the answer colour as rgb(0.2,0.2,0.9) =
    # #3333E6; the palette moves it onto the same blue as everything else,
    # prose and drawings alike, while [housecolors] leaves it where it was.
    assert _solution_colors(accessible) == {0x1754FF}, (
        "the palette's answer colour is no longer allyBlue"
    )
    assert _solution_colors(house) == {0x3333E6}, (
        "[housecolors] did not restore the course's original answer colour"
    )


def _solution_colors(pdf: Path) -> set[int]:
    """Every non-black text colour in a PDF, as 0xRRGGBB integers."""
    import pymupdf

    document = pymupdf.open(pdf)
    return {
        span["color"]
        for page in document
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["color"] != 0 and span["text"].strip()
    }


#: The five palette tokens, and what each measures against a white page. One
#: set now, for prose and for drawings alike: each is also >= 3:1 against the
#: black axes a line sits beside, which is what the second, lighter set used to
#: be for. They sit close together on purpose -- the derivation gives each the
#: same relative margin over its two floors.
PALETTE = {
    "allyBlue": (0x1754FF, 5.6),
    "allyRed": (0xD20000, 5.61),
    "allyGreen": (0x007900, 5.62),
    "allyPurple": (0xB800B8, 5.63),
    "allyOrange": (0xA55000, 5.6),
}


def _ratio(packed: int) -> float:
    from latexally.check.contrast import contrast_ratio

    rgb = tuple(((packed >> shift) & 0xFF) / 255 for shift in (16, 8, 0))
    return contrast_ratio(rgb, (1.0, 1.0, 1.0))


def test_every_palette_token_clears_the_floor():
    """The reason the palette exists, stated as numbers rather than as a belief.

    Pure primaries were the brief and none of them survives both floors: #FF0000
    measures 4.00:1 on the page and fails AA for body text, #00FF00 measures
    1.37:1 and fails it comprehensively, and #0000FF clears the page at 8.59:1
    but only 2.44:1 against the black axes a plotted line sits beside. So the
    hue is held exactly and the lightness is what moves.
    """
    assert _ratio(0xFF0000) < 4.5, "pure red should be the one that fails"
    assert _ratio(0x00FF00) < 4.5, "pure green should be the one that fails badly"
    assert _ratio(0x0000FF) >= 4.5, "pure blue clears the page; it is the ink it fails"

    for name, (packed, expected) in PALETTE.items():
        measured = _ratio(packed)
        assert measured >= 4.5, f"{name} is below the AA floor at {measured:.2f}:1"
        assert abs(measured - expected) < 0.02, (
            f"{name} measures {measured:.2f}:1, not the documented {expected}:1"
        )


def test_the_palette_is_what_the_style_file_actually_defines():
    """A grep, because the alternative is compiling five documents to read five
    fills. The .sty is the single source of these values -- the profile does not
    restate them -- so a drift here is a drift everywhere."""
    style = (Path(__file__).resolve().parents[1] / "tex" / "latexally-core.sty").read_text()
    defined = [line for line in style.splitlines() if line.startswith("\\definecolor")]
    for name, (packed, _) in PALETTE.items():
        assert any(name in line and f"{packed:06X}" in line for line in defined), (
            f"{name} is not defined as #{packed:06X}"
        )

    # The old per-name values must be gone from the DEFINITIONS, not from the
    # file: the comment above them explains what they were and why they went,
    # and a bare `"187AC4" not in style` matches that prose and fails on a
    # correct file. Leaving a real \definecolor{allySolution}{HTML}{187AC4}
    # behind is the actual hazard -- a document loading the package directly
    # would still get the second blue.
    assert not any("187AC4" in line for line in defined), "the old blue is still defined"
    assert not any("EE0000" in line for line in defined), "the old red is still defined"


def test_the_hue_bins_are_what_the_style_file_bins():
    """The .sty does the snapping; Python only mirrors it for the runner.

    Two implementations of one rule, so this is the drift test. The .sty writes
    each bin as its upper bound, in order, which is the same shape SNAP_BINS has.
    """
    from latexally.check.contrast import ACHROMATIC_CHROMA, SNAP_BINS

    style = (Path(__file__).resolve().parents[1] / "tex" / "latexally-core.sty").read_text()
    body = style.split("\\cs_new_protected:Npn \\__access_snap_bin:w")[1].split("\\cs_new")[0]
    assert f"{{ {ACHROMATIC_CHROMA:.2f} }}" in body, "the achromatic floor drifted"
    binned = re.findall(r"< \{ (\d+) \}\s*\{ ([a-z]+) \}", body)
    assert [(float(edge), name) for edge, name in binned] == list(SNAP_BINS)


def test_the_snap_is_installed_on_every_path_a_colour_can_take():
    r"""Three hooks, because a colour reaches the page three ways.

    ``\pgfutil@colorlet`` is the drawing one, and it is deliberately not pgf's
    ``\applycolormixins``: tikz sends a bare colour word or a mix through its
    ``.unknown`` key to ``\tikz@compat@color@set``, which never calls the mixin
    hook. The other two are xcolor's own -- expressions take the undeclared
    path, plain names the declared fast path.
    """
    style = (Path(__file__).resolve().parents[1] / "tex" / "latexally-core.sty").read_text()
    body = style.split("\\NewDocumentCommand \\accesssnapcolors")[1]
    for hook in ("\\pgfutil@colorlet", "\\XC@undeclaredcolor", "\\XC@declaredc@lor"):
        assert hook in body, f"{hook} is not patched; colours through it keep their hue"
    assert "\\bool_if:NF \\g__access_snap_bool" in body, (
        "installing twice would make the saved original the patched version"
    )
    palette = style.split("\\NewDocumentCommand \\accesspalette")[1].split("\\NewDocument")[0]
    assert "\\accesssnapcolors" in palette, "the palette mode does not turn snapping on"
    conforming = style.split("\\NewDocumentCommand \\accessconformingcolors")[1].split("\\NewDocument")[0]
    assert "\\accesssnapcolors" not in conforming, (
        "conforming mode exists for documents that must keep their own hues"
    )


def test_one_blue_reaches_both_text_and_drawings():
    """The mismatch this replaced, as an assertion about names.

    fa26/dis/00B draws its answer text with \\color{answerColor} and its answer
    vectors with \\addplot[..., blue]. Under the old scheme those resolved to
    #187AC4 and #0000FF: two blues, one page. \\accesspalette has to bind both
    spellings, and every other base name a figure might use, to a token.
    """
    style = (Path(__file__).resolve().parents[1] / "tex" / "latexally-core.sty").read_text()
    body = style.split("\\NewDocumentCommand \\accesspalette")[1].split("\\NewDocument")[0]
    for name in ("solutionColor", "solansColor", "answerColor", "blueish", "redish"):
        assert name in body, f"{name} is not remapped by \\accesspalette"
    for name in ("blue", "red", "green", "purple", "orange"):
        assert f"{{ {name} }}" in body, f"xcolor's {name} is not remapped; figures keep it"


# ---------------------------------------------------------------------- #
# the invisible text layer
# ---------------------------------------------------------------------- #

_TEXT_LAYER_DOC = (
    "\\documentclass[11pt]{article}\n"
    "\\usepackage{latexally-core}\n"
    "%s"
    "\\pagestyle{empty}\n"
    "\\begin{document}\\noindent\n"
    "\\described{A voltage divider with two resistors in series.}"
    "{\\framebox[3in]{\\rule{0pt}{1in}SECRETLABEL}}\n"
    "\\end{document}\n"
)


def _text_layer_pair(tmp_path_factory):
    work = tmp_path_factory.mktemp("textlayer")
    on = _compile(work, "on", _TEXT_LAYER_DOC % "")
    off = _compile(work, "off", _TEXT_LAYER_DOC % "\\accesssetup{text-layer=false}\n")
    return on, off


def test_text_layer_reaches_viewers_that_ignore_tags(tmp_path_factory):
    """The description must be EXTRACTABLE, not merely present as /Alt.

    macOS PDFKit -- Preview, Quick Look, VoiceOver-in-Preview -- ignores both
    the tag tree and /ActualText, so a figure described only by /Alt is read to
    those users as its own stray labels. Verified against the real viewer.
    """
    import pymupdf

    on, off = _text_layer_pair(tmp_path_factory)
    assert "A voltage divider" in pymupdf.open(on)[0].get_text()
    assert "A voltage divider" not in pymupdf.open(off)[0].get_text()


def test_text_layer_does_not_change_layout(tmp_path_factory):
    """It must be invisible in the strict sense: not one pixel may move.

    This is what lets the fidelity fixture switch the layer off and still be
    measuring the same document.
    """
    on, off = _text_layer_pair(tmp_path_factory)
    assert strongly_differing_fraction(on, off) == 0.0


def test_a_drawing_and_the_prose_beside_it_come_out_one_colour(tmp_path: Path):
    r"""The bug this palette exists for, read back out of a PDF.

    fa26/dis/01A draws with ``green!70!black``. Under the old text palette that
    resolved to #004700 -- 1.90:1 against the black axes beside it, which is
    what "the green looks black" was. The answer was a second, lighter palette
    for drawings, and that produced the next report: the prose green and the
    plotted green were visibly two greens on one page.

    Now the mix snaps whole, to the same token the prose uses. One green.
    """
    pdf = _compile(
        tmp_path,
        "ink",
        "\\DocumentMetadata{lang=en-US,pdfversion=1.7,tagging=on}\n"
        "\\documentclass{article}\n"
        "\\usepackage{tikz}\n"
        "\\usepackage{latexally-core}\n"
        "\\accesssetup{palette}\n"
        "\\begin{document}\n"
        "Prose in {\\color{green}green}.\n"
        "\\begin{tikzpicture}\n"
        "  \\draw[green!70!black, thick] (0,0) -- (2,1);\n"
        "  \\draw[teal, thick] (0,1) -- (2,2);\n"
        "\\end{tikzpicture}\n"
        "\\end{document}\n",
    )
    import pymupdf

    page = pymupdf.open(pdf)[0]
    strokes = {
        "#%02X%02X%02X" % tuple(round(channel * 255) for channel in drawing["color"])
        for drawing in page.get_drawings()
        if drawing.get("color")
    }
    text = {
        f"#{span['color']:06X}"
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["color"]
    }

    assert text == {"#007900"}, "prose takes the palette green"
    assert strokes == {"#1754FF", "#007900"}, (
        "teal snaps into the one blue and green!70!black into that same green"
    )
    assert "#004700" not in strokes, "the near-black green is what this replaced"
    assert "#00A300" not in strokes, "the separate drawing palette is gone"


def test_every_spelling_of_a_hue_comes_out_of_the_pdf_as_one_colour(tmp_path: Path):
    r"""The whole point, measured on a compiled page rather than on a rule.

    These are real spellings from questionBank, in the proportions it uses them:
    a bare word, a mix off black, a mix off another hue, a wash, and the
    ``steelblue31119180`` a matplotlib pgfplots export declares in the preamble.
    Rebinding names reached exactly one of them. Nine lines and four fills go in;
    one blue has to come out.

    The prose is here too, because it must join them: ``\textcolor{red!60}``
    snaps to the same red a ``\draw[red]`` would take.
    """
    pdf = _compile(
        tmp_path,
        "snap",
        "\\documentclass{article}\n"
        "\\usepackage{tikz}\n"
        "\\usepackage{latexally-core}\n"
        "\\accesssetup{palette}\n"
        "\\definecolor{steelblue31119180}{RGB}{31,119,180}\n"
        "\\definecolor{lightblue}{rgb}{0.5,0.8,1}\n"
        "\\definecolor{mydarkblue}{rgb}{0,0.25,1}\n"
        "\\definecolor{darkgray176}{RGB}{176,176,176}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        "A mix in \\textcolor{red!60}{prose}.\n"
        "\\begin{tikzpicture}\n"
        "  \\draw[blue] (0,0) -- (1,0);\n"
        "  \\draw[blue!40!black] (0,1) -- (1,1);\n"
        "  \\draw[cyan] (0,2) -- (1,2);\n"
        "  \\draw[cyan!70!blue] (0,3) -- (1,3);\n"
        "  \\draw[steelblue31119180] (0,4) -- (1,4);\n"
        "  \\draw[lightblue] (0,5) -- (1,5);\n"
        "  \\draw[mydarkblue] (0,6) -- (1,6);\n"
        "  \\draw[teal] (0,7) -- (1,7);\n"
        "  \\draw[color=lightblue] (0,8) -- (1,8);\n"
        "  \\fill[blue!20] (2,0) rectangle (2.5,0.5);\n"
        "  \\fill[steelblue31119180] (2,1) rectangle (2.5,1.5);\n"
        "  \\node[fill=cyan] at (3,2) {n};\n"
        "  \\draw[black!45!green] (4,0) -- (5,0);\n"
        "  \\draw[green!70!black] (4,1) -- (5,1);\n"
        "  \\draw[green] (4,2) -- (5,2);\n"
        "  \\draw[gray!40] (6,0) -- (7,0);\n"
        "  \\draw[darkgray176] (6,1) -- (7,1);\n"
        "  \\draw[black] (6,2) -- (7,2);\n"
        "\\end{tikzpicture}\n"
        "\\end{document}\n",
    )
    import pymupdf

    page = pymupdf.open(pdf)[0]
    drawn = {
        "#%02X%02X%02X" % tuple(round(channel * 255) for channel in value)
        for drawing in page.get_drawings()
        for key in ("color", "fill")
        if (value := drawing.get(key))
    }
    text = {
        f"#{span['color']:06X}"
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["color"]
    }

    assert drawn == {
        "#1754FF",  # nine blue lines, three blue fills, however they were spelled
        "#007900",  # green, black!45!green, green!70!black
        "#000000",  # and the achromatic three, left where they were
        "#B0B0B0",
        "#CCCCCC",
    }, "a hue reached the page as more than one colour"
    assert text == {"#D20000"}, "prose snaps to the same red the drawings take"


@pytest.mark.parametrize(
    "metadata",
    ["", "\\DocumentMetadata{lang=en-US,pdfversion=1.7,tagging=on}\n"],
    ids=["classic", "tagged"],
)
def test_a_reference_takes_the_colour_of_the_prose_it_sits_in(tmp_path: Path, metadata):
    r"""``linkcolor`` overrides the prose, and questionBank sets it to black.

    Every solution file in the corpus loads hyperref with ``colorlinks=true,
    linkcolor=black``, so an ``\eqref`` inside the blue ``\sol`` came out black
    -- a hole in the sentence that the colour is there to mark as the solution.

    Both hyperref drivers, because they need different switches and only one of
    them is the one this tool emits. ``linkcolor=.`` is enough for classic
    hyperref and is silently ignored under ``tagging=on``, where the colour goes
    through l3color rather than xcolor -- so the tagged case is the one that
    would have shipped the bug.
    """
    pdf = _compile(
        tmp_path,
        "linkcolor",
        metadata + "\\documentclass{article}\n"
        "\\usepackage{amsmath,hyperref}\n"
        "\\hypersetup{colorlinks=true,linkcolor=black,urlcolor=blue}\n"
        "\\usepackage{latexally-core}\n"
        "\\accesssetup{palette}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        "\\begin{equation}x = 1\\label{eq:one}\\end{equation}\n"
        "{\\color{blue}Solution: from \\eqref{eq:one} we get $x$.}\n"
        "\\par Body text, see \\eqref{eq:one}.\n"
        "\\end{document}\n",
    )
    import pymupdf

    lines = [
        (
            "".join(span["text"] for span in line["spans"]),
            {f"#{span['color']:06X}" for span in line["spans"]},
        )
        for block in pymupdf.open(pdf)[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
    ]
    solution = next(colors for text, colors in lines if text.startswith("Solution"))
    body = next(colors for text, colors in lines if text.startswith("Body"))

    assert solution == {"#1754FF"}, "the reference broke the blue of the solution"
    assert body == {"#000000"}, "a reference in body prose should stay black"

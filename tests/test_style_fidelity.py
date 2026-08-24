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
        "\\begin{{qunlist}}\\qns{{Q}}\n\\sol{{Coloured solution text.}}\n"
        "\\end{{qunlist}}\n\\end{{document}}\n"
    )
    conforming = _compile(work, "conforming", document.format(options="sol"))
    house = _compile(work, "house", document.format(options="sol,housecolors"))

    # The exact ink colour, not a pixel fraction: reading the span colour is
    # both stricter than a pixel count and independent of its threshold.
    #
    # #187AC4 is the smallest darkening of the course's #3399E6 that clears
    # 4.5:1 (it measures 4.55:1). It replaced #0645AD, which conformed at
    # 8.53:1 but overshot the floor so far that it read as harder than the
    # blue it was fixing.
    assert _solution_colors(conforming) == {0x187AC4}, (
        "the default palette is no longer the minimum-change blue"
    )
    assert _solution_colors(house) == {0x0000FF}, (
        "[housecolors] did not restore the course's original blue"
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


def test_conforming_palette_actually_meets_the_contrast_floor():
    """The reason the default exists, stated as numbers rather than as a belief.

    Worth being precise about which colour is the problem. Plain ``blue`` --
    what the homework drivers use for ``\\sol`` -- is #0000FF and measures
    8.59:1, comfortably conforming. The failure is ``solutionColor``, defined by
    the discussion preambles as rgb(0.2, 0.6, 0.9) = #3399E6, at 2.6:1. The
    conforming palette replaces both so that one rule covers every document,
    and the replacement is checked here to be no worse than either.
    """
    from latexally.check.contrast import contrast_ratio

    white = (1.0, 1.0, 1.0)
    ratio = lambda hexed: contrast_ratio(
        tuple(component / 255 for component in hexed), white
    )

    assert ratio((0x33, 0x99, 0xE6)) < 4.5, "solutionColor should be the failing one"
    assert ratio((0x00, 0x00, 0xFF)) >= 4.5, "plain blue conforms; do not claim otherwise"
    assert ratio((0x18, 0x7A, 0xC4)) >= 4.5, "the replacement must conform"
    # It clears the floor and stops. Overshooting is the failure mode this
    # replaced: #0645AD reached 8.53:1, nearly twice what was asked for, and
    # was reported as harder to read than the colour it fixed. Assert the
    # ceiling as well as the floor, so a future "safer" darker value fails
    # here rather than in someone's eyes.
    assert 4.5 <= ratio((0x18, 0x7A, 0xC4)) < 5.0
    assert ratio((0x18, 0x7A, 0xC4)) > ratio((0x33, 0x99, 0xE6))


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

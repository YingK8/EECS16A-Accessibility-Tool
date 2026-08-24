"""Validate what a screen reader says, as an assertion.

Every other test here checks that something is *present*: the tag exists, the
/Alt is not a placeholder, no text is drawn outside marked content. A document
can pass all of them and still be read out wrong, because reading order is the
one property that lives in neither the tag tree nor the content stream but in
how they interleave.

This is the test that would have caught "it reads the question number and then
stops": it compiles a document, walks it the way a reader does, and asserts on
the sequence of utterances.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pikepdf", reason="PDF assertions need the [pdf] extra")
pytestmark = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")

REPO = Path(__file__).resolve().parent.parent
TEXDIR = REPO / "tex"

#: A question whose prose and math interleave, which is the shape that breaks.
DOCUMENT = r"""
\DocumentMetadata{lang=en-US,pdfversion=2.0,testphase={phase-III,math}}
\documentclass{article}
\usepackage{amsmath}
\usepackage{latexally-math}
\begin{document}
\section{Noisy Images}
Consider the problem of reconstructing an image $\vec{i}$ from measurements
captured by a single pixel camera. Each measurement $s_i$ is obtained by
applying a mask.
\end{document}
"""


def build(work: Path, name: str = "speech") -> Path:
    """Compile, convert the math, compile again. Returns the PDF."""
    import os

    from latexally.mathspeech import DRIVER, convert, read_dummy, write_sources

    (work / f"{name}.tex").write_text(DOCUMENT)
    env = {"TEXINPUTS": f"{TEXDIR}:{work}:", "PATH": os.environ["PATH"]}

    def run() -> None:
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", f"{name}.tex"],
            cwd=work,
            env=env,
            capture_output=True,
            timeout=180,
            check=False,
        )

    run()
    dummy = work / f"{name}-mathml-dummy.html"
    if dummy.is_file() and DRIVER.is_file():
        formulas = read_dummy(dummy)
        if formulas:
            write_sources(convert(formulas, cache=work / "cache.json"), formulas, name, work)
    for _ in range(3):
        run()
    return work / f"{name}.pdf"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    pdf = build(tmp_path_factory.mktemp("speech"))
    if not pdf.is_file():
        pytest.skip("pdflatex produced no PDF")
    return pdf


@pytest.fixture(scope="module")
def utterances(built: Path):
    from latexally.check.speech import spoken_utterances

    return spoken_utterances(built)


def spoken(utterances) -> list[str]:
    return [u.text for u in utterances]


def squeeze(text: str) -> str:
    """Drop every space.

    pdflatex kerns inside words -- "measurements" is drawn as "measuremen ts" --
    so a literal prefix match tests the font, not the reading order.
    """
    return "".join(text.split())


def index_of(utterances, phrase: str) -> int:
    """Position of the first utterance containing `phrase`, spaces ignored."""
    wanted = squeeze(phrase)
    for position, utterance in enumerate(utterances):
        if wanted in squeeze(utterance.text):
            return position
    raise AssertionError(f"never announced: {phrase!r} in {spoken(utterances)}")


def test_the_heading_is_announced_before_its_body(utterances):
    """The reported symptom: a reader that stops after the question number."""
    heading = index_of(utterances, "Noisy Images")
    body = index_of(utterances, "Consider the problem")

    assert heading < body, f"body announced before its heading: {spoken(utterances)}"


def test_prose_and_math_interleave_in_source_order(utterances):
    r"""The bug this file exists for.

    A paragraph with inline math owns mcids 7, 9, 11 while its `Formula`
    children own 8, 10, 12. Announcing all the prose and then all the formulas
    passes every structural check and is a different document: the reader hears
    a paragraph with holes in it, followed by a list of disconnected symbols.
    """
    order = [
        index_of(utterances, "reconstructing an image"),
        next(i for i, u in enumerate(utterances) if u.source == "alt"),
        index_of(utterances, "from measurements"),
        next(i for i, u in enumerate(utterances) if u.source == "alt" and i > 3),
        index_of(utterances, "applying a mask"),
    ]

    assert order == sorted(order), (
        f"math and prose are announced out of order: {spoken(utterances)}"
    )


def test_every_word_of_the_question_is_reachable(utterances):
    """Nothing is present-but-never-spoken."""
    squeezed = squeeze(" ".join(spoken(utterances)))
    for phrase in ("NoisyImages", "reconstructinganimage", "singlepixelcamera", "applyingamask"):
        assert phrase in squeezed, f"{phrase!r} is in the PDF but never announced"


def test_formulas_are_spoken_as_words_not_latex(utterances):
    said = [u.text for u in utterances if u.source == "alt"]

    assert said, "no formula was announced by its /Alt"
    for text in said:
        assert "\\" not in text and "$" not in text, f"LaTeX reached a reader: {text!r}"
        assert "LaTeX formula" not in text


def test_reading_order_does_not_run_backwards(built: Path):
    """Tree order must not jump backwards against the content stream.

    Not a style point: a backwards jump is a reader announcing the page out of
    sequence. This is the measurement that showed the interleaving bug -- 185
    inversions in one real homework before the fix, 6 after, and the six that
    remain are headings, which legitimately invert because a list label is
    tagged before the heading it introduces and painted after it.
    """
    from latexally.check.structure import read_structure

    structure = read_structure(built)
    painted: list[tuple[int, int, str]] = []

    def visit(index: int) -> None:
        node = structure.nodes[index]
        for kind, position in node.order:
            if kind == "el":
                visit(position)
            elif position < len(node.pages) and node.pages[position] is not None:
                painted.append((node.pages[position], node.mcids[position], node.tag))

    for root, node in enumerate(structure.nodes):
        if node.parent is None:
            visit(root)

    highest: dict[int, int] = {}
    backwards = []
    for page, mcid, tag in painted:
        if page in highest and mcid < highest[page] and not tag.startswith("H"):
            backwards.append((page, highest[page], mcid, tag))
        highest[page] = max(highest.get(page, -1), mcid)

    assert backwards == [], f"content is announced out of page order: {backwards}"


def test_nothing_readable_is_buried_under_an_alt(tmp_path_factory):
    """Text inside an element carrying /Alt is never announced.

    `ALLY-PDF-031` catches the content-stream form of this. The structure-tree
    form survives a clean content stream, so it needs its own check.
    """
    from latexally.check.speech import unreachable_text

    pdf = build(tmp_path_factory.mktemp("buried"), "buried")
    if not pdf.is_file():
        pytest.skip("pdflatex produced no PDF")

    lost = unreachable_text(pdf)

    assert lost == [], f"tagged text that no reader will ever reach: {lost}"


def test_extracted_text_speaks_the_maths_too(built: Path):
    """The half that serves readers which ignore tags entirely.

    macOS Preview, and every "read aloud" built on positional text extraction,
    never look at the structure tree -- so /Alt is invisible to them and they
    announce the raw glyphs of an equation. They do substitute /ActualText, but
    only from the *marked-content* property list: /ActualText on the structure
    element was tried and **[verified]** to change extraction not at all.
    """
    pymupdf = pytest.importorskip("pymupdf", reason="extraction check needs PyMuPDF")

    extracted = "".join(pymupdf.open(built)[0].get_text().split())

    # The document's two formulas are \vec{i} and s_i. MathCAT calls the first
    # one "vector i"; the Speech Rule Engine used to read the accent literally,
    # as "i rightarrow".
    assert "vectori" in extracted, (
        "a positional extractor still sees the glyphs, not the description"
    )
    assert "ssubi" in extracted

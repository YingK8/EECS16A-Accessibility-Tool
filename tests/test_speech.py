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


# ---------------------------------------------------------------------- #
# the audit, checked against defects planted on purpose
# ---------------------------------------------------------------------- #


def _audit_with(monkeypatch, utterances, unreachable=()):
    """Run the audit over a fabricated transcript."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import speech_audit

    monkeypatch.setattr(speech_audit, "spoken_utterances", lambda _p: utterances)
    monkeypatch.setattr(speech_audit, "unreachable_text", lambda _p: list(unreachable))
    return speech_audit.audit_speech(Path("unused.pdf"))


class _Said:
    """One utterance, as speech_audit consumes it."""

    def __init__(self, text, tag="Figure"):
        self.text, self.tag, self.node, self.source = text, tag, 0, "alt"


def test_the_audit_hears_silence(monkeypatch):
    """An /Alt of punctuation is silence, and the checker counts it described.

    `if not alt` is False for " " and for "-", so both reach a listener as
    nothing at all while every count in the report says the figure has a
    description.
    """
    for quiet in (" ", "-", "...", ""):
        audit = _audit_with(monkeypatch, [_Said(quiet)])
        assert audit.of_kind("silence"), f"{quiet!r} should read as silence"


def test_the_audit_hears_markup_and_named_symbols(monkeypatch):
    r"""Two ways a description reaches the reader as gibberish.

    Markup is uttered verbatim: ``\vec{x}`` is "backslash vec x". A named
    symbol is worse, because it looks like prose: "x underscore i" is what a
    converter writes when it gives up, and nothing downstream flags it.
    """
    assert _audit_with(monkeypatch, [_Said(r"the vector \vec{x} shown")]).of_kind("markup")
    assert _audit_with(monkeypatch, [_Said("x underscore i against t")]).of_kind("named-symbol")
    assert _audit_with(monkeypatch, [_Said("1000cmd/emph/after0Interpretation")]).of_kind(
        "internals"
    )


def test_the_audit_passes_ordinary_prose(monkeypatch):
    """It must stay quiet on the descriptions people actually write.

    Including the ones whose words merely contain a symbol name: "understated"
    is not "underscore", and a colon is ordinary punctuation in this corpus.
    """
    clean = [
        _Said("Scatter plot on x and y axes, four transactions marked."),
        _Said("Block diagram: input x enters block A, then block B, giving y."),
        _Said("An understated curve rising to one comma one."),
    ]
    assert _audit_with(monkeypatch, clean).ok


def test_the_audit_reports_text_a_reader_never_reaches(monkeypatch):
    """Words in the tag tree that no reader announces are lost, not present."""
    audit = _audit_with(monkeypatch, [_Said("fine", "P")], [("Figure", "swallowed prose")])
    assert audit.of_kind("unreachable")


def test_prose_may_carry_what_an_alt_may_not(monkeypatch):
    r"""A dollar sign is money in a paragraph and LaTeX in a description.

    Calibrated against the corpus, not guessed: "$0.12 per kilowatt-hour" and
    "PG&E" are real sentences in fa23/dis/08A, and the content reader returns
    raw show-text operators so the `fl` ligature in "flowing" arrives as \x03.
    Flagging those reported the extractor rather than the document -- 11 of 109
    PDFs failed on it. Substitute text is held to the stricter standard, because
    an /Alt is what a reader says *instead of* the content.
    """
    prose = [
        _Said("Suppose PG&E charges $0.12 per kilowatt-hour.", "P"),
        _Said("What are the currents \x03owing into the terminals?", "P"),
    ]
    for said in prose:
        said.source = "content"
    assert _audit_with(monkeypatch, prose).ok

    described = _Said("the region $x_1$ shaded", "Figure")
    described.source = "alt"
    assert _audit_with(monkeypatch, [described]).of_kind("markup")


def test_a_backslash_is_wrong_wherever_it_appears(monkeypatch):
    """notes_fa25/note20 ships a heading reading `Design Example: \\Almost"`.

    Nothing in this corpus says a backslash aloud on purpose, so unlike the
    dollar sign it needs no exemption for prose.
    """
    heading = _Said(r'Design Example: \\Almost" current source', "H1")
    heading.source = "content"
    assert _audit_with(monkeypatch, [heading]).of_kind("markup")


def test_prose_the_alt_does_say_is_not_reported_as_lost(monkeypatch):
    r"""An align's /Alt speaks the prose between its rows, so it is not lost.

    "Replaced" is not "lost". The words reach the listener through the
    substitute rather than the subtree, interleaved with equation numbers that
    are not in the /Alt at all -- so demanding the buried text appear verbatim
    can never pass. Prose that is entirely absent is the real defect.
    """
    said = _Said("fine", "P")
    audit = _audit_with(
        monkeypatch,
        [said],
        [("equation 3; Subtract: the referenced equation; y", "(1) (2) Subtract: ( )")],
    )
    assert audit.ok, "the /Alt says 'Subtract', so nothing was lost"


def test_prose_the_alt_never_says_is_still_reported(monkeypatch):
    """The original defect: an /Alt that speaks none of what it replaced."""
    said = _Said("fine", "P")
    audit = _audit_with(
        monkeypatch,
        [said],
        [("2 x plus 3 y is equal to 5", "Subtract the first equation from the second")],
    )
    assert audit.of_kind("unreachable")

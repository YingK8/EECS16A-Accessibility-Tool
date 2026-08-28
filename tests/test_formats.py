r"""What the alternative formats actually contain.

Every other PDF test in this suite asks whether the artefact is *correct*. This
one asks whether the correctness survives the trip to a student, which is a
different question and the one that was never being asked.

The gap it closes, stated once: ``/Alt`` and ``/ActualText`` are instructions to
a reader, and readers disagree about whether to follow them. Measured on one
spec-correct build of ``fa26/dis/00B``, on ``$a = 1 - i\sqrt{3}$`` -- five
engines, three answers:

    structure tree   a is equal to, 1 minus, i times the square root of 3
    PDFBox           a is equal to, 1 minus, i times the square root of 3
    PyMuPDF          a is \ne\nqual to, ... of \n3        (span fragmented)
    poppler          1-i 3                                (/ActualText ignored)
    Ghostscript      Let a = 1-i sqrt3                    (/ActualText ignored)

Canvas Ally is a Java service built on PDFBox, so the second row is the one a
student hears and the one ``ALLY-FMT-001`` is written against.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from latexally.check.formats import (
    ALLY_EXTRACTOR,
    Extraction,
    _GLYPH_SOUP,
    _pdfbox_jar,
    check_formats,
    extract_all,
    render_audio,
    render_braille,
    write_evidence,
)
from latexally.check.structure import read_structure

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_core.pdf"

#: PDFBox is the only extractor whose absence makes a test meaningless rather
#: than merely narrower, because it is the one standing in for Ally.
needs_pdfbox = pytest.mark.skipif(
    not shutil.which("java") or not _pdfbox_jar().is_file(),
    reason="PDFBox needs java and vendor/pdfbox/pdfbox-app.jar",
)


# ---------------------------------------------------------------------- #
# comparison
# ---------------------------------------------------------------------- #


def test_says_ignores_the_whitespace_tex_invents():
    """TeX kerns inside words, so a rendered word arrives as several strings.

    "pixel" comes back as "pix el" from every extractor that reads show-text
    operators. A substring test on the raw text therefore fails on *correct*
    output, which would make this whole tier report false failures and be
    switched off within a week.
    """
    extraction = Extraction("t", text="a red squ are used as a bullet")
    assert extraction.says("A red square used as a bullet")
    assert not extraction.says("A blue square used as a bullet")


def test_an_unavailable_extractor_is_skipped_and_says_why():
    """A missing tool narrows the evidence. It must never fail the run, and it
    must never look like a pass either."""
    extraction = Extraction("poppler", skipped="pdftotext not found")
    assert not extraction.ran
    assert not extraction.says("anything at all")


# ---------------------------------------------------------------------- #
# the extractor matrix
# ---------------------------------------------------------------------- #


def test_every_extractor_either_reads_the_pdf_or_explains_itself():
    found = extract_all(GOLDEN)
    assert set(found) == {"structure", ALLY_EXTRACTOR, "mupdf", "poppler", "ghostscript"}
    for name, extraction in found.items():
        assert extraction.ran or extraction.skipped, f"{name} returned neither"


@needs_pdfbox
def test_the_ally_extractor_carries_every_description():
    """The assertion the whole tier exists for.

    golden_core.pdf has three described figures. If PDFBox cannot see one of
    them, Ally's MP3 and braille do not contain it -- and nothing else in this
    suite would notice, because the structure tree is perfect either way.
    """
    structure = read_structure(GOLDEN)
    described = [node.alt for node in structure.nodes if node.alt]
    assert described, "the fixture must have described figures to be a fixture"

    ally = extract_all(GOLDEN, structure=structure)[ALLY_EXTRACTOR]
    for alt in described:
        assert ally.says(alt), f"{ALLY_EXTRACTOR} lost: {alt[:60]!r}"


@needs_pdfbox
def test_the_extractors_that_ignore_actualtext_are_reported_not_hidden():
    """poppler and Ghostscript drop substitute text by design.

    That is not a defect in the PDF and it is not something this tool can fix,
    so it is a warning rather than an error -- but it is never silent. A
    "works everywhere" claim made without measuring is how the previous round
    of this shipped.
    """
    rules = {finding.rule for finding in check_formats(GOLDEN)}
    assert "ALLY-FMT-002" in rules

    findings = [f for f in check_formats(GOLDEN) if f.rule == "ALLY-FMT-002"]
    assert {f.data["extractor"] for f in findings} == {"poppler", "ghostscript"}
    assert all(f.severity == "warning" for f in findings)


@needs_pdfbox
def test_a_described_fixture_raises_no_errors():
    """The fixture is what conversion is supposed to produce, so it must pass.

    A tier that cannot go clean is a tier people learn to ignore.
    """
    errors = [f for f in check_formats(GOLDEN) if f.severity == "error"]
    assert not errors, [f"{f.rule}: {f.message}" for f in errors]


def test_an_untagged_pdf_is_one_finding_not_a_crash(tmp_path):
    """Nothing downstream can carry a description out of an untagged file, so
    there is exactly one thing to say and no point saying more.

    Built with pikepdf, NOT pymupdf, and that is not a preference. PyMuPDF's C
    extension segfaults when it is imported into a process that has already
    loaded pikepdf -- and `check_formats` always has, because `read_structure`
    reads the tag tree with pikepdf. Importing it here took the whole
    interpreter down mid-run, which pytest reports as a silent hang on whichever
    test happens to be sixth. `_extract_mupdf` runs out-of-process for the same
    reason; a test may not do what the code it tests is careful not to.
    """
    import pikepdf

    plain = tmp_path / "plain.pdf"
    with pikepdf.new() as document:
        document.add_blank_page()
        document.save(plain)

    findings = check_formats(plain)
    assert [f.rule for f in findings] == ["ALLY-FMT-001"]
    assert "not tagged" in findings[0].message


# ---------------------------------------------------------------------- #
# glyph soup
# ---------------------------------------------------------------------- #


def test_glyph_soup_matches_an_undescribed_plot():
    r"""The literal text an undescribed pgfplots axis extracts as.

    Taken from fa26/dis/00B before its figures were described. A reader
    announces this as data, which is worse than announcing nothing: silence
    prompts a question and "minus two minus one one two three four" does not.
    """
    assert _GLYPH_SOUP.search("−2 −1 1 2 3 4 −3 −2 −1 1 2 3 Re Im ")


def test_glyph_soup_does_not_match_prose_with_numbers_in_it():
    """The rule has to survive ordinary sentences, or it gets switched off."""
    assert not _GLYPH_SOUP.search(
        "Two capacitors, C1 and C2, each connected from its own top node to ground."
    )
    assert not _GLYPH_SOUP.search("The answer is 42 volts across the 3 ohm resistor.")


# ---------------------------------------------------------------------- #
# evidence
# ---------------------------------------------------------------------- #


def test_evidence_is_written_per_extractor_with_a_machine_readable_report(tmp_path):
    """One transcript each, so a disagreement can be diffed rather than argued.

    Audio and braille are off here: they shell out to `say` and
    `lou_translate`, which are about the artefact a human inspects, not about
    what this test is checking.
    """
    import json

    evidence = write_evidence(GOLDEN, tmp_path, audio=False, braille=False)
    for name in ("structure", ALLY_EXTRACTOR, "mupdf", "poppler", "ghostscript"):
        assert evidence.transcripts[name].is_file()

    report = json.loads((tmp_path / "report.json").read_text())
    assert report["ally_extractor"] == ALLY_EXTRACTOR
    assert set(report["extractors"]) == set(evidence.transcripts)


def test_a_skipped_extractor_writes_the_reason_where_the_text_would_be(tmp_path):
    """So an empty transcript can never be mistaken for a silent document."""
    evidence = write_evidence(GOLDEN, tmp_path, audio=False, braille=False)
    for name, path in evidence.transcripts.items():
        body = path.read_text()
        assert body.strip(), f"{name} wrote an empty transcript"
        if body.startswith("[skipped]"):
            assert any(name in note for note in evidence.notes)


# ---------------------------------------------------------------------- #
# renderers
# ---------------------------------------------------------------------- #


def test_a_missing_renderer_returns_a_reason_rather_than_raising(tmp_path, monkeypatch):
    """These are evidence, not the shipped artefact. A machine without `say` or
    liblouis must still get its transcripts and its findings."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert render_audio("hello", tmp_path / "a.mp3")
    assert render_braille("hello", tmp_path / "a.brf")
    assert not (tmp_path / "a.mp3").exists()
    assert not (tmp_path / "a.brf").exists()


@pytest.mark.skipif(not shutil.which("lou_translate"), reason="needs liblouis")
def test_braille_is_translated_not_copied(tmp_path):
    """A BRF that is just the ASCII back again means the table never ran."""
    target = tmp_path / "out.brf"
    assert render_braille("the quick brown fox", target) == ""
    written = target.read_text().strip()
    assert written
    assert written != "the quick brown fox"

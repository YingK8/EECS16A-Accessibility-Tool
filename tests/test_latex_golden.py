"""End-to-end LaTeX tests: compile the golden fixture and assert on the PDF.

These are the tests that matter most, because the defects they catch are
invisible everywhere else. The `Described` leak in particular produced a clean
LaTeX log, a correct-looking tag tree, a correct `/Alt`, and zero tagpdf
warnings -- while still leaking every glyph of the figure to a screen reader.
Only inspecting the marked-content nesting in the content stream reveals it.

Skipped automatically when pdflatex or pikepdf is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "golden_core.tex"
TEXDIR = REPO / "tex"

#: The text drawn inside the Described region. It must never be readable.
FORBIDDEN = "THIS TEXT MUST NOT BE SPOKEN"

pytest.importorskip("pikepdf", reason="PDF assertions need the [pdf] extra")
pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not installed"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """Compile the fixture three times and return the PDF path.

    Three runs are not superstition: tagpdf resolves the structure tree's
    marked-content ids across runs via the .aux file. After a single run every
    /MCID in the tree reads 1 while the content stream numbers them 0..n, so the
    reading order is wrong and every structural assertion below fails.
    """
    work = tmp_path_factory.mktemp("golden")
    shutil.copy(FIXTURE, work / FIXTURE.name)
    for _ in range(3):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-file-line-error", FIXTURE.name],
            cwd=work,
            env={"TEXINPUTS": f"{TEXDIR}:", "PATH": __import__("os").environ["PATH"]},
            capture_output=True,
            timeout=180,
            check=False,
        )
    pdf = work / "golden_core.pdf"
    assert pdf.is_file(), "fixture did not produce a PDF"
    return pdf


@pytest.fixture(scope="module")
def log(built: Path) -> str:
    return (built.with_suffix(".log")).read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def structure(built: Path):
    from latexa11y.check.structure import read_structure

    return read_structure(built)


@pytest.fixture(scope="module")
def content(built: Path):
    from latexa11y.check.content import read_page_content

    return read_page_content(built, 0)


# ---------------------------------------------------------------------- #
# build health
# ---------------------------------------------------------------------- #


def test_build_has_no_errors(log: str):
    errors = [line for line in log.splitlines() if line.startswith("./golden_core.tex:")]
    assert not errors, "LaTeX errors:\n" + "\n".join(errors[:10])


def test_build_has_no_tagpdf_warnings(log: str):
    warnings = [line for line in log.splitlines() if "tagpdf Warning" in line]
    assert not warnings, "tagpdf warnings:\n" + "\n".join(warnings[:10])


def test_tagging_actually_ran(log: str):
    # The cheapest smoke test there is: absent or zero structure objects means
    # tagging silently did not run and the PDF is untagged.
    assert "Finalizing the tagging structure" in log


# ---------------------------------------------------------------------- #
# the suppression contract -- the reason this package exists
# ---------------------------------------------------------------------- #


def test_described_region_is_not_readable(content):
    """The Described body must not appear in anything a reader announces."""
    assert FORBIDDEN not in content.readable_text()


def test_described_text_sits_in_a_suppressing_element(content):
    regions = content.find_text(FORBIDDEN)
    assert regions, "fixture no longer contains the marker text"
    for region in regions:
        assert region.is_suppressed, (
            f"text is readable inside <{region.tag} mcid={region.mcid}> "
            f"with ancestors {region.ancestors}"
        )


def test_described_has_no_nested_readable_element(content):
    """No readable marked content may be nested inside the Figure.

    This is the exact defect that made the suppression fail silently: the body
    was boxed before the Figure was opened, so a TikZ node opened its own `text`
    sequence while tagging was still live.
    """
    figures = [region for region in content.regions if region.tag == "Figure"]
    assert figures
    figure_ids = {(region.tag, region.mcid) for region in figures}
    nested = [
        region
        for region in content.regions
        if region.tag not in ("Figure", "Artifact")
        and any(ancestor in figure_ids for ancestor in region.ancestors)
    ]
    assert not nested, f"readable elements nested inside a Figure: {nested}"


def test_decorative_content_is_an_artifact(content):
    assert any(region.tag == "Artifact" for region in content.regions)


def test_no_untagged_real_content(content):
    # Matterhorn checkpoint 01: real content outside any marked-content sequence.
    assert not content.untagged_text, f"untagged text: {content.untagged_text[:200]!r}"


# ---------------------------------------------------------------------- #
# structure, headings, bookmarks, metadata
# ---------------------------------------------------------------------- #


def test_document_is_tagged_and_marked(structure):
    assert structure.tagged
    assert structure.marked


def test_heading_hierarchy_is_h1_to_h4_without_skips(structure):
    levels = structure.heading_levels
    assert levels, "no headings found"
    assert levels[0] == 1, "first heading must be H1 (Matterhorn 14-002)"
    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, (
            f"heading level skips from H{previous} to H{current} (Matterhorn 14-003)"
        )
    assert levels == [1, 2, 3, 4]


def test_bookmarks_mirror_the_heading_tree(structure):
    assert structure.outline, "no PDF outline (WCAG 2.1 AA, technique PDF2)"
    assert [level for level, _ in structure.outline] == structure.heading_levels
    assert structure.outline[0][1] == "Homework 13"


def test_every_figure_has_meaningful_alt(structure):
    figures = structure.of_tag("Figure")
    assert len(figures) == 3
    for figure in figures:
        assert figure.alt, "Figure without /Alt (Matterhorn 13-004)"
        assert not figure.alt.endswith((".png", ".jpg", ".pdf"))
        assert "<<ALT:" not in figure.alt


def test_no_figure_uses_actualtext(structure):
    # Matterhorn 13-005: /ActualText where /Alt is more appropriate. /ActualText
    # is also unreliable across readers, so it must never be used for figures.
    for figure in structure.of_tag("Figure"):
        assert figure.actual_text is None


def test_language_and_title_metadata(structure):
    assert structure.lang == "en-US"  # Matterhorn 11-001
    assert structure.title  # Matterhorn 06-003 (dc:title)


def test_data_table_has_header_cells(structure):
    assert structure.of_tag("Table"), "no Table element"
    assert structure.of_tag("TH"), "data table produced no TH header cells"
    assert structure.of_tag("TD")


def test_layout_table_is_not_a_table(structure):
    # Exactly one Table element: the DataTable. The LayoutTable must degrade to
    # a non-table structure so a reader is not trapped in a meaningless grid.
    assert len(structure.of_tag("Table")) == 1


# ---------------------------------------------------------------------- #
# legacy markup through the compatibility shim
# ---------------------------------------------------------------------- #

LEGACY = REPO / "tests" / "fixtures" / "golden_legacy.tex"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("legacy")
    shutil.copy(LEGACY, work / LEGACY.name)
    for _ in range(3):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-file-line-error", LEGACY.name],
            cwd=work,
            env={"TEXINPUTS": f"{TEXDIR}:", "PATH": __import__("os").environ["PATH"]},
            capture_output=True,
            timeout=180,
            check=False,
        )
    pdf = work / "golden_legacy.pdf"
    assert pdf.is_file()
    return pdf


@pytest.fixture(scope="module")
def legacy_structure(legacy: Path):
    from latexa11y.check.structure import read_structure

    return read_structure(legacy)


def test_legacy_markup_compiles_without_errors(legacy: Path):
    log = legacy.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    assert not [ln for ln in log.splitlines() if ln.startswith("./golden_legacy.tex:")]
    assert "tagpdf Warning" not in log


def test_legacy_argumentless_title_is_recovered(legacy_structure):
    # body.tex files write `\def\title{Homework 9}`, which overwrites the
    # one-argument \title. Without recovery the document ships with no dc:title,
    # a PDF/UA metadata failure that is easy to miss.
    assert legacy_structure.title == "Homework 9"


def test_legacy_produces_heading_tags_for_block_level_headings(legacy_structure):
    """H1 for the masthead, and by default nothing below it.

    Everything under the masthead is inline in an EECS 16A document: a question
    title shares its paragraph with the body text that follows, "(a)" is a list
    label, and "Solution:" opens the same paragraph as the solution. PDF forbids
    a heading inside a paragraph, and forcing each onto its own paragraph
    inserts a \\parskip and moves the page.

    Measured: 74 of 362 \\qns calls in the live question bank (20%) are followed
    immediately by text, so the forced break is a real reflow, not a no-op.

    The default therefore preserves the page and puts all four levels in the
    bookmark tree. \\accessquestiontags opts into real H2 tags for courses that
    prefer the structure -- covered by the next test.
    """
    assert legacy_structure.heading_levels == [1]


def test_legacy_bookmark_tree_keeps_all_four_levels(legacy_structure):
    levels = [level for level, _ in legacy_structure.outline]
    assert levels[0] == 1
    assert set(levels) == {1, 2, 3, 4}
    titles = [title for _, title in legacy_structure.outline]
    assert titles[0] == "Homework 9"
    assert "Part (a)" in titles and "Solution" in titles


def test_legacy_bookmarks_are_clean_pdf_strings(legacy_structure):
    titles = [title for _, title in legacy_structure.outline]
    assert titles[0] == "Homework 9"
    assert "Question 1" in titles and "Part (a)" in titles and "Solution" in titles
    # Regression: colour commands used to leak into the outline as literal text
    # ("a11ySolutionSolution") and counters never expanded ("Question ").
    for title in titles:
        assert "a11y" not in title
        assert title.strip() == title and title.strip() != ""
        assert not title.rstrip().endswith(("Question", "Part ()"))


def test_legacy_qitem_inside_enumerate_does_not_leak_figure_text(legacy):
    from latexa11y.check.content import read_page_content

    content = read_page_content(legacy, 0)
    assert "R1" not in content.readable_text()


# ---------------------------------------------------------------------- #
# the deprecated vocabulary
# ---------------------------------------------------------------------- #

DEPRECATED = REPO / "tests" / "fixtures" / "golden_deprecated.tex"


@pytest.fixture(scope="module")
def deprecated(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("deprecated")
    shutil.copy(DEPRECATED, work / DEPRECATED.name)
    for _ in range(3):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-file-line-error", DEPRECATED.name],
            cwd=work,
            env={"TEXINPUTS": f"{TEXDIR}:", "PATH": __import__("os").environ["PATH"]},
            capture_output=True,
            timeout=180,
            check=False,
        )
    pdf = work / "golden_deprecated.pdf"
    assert pdf.is_file()
    return pdf


def test_deprecated_names_still_build(deprecated: Path):
    """AltOnly / \\altonly / FigureBlock / \\FigureDescription must not strand anyone.

    The rename was free -- zero corpus files used the old spellings -- but a
    document written against them mid-migration still has to compile.
    """
    log = deprecated.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    assert not [ln for ln in log.splitlines() if ln.startswith("./golden_deprecated.tex:")]


def test_deprecated_names_say_what_to_use_instead(deprecated: Path):
    log = deprecated.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    # LaTeX wraps a long warning and prefixes each continuation line with
    # "(latexa11y)", so the message has to be reassembled before matching.
    flat = " ".join(log.replace("(latexa11y)", " ").split())
    import re

    for old, new in (
        ("AltOnly", "Described"),
        ("\\altonly", "\\described"),
        ("FigureBlock", "DescribedFigure"),
        ("\\FigureDescription", "\\LongDescription"),
    ):
        # \s* around the quotes: \tl_to_str:n on a control sequence leaves a
        # trailing space, and the wrap can fall anywhere in the sentence.
        pattern = (
            rf"'{re.escape(old)}\s*'\s*is deprecated;\s*use\s*'{re.escape(new)}\s*'"
        )
        assert re.search(pattern, flat), (
            f"no deprecation notice pointing {old} at {new}"
        )


def test_deprecated_wrappers_still_actually_suppress(deprecated: Path):
    """A shim that warns correctly but leaks the figure would be worse than none."""
    from latexa11y.check.content import read_page_content

    content = read_page_content(deprecated, 0)
    readable = content.readable_text()
    assert "MUST NOT BE SPOKEN" not in readable
    assert "ALSO MUST NOT BE SPOKEN" not in readable


def test_deprecated_wrappers_still_produce_alt(deprecated: Path):
    from latexa11y.check.structure import read_structure

    figures = read_structure(deprecated).of_tag("Figure")
    assert len(figures) == 3
    for figure in figures:
        assert figure.alt


def test_question_tags_option_produces_h2(tmp_path_factory):
    """\\accessquestiontags turns question titles into real H2 tags.

    The opposite of the default: costs a forced \\par after each question title,
    buys a heading tag. Both behaviours are exercised so neither can regress.
    """
    from latexa11y.check.structure import read_structure

    work = tmp_path_factory.mktemp("qtags")
    source = LEGACY.read_text(encoding="utf-8").replace(
        r"\usepackage{latexa11y-compat-ee16}",
        "\\usepackage{latexa11y-compat-ee16}\n\\accessquestiontags",
    )
    name = "qtags.tex"
    (work / name).write_text(source, encoding="utf-8")
    for _ in range(3):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-file-line-error", name],
            cwd=work,
            env={"TEXINPUTS": f"{TEXDIR}:", "PATH": __import__("os").environ["PATH"]},
            capture_output=True,
            timeout=180,
            check=False,
        )
    structure = read_structure(work / "qtags.pdf")
    assert structure.heading_levels == [1, 2, 2, 2]

"""The conversion engine's guarantees, without compiling anything.

The expensive end-to-end proof lives in ``test_latex_golden.py``. What is
checked here is the part that cannot be seen in a PDF: that mirror mode really
does leave the corpus untouched, that in-place mode refuses when its edits could
not be undone, and that a log with a fatal error is never read as a clean build.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from latexally.build import (
    _log_findings,
    inject,
    materialise,
    mirror_dependencies,
    require_clean_worktree,
)
from latexally.config import CorpusScope, Profile
from latexally.errors import LatexAllyError
from latexally.run import Output, RunConfig
from latexally.discover import Assignment
from latexally.texlex import TexSource

LINES = [
    "\\DocumentMetadata{lang=en-US,pdfversion=2.0,testphase={phase-III}}",
    "\\usepackage{latexally-ee16}",
]


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "sem" / "hw" / "3").mkdir(parents=True)
    (root / "shared.sty").write_text("% shared\n")
    (root / "sem" / "hw" / "3" / "sol3.tex").write_text(
        "\\documentclass{article}\n\\usepackage{../../../shared}\n\\input{body}\n"
    )
    (root / "sem" / "hw" / "3" / "body.tex").write_text(
        "\\begin{document}\nHello.\n\\end{document}\n"
    )
    return root


@pytest.fixture
def profile(corpus: Path) -> Profile:
    return Profile(name="test", corpus=CorpusScope(root=corpus, include=("**/*.tex",)))


@pytest.fixture
def assignment() -> Assignment:
    return Assignment(path="sem/hw/3", kind="homework", driver="sol3.tex", tex_files=2)


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------- #
# injection
# ---------------------------------------------------------------------- #


def test_injection_adds_only_the_requested_lines(corpus: Path):
    driver = corpus / "sem" / "hw" / "3" / "sol3.tex"
    source = TexSource.from_path(driver)
    result = inject(source, LINES)
    # Every original line survives, in order, unmodified.
    original = source.text.splitlines()
    assert [line for line in result.splitlines() if line in original] == original
    assert result.splitlines()[0] == LINES[0]


def test_document_metadata_leads_the_file(corpus: Path):
    """Anywhere but the first line and the kernel ignores it, silently untagged."""
    driver = corpus / "sem" / "hw" / "3" / "sol3.tex"
    result = inject(TexSource.from_path(driver), LINES)
    assert result.startswith("\\DocumentMetadata{")


def test_packages_land_before_the_body_input(corpus: Path):
    driver = corpus / "sem" / "hw" / "3" / "sol3.tex"
    lines = inject(TexSource.from_path(driver), LINES).splitlines()
    assert lines.index("\\usepackage{latexally-ee16}") < lines.index("\\input{body}")


def test_packages_land_before_begin_document_when_the_driver_has_one(tmp_path: Path):
    driver = tmp_path / "standalone.tex"
    driver.write_text("\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n")
    lines = inject(TexSource.from_path(driver), LINES).splitlines()
    assert lines.index("\\usepackage{latexally-ee16}") < lines.index("\\begin{document}")


def test_injection_refuses_a_file_that_is_not_a_driver(tmp_path: Path):
    fragment = tmp_path / "fragment.tex"
    fragment.write_text("Just a paragraph of a question.\n")
    with pytest.raises(LatexAllyError):
        inject(TexSource.from_path(fragment), LINES)


# ---------------------------------------------------------------------- #
# mirror mode leaves the corpus alone
# ---------------------------------------------------------------------- #


def test_mirror_leaves_the_corpus_byte_identical(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """The promise the default mode makes, checked by hashing every file."""
    before = _tree_digest(corpus)
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    materialise(assignment, config, profile, lines=LINES)
    assert _tree_digest(corpus) == before


def test_rewrites_land_in_the_mirror_and_never_in_the_corpus(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    r"""The tagging fixes obey the same rule the descriptions do.

    `\] \\` is ALLY-SRC-042: under tagging it fails with "There's no line here
    to end" and produces no PDF at all. It has to be fixed for the document to
    build, and it has to be fixed in the mirror.
    """
    from latexally.build import rewrite_incompatibilities

    body = corpus / "sem" / "hw" / "3" / "body.tex"
    body.write_text("\\begin{document}\n\\[\nx\n\\] \\\\\nHello.\n\\end{document}\n")
    before = _tree_digest(corpus)

    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    prepared = materialise(assignment, config, profile, lines=LINES)
    counts = rewrite_incompatibilities(prepared)

    assert counts == {"ALLY-SRC-042": 1}
    assert "\\mbox{}\\\\" in (prepared.work_dir / "body.tex").read_text()
    assert _tree_digest(corpus) == before, "the corpus must be byte-identical"


def test_the_unconverted_baseline_is_not_rewritten(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """It exists to be compiled as-is; rewriting it measures the tool against
    itself."""
    from latexally.build import rewrite_incompatibilities

    body = corpus / "sem" / "hw" / "3" / "body.tex"
    body.write_text("\\begin{document}\n\\[\nx\n\\] \\\\\nHello.\n\\end{document}\n")
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    prepared = materialise(assignment, config, profile, lines=LINES)
    rewrite_incompatibilities(prepared)

    if prepared.original is not None:
        assert "\\mbox{}" not in prepared.original.read_text()


def test_a_parallel_run_reports_in_the_order_it_was_asked_for(
    corpus: Path, profile: Profile, tmp_path: Path
):
    """`pool.map`, never `as_completed`.

    combine_logs, write_report and the CLI's report table all read `reports`
    positionally, and build-log.txt is an artefact people diff between runs. A
    dry run returns before any LaTeX, so this exercises the whole scheduler
    with no engine installed.
    """
    from latexally.build import build_run

    for name in ("1", "2", "4", "5"):
        directory = corpus / "sem" / "hw" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sol3.tex").write_text(
            "\\documentclass{article}\n\\input{body}\n"
        )
        (directory / "body.tex").write_text("\\begin{document}\nHi.\n\\end{document}\n")

    wanted = ("sem/hw/1", "sem/hw/2", "sem/hw/3", "sem/hw/4", "sem/hw/5")
    serial = RunConfig(assignments=wanted, output=Output(root=tmp_path / "a"), jobs=1)
    parallel = RunConfig(assignments=wanted, output=Output(root=tmp_path / "b"), jobs=4)

    one = [(r.assignment, r.variant) for r in build_run(serial, profile)]
    many = [(r.assignment, r.variant) for r in build_run(parallel, profile)]

    assert many == one
    assert [name for name, _ in one] == list(wanted)


def test_mirror_writes_a_converted_driver(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    prepared = materialise(assignment, config, profile, lines=LINES)
    assert prepared.driver.is_file()
    assert prepared.driver.read_text().startswith("\\DocumentMetadata{")
    # Siblings come along unconverted.
    assert (prepared.work_dir / "body.tex").is_file()


def test_mirror_carries_relative_dependencies_to_the_same_offsets(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """``../../../shared.sty`` must still be three hops up in the mirror.

    TEXINPUTS cannot fix a path spelled with ``../``: TeX resolves it against the
    directory, so a mirror carrying only the assignment's own files dies on line
    two no matter what the search path says.
    """
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    prepared = materialise(assignment, config, profile, lines=LINES)
    assert (prepared.work_dir / ".." / ".." / ".." / "shared.sty").resolve().is_file()


def test_mirror_search_path_puts_the_mirror_first(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """Regression: the corpus ahead of the mirror silently discarded every edit.

    kpathsea searches TEXINPUTS entries before the default (where "." lives), so
    ``\\input{body}`` found the ORIGINAL body.tex and the mirrored one was never
    read -- a conversion that appeared to work and changed nothing.
    """
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    prepared = materialise(assignment, config, profile, lines=LINES)
    assert prepared.search_path[0] == prepared.work_dir
    source_dir = (corpus / assignment.path).resolve()
    assert prepared.search_path.index(source_dir) > 0


def test_dependency_outside_the_output_root_is_skipped_not_written(tmp_path: Path):
    """A shallow assignment's ``../`` hops must not climb out of the output tree."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    outsider = tmp_path / "outsider.sty"
    outsider.write_text("% must not be copied\n")
    driver = source_dir / "main.tex"
    driver.write_text("\\usepackage{../outsider}\n\\begin{document}\\end{document}\n")

    # The assignment sits AT the mirror root, so its one ../ hop lands above it.
    mirror_root = tmp_path / "mirror"
    mirror_root.mkdir()
    copied = mirror_dependencies(driver, source_dir, mirror_root, mirror_root)
    assert copied == []
    # And nothing was scattered into the directory above the output tree.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "mirror",
        "outsider.sty",
        "src",
    ]


def test_the_driver_itself_is_never_copied_over_its_converted_form(tmp_path: Path):
    """Copying the original driver into the mirror would undo the conversion."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    driver = source_dir / "main.tex"
    driver.write_text("\\begin{document}\\end{document}\n")
    mirror_root = tmp_path / "mirror"
    target = mirror_root / "a"
    target.mkdir(parents=True)
    assert mirror_dependencies(driver, source_dir, target, mirror_root) == []
    assert not (target / "main.tex").exists()


# ---------------------------------------------------------------------- #
# in-place mode must stay undoable
# ---------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_in_place_refuses_outside_a_repository(corpus: Path):
    with pytest.raises(LatexAllyError, match="not a git repository"):
        require_clean_worktree(corpus)


def test_in_place_refuses_a_dirty_worktree(corpus: Path):
    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.email", "t@example.com")
    _git(corpus, "config", "user.name", "T")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-qm", "initial")
    require_clean_worktree(corpus)  # clean: allowed

    (corpus / "sem" / "hw" / "3" / "body.tex").write_text("edited\n")
    with pytest.raises(LatexAllyError, match="uncommitted"):
        require_clean_worktree(corpus)


def test_in_place_never_edits_the_corpus_sources(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """`in-place` means the PDF lands beside the original, nothing more.

    It used to mean the corpus .tex was rewritten, guarded by a clean git
    worktree. The conversion is now always mirrored whichever mode is chosen,
    so the source is byte-identical afterwards and only the finished document
    ever reaches the corpus.
    """
    before = _tree_digest(corpus)
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="in-place"), write=True
    )

    prepared = materialise(assignment, config, profile, lines=LINES)

    assert _tree_digest(corpus) == before
    assert corpus.resolve() not in prepared.driver.resolve().parents, (
        "the converted driver must live in the mirror, not in the corpus"
    )


# ---------------------------------------------------------------------- #
# log reading
# ---------------------------------------------------------------------- #


def test_absolute_path_errors_are_detected(tmp_path: Path):
    """Regression: matching only "./" reported a failed build as clean.

    A mirrored build is handed an absolute driver path, so every one of its
    errors is prefixed with "/" -- and the old scan counted zero.
    """
    log = tmp_path / "job.log"
    log.write_text(
        "/private/tmp/out/tex/sem/hw/3/body.tex:57: Emergency stop.\n"
        "./relative.tex:12: Missing number, treated as zero.\n"
        "Package tagpdf Warning: nested marked content found - mcid 34\n"
    )
    errors, warnings = _log_findings(log)
    assert len(errors) == 2
    assert len(warnings) == 1


def test_fatal_errors_without_a_file_prefix_are_detected(tmp_path: Path):
    log = tmp_path / "job.log"
    log.write_text(
        "! LaTeX Error: File `../../../timestamp.sty' not found.\n"
        "Fatal error occurred, no output PDF file produced!\n"
    )
    errors, _ = _log_findings(log)
    assert len(errors) == 2


def test_a_clean_log_reads_clean(tmp_path: Path):
    log = tmp_path / "job.log"
    log.write_text("This is pdfTeX\nOutput written on job.pdf (13 pages).\n")
    assert _log_findings(log) == ([], [])


def test_a_missing_log_reads_empty_rather_than_crashing():
    """A build that never wrote a log still has to report its failure.

    Raising here replaces the build error the user needs with an
    AttributeError from the reporting code -- which is exactly what happened.
    """
    assert _log_findings(None) == ([], [])
    assert _log_findings(Path("/nonexistent/job.log")) == ([], [])


# ---------------------------------------------------------------------- #
# output paths reach the engine correctly
# ---------------------------------------------------------------------- #


def test_artifact_paths_are_absolute(monkeypatch, tmp_path: Path):
    """`-o ally-out` must not land inside the directory being built.

    pdflatex is run with cwd set to the source/mirror directory, and resolves a
    relative -output-directory against THAT. A relative output root therefore
    wrote the PDF into the mirrored source tree, and the log lookup that
    followed found nothing and crashed. Every artifact path is absolute by the
    time it leaves the model.
    """
    monkeypatch.chdir(tmp_path)
    output = Output(root=Path("ally-out"))
    for directory in (
        output.pdf_dir(),
        output.log_dir(),
        output.tex_dir(),
        output.worklog_dir(),
        output.baseline_dir(),
    ):
        assert directory.is_absolute(), directory
        assert directory.is_relative_to(tmp_path)


def test_a_relative_override_is_absolute_too(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    output = Output(root=Path("out"))
    output.set_path("pdf", "final")
    assert output.pdf_dir() == (tmp_path / "out" / "final")
    assert output.pdf_dir().is_absolute()


def test_run_yaml_keeps_the_path_the_user_typed(monkeypatch, tmp_path: Path):
    """Absolute at use, relative on disk -- or run.yaml stops being portable."""
    monkeypatch.chdir(tmp_path)
    output = Output(root=Path("ally-out"))
    output.set_path("descriptions", "shared/alt")
    assert output.as_dict()["root"] == "ally-out"
    assert output.as_dict()["paths"]["descriptions"] == "shared/alt"


# ---------------------------------------------------------------------- #
# bookmarks that do not navigate
# ---------------------------------------------------------------------- #


def test_an_outline_stuck_on_one_page_is_reported():
    """The signature of anchors that were never placed at the headings.

    Detected structurally rather than by inspecting the LaTeX: any route that
    produces this shape is broken, whoever wrote it.
    """
    from latexally.check.rules import _bookmark_navigation

    class Stub:
        page_count = 13
        outline_targets = [(f"H{i}", 1, "/Fit") for i in range(12)]

    rules = {finding.rule for finding in _bookmark_navigation(Stub(), "x.pdf")}
    assert "ALLY-PDF-025" in rules  # all on one page
    assert "ALLY-PDF-024" in rules  # and none positional


def test_a_healthy_outline_is_not_reported():
    from latexally.check.rules import _bookmark_navigation

    class Stub:
        page_count = 13
        outline_targets = [(f"H{i}", 1 + i % 13, "/XYZ") for i in range(12)]

    assert _bookmark_navigation(Stub(), "x.pdf") == []


def test_a_dead_destination_is_reported():
    from latexally.check.rules import _bookmark_navigation

    class Stub:
        page_count = 4
        outline_targets = [("A", 1, "/XYZ"), ("B", None, None), ("C", 3, "/XYZ")]

    findings = _bookmark_navigation(Stub(), "x.pdf")
    assert [f.rule for f in findings] == ["ALLY-PDF-023"]
    assert "'B'" in findings[0].message


def test_a_single_page_document_is_not_flagged():
    """Every bookmark on page 1 of a one-page document is correct, not broken."""
    from latexally.check.rules import _bookmark_navigation

    class Stub:
        page_count = 1
        outline_targets = [(f"H{i}", 1, "/XYZ") for i in range(5)]

    assert _bookmark_navigation(Stub(), "x.pdf") == []


def test_a_second_variant_does_not_clobber_the_first(
    corpus: Path, profile: Profile, tmp_path: Path
):
    """Building `problem` must not lay the original over the converted `solution`.

    The copy-siblings step took every .tex except the driver it was converting,
    which on the second pass included the first pass's freshly converted driver.
    The PDFs were correct -- each was compiled before being overwritten -- so
    only the mirrored tree was wrong, and rebuilding from it produced an
    untagged document.
    """
    directory = corpus / "sem" / "hw" / "3"
    (directory / "prob3.tex").write_text(
        "\\documentclass{article}\n\\input{body}\n"
    )
    assignment = Assignment(
        path="sem/hw/3",
        kind="homework",
        driver="sol3.tex",
        drivers={"solution": "sol3.tex", "problem": "prob3.tex"},
    )
    config = RunConfig(output=Output(root=tmp_path / "out"), write=True)
    both = frozenset(assignment.drivers.values())
    for driver in ("sol3.tex", "prob3.tex"):
        materialise(
            assignment, config, profile, lines=LINES,
            driver=driver, siblings_to_skip=both,
        )
    mirror = config.output.tex_dir() / "sem" / "hw" / "3"
    for driver in ("sol3.tex", "prob3.tex"):
        assert (mirror / driver).read_text().startswith("\\DocumentMetadata{"), (
            f"{driver} lost its conversion"
        )


def test_latex_lab_default_math_alt_is_rejected():
    """ALLY-PDF-041's whole job: reject the alt text nobody asked for.

    latex-lab turns `math/alt/use` on by itself once PDF/UA-1 is declared and
    fills /Alt from its own template. The result passes veraPDF and is read out
    as backslashes, so the rule has to fire on the template and stay quiet on
    real speech.
    """
    from latexally.check.rules import _RAW_LATEX

    default = (
        r"LaTeX formula starts \begin{equation} \frac{x^2-1}{x+1} "
        r"\end{equation} LaTeX formula ends"
    )
    assert _RAW_LATEX.search(default)
    assert _RAW_LATEX.search(r"$\mathbf{A}\vec{x}$")
    assert not _RAW_LATEX.search(
        "the fraction with numerator x squared minus 1, and denominator x plus 1"
    )


def test_inline_math_closed_with_a_dollar_is_reported(tmp_path):
    r"""ALLY-SRC-043, found by building the real corpus.

    `\(b_j$` opens with `\(` and closes with `$`. Untagged pdfLaTeX accepts it,
    so sp26/dis/13A has carried it for years without one error; under tagging
    latex-lab's grabber scans past the intended end and eats the closing brace
    of the enclosing `\ans{...}`. 30 occurrences in the live corpus.

    Reported by `check_tagging`, not `check_source`: it is a LaTeX limitation,
    not an accessibility failure, so it belongs to `doctor`'s corpus tier.
    """
    from latexally.check.rules import Severity, check_tagging

    source = tmp_path / "q.tex"
    source.write_text(
        "products of \\(a_i\\) and \\(b_j$ where \\(i + j = n\\):\n"
        "a price of \\(\\frac{\\$5}{2}\\) is fine, and so is $x$ beside \\(y\\).\n"
    )

    findings = [f for f in check_tagging(source) if f.rule == "ALLY-SRC-043"]

    assert len(findings) == 1, "escaped \\$ and ordinary $...$ must not be flagged"
    assert findings[0].severity == Severity.ERROR
    assert findings[0].line == 1


def test_a_layout_artifact_with_text_is_reported():
    r"""ALLY-PDF-032: decorative content that a positional reader still speaks.

    `Decorative` hides a region from readers that walk the tag tree, and that
    is the conformant mechanism. Readers that extract text by position ignore
    it, and tagpdf emits `/Artifact BMC` with no property list, so there is
    nowhere to hang the /ActualText that stops them -- unlike `Described`,
    which was fixed that way. Detection is the honest answer to a ceiling.

    Only `/Type/Layout` counts. A running head is text, is an artifact, and is
    entirely correct.
    """
    from latexally.check.content import MarkedRegion

    decorative = MarkedRegion(tag="Artifact", mcid=None, start=0, end=1,
                              subtype="Layout", text="DECORATIVELEAK")
    running_head = MarkedRegion(tag="Artifact", mcid=None, start=0, end=1,
                                subtype="Pagination", text="Homework 9")
    empty = MarkedRegion(tag="Artifact", mcid=None, start=0, end=1, subtype="Layout")

    flagged = [
        region.subtype == "Layout" and bool(region.text)
        for region in (decorative, running_head, empty)
    ]

    assert flagged == [True, False, False]


def test_line_break_after_a_question_macro_is_reported(tmp_path):
    r"""ALLY-SRC-044, found by building sp26/dis/11A.

    `\qns{...}` followed by `\newline` is harmless while the macro is inline.
    Once `question_tags` makes it emit a real H2 the heading closes the
    paragraph first, so the break has nothing to end. 930 occurrences in 575
    files -- larger than the other four tagging blockers combined.

    Report-only: the heading supplies the break, so the fix is to delete the
    `\newline` -- and deleting a break moves the page, which is the one thing
    this tool promises not to do. It stays a finding for a human.
    """
    from latexally.check.rules import Severity, check_tagging

    source = tmp_path / "q.tex"
    source.write_text(
        "\\qns{I bet Cal will win this year}\n"
        "\\newline\n"
        "As huge fans of the Big Game, you and your friend want to bet.\n"
        "\\qns{A question that does not break}\n"
        "Immediately followed by prose, which is fine.\n"
    )

    findings = [f for f in check_tagging(source) if f.rule == "ALLY-SRC-044"]

    assert len(findings) == 1, "only the macro followed by a break may be flagged"
    assert findings[0].severity == Severity.ERROR
    assert findings[0].line == 1


def test_dropped_tounicode_mappings_are_reported(tmp_path):
    r"""A silently-dropped glyph mapping must not read as a clean build.

    glyphtounicode.tex spells multi-codepoint entries ``{0066 0066 0069}``.
    Read under expl3 catcodes -- which ``\ProvidesExplPackage`` turns on, and
    where SPACE IS IGNORED -- the spaces vanish, pdfTeX sees one number, and
    drops the entry. 119 per document, and the only symptom is that "difficult"
    extracts as "di<U+FB03>cult".
    """
    from latexally.build import _log_findings

    log = tmp_path / "doc.log"
    log.write_text(
        "This is pdfTeX, Version 3.141592653\n"
        "pdfTeX warning: pdflatex: ToUnicode: value out of range [0,10FFFF]: 660066\n"
        "pdfTeX warning: pdflatex: ToUnicode: value out of range [0,10FFFF]: 660069\n"
        "Output written on doc.pdf (1 page).\n",
        encoding="utf-8",
    )
    errors, warnings = _log_findings(log)

    assert errors == []
    assert any("dropped 2 ToUnicode" in w for w in warnings)


def test_clean_log_reports_no_tounicode_warning(tmp_path):
    from latexally.build import _log_findings

    log = tmp_path / "doc.log"
    log.write_text("Output written on doc.pdf (1 page).\n", encoding="utf-8")

    assert _log_findings(log) == ([], [])


def test_enumitem_shortlabels_and_series_are_reported(tmp_path):
    r"""ALLY-SRC-040 for the two spellings that carry no counter macro.

    The patterns above this one all look for a counter -- `label=(\roman*)` or
    `leftmargin=*`. Two enumitem features are dropped by the same mechanism and
    match none of them: the *shortlabels* spelling `[(A)]`, which is `label=`
    without the key, and `series=`/`resume=`, which carry numbering across
    lists. Both renumber silently, with no error anywhere in the log, and
    `series=` is the largest exposure in the corpus.

    Bare keywords such as `[nosep]` are not shortlabels and must not be flagged.
    """
    from latexally.check.rules import Severity, check_tagging

    source = tmp_path / "q.tex"
    source.write_text(
        "\\begin{enumerate}[(A)]\n\\item one\n\\end{enumerate}\n"
        "\\begin{enumerate}[series=qn]\n\\item two\n\\end{enumerate}\n"
        "\\begin{enumerate}[resume=qn]\n\\item three\n\\end{enumerate}\n"
        "\\begin{itemize}[nosep]\n\\item not a shortlabels spec\n\\end{itemize}\n"
    )

    findings = [f for f in check_tagging(source) if f.rule == "ALLY-SRC-040"]

    assert [f.line for f in findings] == [1, 4, 7], "[nosep] is a bare keyword"
    assert all(f.severity == Severity.ERROR for f in findings)


def test_the_baseline_survives_wrappers_in_the_shared_question_files(
    profile, tmp_path
):
    r"""Found in a real run: every baseline of sp26/hw/10 died.

    The baseline is the untouched driver, compiled to measure what conversion
    cost. It shares the mirror with the converted one, and `apply_descriptions`
    writes `\described` / `Described` into the SHARED question files -- which
    the baseline \inputs too. Those files are not the driver, so the baseline
    picked up the wrappers while loading none of the packages that define them:
    "Environment Described undefined", then every enumerate in the document
    ending in the wrong place, then no baseline PDF and no pixel diff at all.

    The wrappers must therefore be *defined away* in the baseline, not avoided.
    They add no visual output of their own, so a transparent definition renders
    exactly the page the original source did -- which is the page the diff has
    to measure against.
    """
    from latexally.build import BASELINE_SHIM, inject
    from latexally.texlex import TexSource

    driver = tmp_path / "d.tex"
    driver.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "\\input{q}\n"
        "\\end{document}\n"
    )
    baseline = inject(TexSource.from_path(driver), list(BASELINE_SHIM))

    # Every wrapper the apply step can write has a definition here, or the
    # baseline dies on the first one that does not.
    for name in ("described", "LongDescription", "Described", "Decorative"):
        assert name in baseline, f"{name} is not defined away in the baseline"
    # In the preamble, after \documentclass -- a \newcommand before it is an
    # error in its own right.
    assert baseline.index("\\documentclass") < baseline.index("\\providecommand")
    assert baseline.index("\\providecommand") < baseline.index("\\begin{document}")
    # And the document body is untouched.
    assert "\\input{q}" in baseline

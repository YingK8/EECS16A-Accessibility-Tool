"""The conversion engine's guarantees, without compiling anything.

The expensive end-to-end proof lives in ``test_latex_golden.py``. What is
checked here is the part that cannot be seen in a PDF: that mirror mode really
does leave the corpus untouched, that in-place mode refuses when its edits could
not be undone, and that a log with a fatal error is never read as a clean build.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from latexally.build import (
    BuildReport,
    _log_findings,
    build_assignment,
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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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


def test_edit_mode_does_not_trip_over_its_own_edits(
    corpus: Path, profile: Profile, monkeypatch, tmp_path: Path
):
    r"""One run, several variants, one worktree check.

    `--edit` rewrites the corpus sources, which is the point, and the clean-git
    guard is what makes that undoable. But the guard was evaluated per DOCUMENT
    as well as per run, so the second variant saw the first variant's edit and
    refused:

        fa26/hw/1  solution  OK
        fa26/hw/1  problem   failed -- no PDF
          the corpus has 1 uncommitted change(s):
             M fa26/hw/1/sol1.tex

    The run reported its own write as the user's uncommitted work, and every
    assignment with more than one variant was unbuildable in edit mode.
    """
    from latexally.build import build_run

    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.email", "t@example.com")
    _git(corpus, "config", "user.name", "T")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-qm", "initial")

    seen: list[tuple[str, str]] = []

    def fake_build_assignment(assignment, config, prof, **kwargs):
        seen.append((assignment.path, kwargs.get("variant", "document")))
        # What the real one does at this point, and the whole cause: the first
        # variant leaves the corpus dirty for the second.
        (corpus / "sem" / "hw" / "3" / "sol3.tex").write_text("% converted\n")
        assert kwargs.get("worktree_checked") is True, (
            "build_run must tell build_assignment it already checked"
        )
        return BuildReport(
            assignment=assignment.path, variant=kwargs.get("variant", "document"), ok=True
        )

    import latexally.build as build

    monkeypatch.setattr(build, "build_assignment", fake_build_assignment)

    config = RunConfig(write=True).with_assignments(["sem/hw/3"])
    config.output = replace(config.output, root=tmp_path / "out", write_mode="edit")
    reports = build_run(config, profile)

    assert reports, "nothing was built"
    assert all(report.ok for report in reports), [r.note for r in reports if not r.ok]


def test_a_direct_caller_still_gets_the_worktree_guard(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """The guard is not removed, only moved up to the run.

    `tests/corpus_compile.py`, `tests/revert_e2e.py` and the agent API all call
    `build_assignment` without a `build_run` above them, and in edit mode they
    are exactly the callers that need the refusal.
    """
    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.email", "t@example.com")
    _git(corpus, "config", "user.name", "T")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-qm", "initial")
    (corpus / "sem" / "hw" / "3" / "body.tex").write_text("a person's own edit\n")

    config = RunConfig(write=True)
    config.output = replace(config.output, root=tmp_path / "out", write_mode="edit")
    # Raises rather than returning a failed report: turning the refusal into a
    # BuildReport is `build_run`'s job, and a direct caller has no such wrapper.
    with pytest.raises(LatexAllyError, match="uncommitted"):
        build_assignment(assignment, config, profile, variant="solution")


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
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
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


def test_a_changed_question_reaches_the_next_build(profile, tmp_path):
    r"""The mirror refreshes a dependency, it does not keep the first copy.

    `destination.exists()` meant a shared question file was mirrored once and
    never again. Two consequences, both silent: editing the question in the
    corpus had no effect on any later build, and last run's `<<TODO:>>`
    wrappers stayed in the mirror -- where `already_described` read them as
    work already done and skipped the figure, so a description filled in
    afterwards was never applied.

    Found by timestamps in a real output tree: drivers from 17:22, the
    questions they \input from 14:36 and the previous day.
    """
    from latexally.build import mirror_dependencies

    corpus = tmp_path / "corpus"
    assignment = corpus / "sem" / "hw" / "1"
    assignment.mkdir(parents=True)
    # `../../bank/q` from sem/hw/1 is sem/bank/q -- resolved against the
    # driver's directory, which is TeX's own rule and what the walk follows.
    (corpus / "sem" / "bank").mkdir(parents=True)
    question = corpus / "sem" / "bank" / "q.tex"
    question.write_text("first version\n")
    driver = assignment / "prob1.tex"
    driver.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\input{../../bank/q}\n"
        "\\end{document}\n"
    )

    mirror_root = tmp_path / "out" / "tex"
    target = mirror_root / "sem" / "hw" / "1"
    target.mkdir(parents=True)
    # `../../bank/q` from sem/hw/1 lands at the same offset inside the mirror,
    # which is the whole point of mirror_dependencies.
    mirrored = mirror_root / "sem" / "bank" / "q.tex"

    mirror_dependencies(driver, assignment, target, mirror_root)
    assert mirrored.read_text() == "first version\n"

    # The author edits the question, and runs again.
    question.write_text("second version\n")
    mirror_dependencies(driver, assignment, target, mirror_root)
    assert mirrored.read_text() == "second version\n", "the build used a stale copy"

    # And a mirror the apply step has since wrapped starts pristine next run,
    # so the wrapper is not read back as a description already written.
    mirrored.write_text("\\described{<<TODO:x>>}{second version}\n")
    mirror_dependencies(driver, assignment, target, mirror_root)
    assert mirrored.read_text() == "second version\n"


def test_draft_warns_about_silent_figures_instead_of_dropping_them(profile, tmp_path):
    """A downgrade to a warning is not a downgrade to silence.

    `_alt_text_failures` used to return nothing at all when strict was off, so
    "warn instead of fail" meant the findings vanished -- and a PDF whose every
    figure announces a placeholder reported zero errors, zero warnings, and a
    tick. The findings are the same either way; only where they are filed
    changes.
    """
    from latexally.build import BuildReport, write_report
    from latexally.run import Output, RunConfig

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
    config.output.root.mkdir(parents=True)
    # `built` is derived from having a PDF, so point at a real one.
    pdf = tmp_path / "out" / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    report = BuildReport(assignment="sem/hw/1", variant="problem", pdf=pdf)
    report.ok = True  # a draft build stands, by design
    report.alt_warnings = [
        "ALLY-PDF-003: Figure /Alt is an unfilled placeholder: '<<TODO:img-1>>'"
    ]

    path = write_report(config, [report])
    text = path.read_text()
    assert "DRAFT" in text, "an ok report with warnings must still be listed"
    assert "<<TODO:img-1>>" in text
    assert "1 figure(s) say nothing" in text


def test_a_clean_build_is_still_reported_without_a_draft_section(profile, tmp_path):
    """And the report does not grow a section for runs that have nothing."""
    from latexally.build import BuildReport, write_report
    from latexally.run import Output, RunConfig

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
    config.output.root.mkdir(parents=True)
    pdf = tmp_path / "out" / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    report = BuildReport(assignment="sem/hw/1", variant="problem", pdf=pdf)
    report.ok = True

    text = write_report(config, [report]).read_text()
    assert "DRAFT" not in text
    assert "sem/hw/1" in text


def test_no_baseline_means_no_original_in_the_mirror(profile, tmp_path):
    r"""Written unconditionally it was a second copy of every driver that
    nothing read: `_compile_assignment` only touches it when comparing."""
    from dataclasses import replace

    from latexally.build import materialise
    from latexally.discover import Assignment
    from latexally.run import Output, RunConfig

    corpus = tmp_path / "corpus"
    folder = corpus / "sem" / "hw" / "1"
    folder.mkdir(parents=True)
    (folder / "prob1.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n"
    )
    profile = replace(profile, corpus=replace(profile.corpus, root=corpus))
    assignment = Assignment(path="sem/hw/1", kind="homework", driver="prob1.tex")

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
    prepared = materialise(assignment, config, profile, lines=[])
    assert prepared.original is None
    assert not list((tmp_path / "out" / "tex").rglob("*-original.tex"))

    config.baseline = True
    prepared = materialise(assignment, config, profile, lines=[])
    assert prepared.original is not None and prepared.original.is_file()


def test_in_place_puts_the_pdf_beside_the_document(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path, monkeypatch
):
    """The reported failure: in-place selected, PDF still landed in ally-out.

    `in-place` names a destination and nothing else, so the one thing it has to
    do is send the finished document to the assignment's own directory. Asserted
    by recording what `compile_document` is handed, which is the only place the
    destination is decided.
    """
    import latexally.build as build_module
    from latexally.build import BuildReport

    seen: dict[str, Path] = {}

    def fake_compile(driver, *, work_dir, output_dir, profile, jobname, **kwargs):
        seen["output_dir"] = Path(output_dir)
        pdf = Path(output_dir) / f"{jobname}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.7\n")
        return pdf

    monkeypatch.setattr(build_module, "compile_document", fake_compile)
    monkeypatch.setattr(build_module, "inspect_pdf", lambda p: {
        "pages": 1, "bookmarks": 0, "figures": 0
    })
    monkeypatch.setattr(build_module, "_alt_text_failures", lambda p, c: [])

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="in-place"), write=True
    )
    # What `build_assignment` does before compiling: the whole output moves to
    # the assignment's own folder, not just the PDF.
    config = replace(
        config, output=config.output.for_assignment(profile.corpus.root, assignment.path)
    )
    prepared = materialise(assignment, config, profile, lines=LINES)
    build_module._compile_assignment(
        prepared, BuildReport(assignment=assignment.path), assignment,
        config, profile, "solution", False,
    )

    expected = (corpus / "sem" / "hw" / "3").absolute()
    assert seen["output_dir"] == expected, (
        "in-place must write the PDF into the assignment folder itself -- not "
        "into a pdf/ of its own, and not inside accessible/"
    )
    assert (tmp_path / "out") not in seen["output_dir"].parents


def test_in_place_moves_every_artifact_not_only_the_pdf(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    """The reported failure: in-place selected, the .tex still went to ally-out.

    `in-place` had moved the PDF and nothing else, so an assignment's converted
    sources and logs stayed in the output tree while its PDF sat beside the
    original -- the one arrangement nobody asked for.
    """
    out = Output(root=tmp_path / "out", write_mode="in-place")
    moved = out.for_assignment(corpus, "sem/hw/3")
    here = (corpus / "sem" / "hw" / "3" / "accessible").absolute()

    assert moved.root == here
    # The PDF is the deliverable and sits in the assignment folder itself.
    assert moved.pdf_dir() == (corpus / "sem" / "hw" / "3").absolute()
    # `accessible/` IS the converted-source tree: no `tex/` level under it.
    assert moved.tex_dir() == here
    for directory in (moved.log_dir(), moved.math_dir(), moved.baseline_dir()):
        assert directory.is_relative_to(here), directory

    # Descriptions are the exception, and deliberately: one description serves
    # every assignment that uses the figure, so a copy per assignment folder
    # would make the shared ones ambiguous.
    assert moved.worklog_dir() == out.worklog_dir()
    assert not moved.worklog_dir().is_relative_to(here)


def test_mirror_leaves_every_artifact_in_the_output_tree(
    corpus: Path, tmp_path: Path
):
    """The other mode is unchanged; `for_assignment` is a no-op for it."""
    out = Output(root=tmp_path / "out", write_mode="mirror")
    assert out.for_assignment(corpus, "sem/hw/3") is out


def test_mirror_puts_the_pdf_in_the_output_tree(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path, monkeypatch
):
    """The other half of the same switch, so neither can drift alone."""
    import latexally.build as build_module
    from latexally.build import BuildReport

    seen: dict[str, Path] = {}
    monkeypatch.setattr(build_module, "compile_document", lambda driver, *, work_dir,
        output_dir, profile, jobname, **kw: (
            seen.__setitem__("output_dir", Path(output_dir)),
            Path(output_dir).mkdir(parents=True, exist_ok=True),
            (Path(output_dir) / f"{jobname}.pdf").write_bytes(b"%PDF-1.7\n"),
            Path(output_dir) / f"{jobname}.pdf",
        )[-1])
    monkeypatch.setattr(build_module, "inspect_pdf", lambda p: {
        "pages": 1, "bookmarks": 0, "figures": 0
    })
    monkeypatch.setattr(build_module, "_alt_text_failures", lambda p, c: [])

    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="mirror"), write=True
    )
    prepared = materialise(assignment, config, profile, lines=LINES)
    build_module._compile_assignment(
        prepared, BuildReport(assignment=assignment.path), assignment,
        config, profile, "solution", False,
    )
    assert seen["output_dir"] == config.output.pdf_dir()


# ---------------------------------------------------------------------- #
# a written description has to reach the PDF, whatever else the run is doing
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["worklog", "caption", "placeholders"])
def test_a_written_description_is_applied_in_every_mode(tmp_path: Path, mode: str):
    r"""The bug this pins, because it was silent and it read as success.

    ``apply_descriptions`` was gated on ``config.alt.touches_sources``, and
    before that on ``config.alt.scans``. Both are real properties and neither is
    the right question, so between them ``\begin{Described}`` was emitted in
    exactly one of the four modes:

    ==============  =======  ===============  =======================
    mode            scans    touches_sources  what happened
    ==============  =======  ===============  =======================
    ``worklog``     True     False            returned at the gate
    ``caption``     False    True             reached the wrapper with
                                              an empty ``entries``
    ``placeholders``True     True             the only one that worked
    ``off``         False    False            correct, by accident
    ==============  =======  ===============  =======================

    Neither failure produced an error. ``describe_run`` reports the worklog's
    own state, so a build announced "3 done, 0 outstanding" while shipping a
    PDF whose figures carried no description at all -- measured on
    fa26/dis/00B: three descriptions written, zero ``Figure`` elements in the
    artefact, and ``check --pdf`` clean, because a checker cannot see a figure
    that produced no element.

    Applying a description someone already wrote is not a mode. ``off`` is the
    only answer to "do nothing here".
    """
    from latexally.build import Prepared, apply_descriptions
    from latexally.run import AltChoice, Output, RunConfig

    root = tmp_path / "out"
    work = root / "tex"
    work.mkdir(parents=True)
    figure = (
        "\\begin{figure}\n"
        "\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}\n"
        "\\caption{A line.}\n"
        "\\end{figure}\n"
    )
    driver = work / "sol1.tex"
    driver.write_text(f"\\documentclass{{article}}\n\\begin{{document}}\n{figure}\\end{{document}}\n")

    profile = Profile(name="test", corpus=CorpusScope(root=work, include=("**/*.tex",)))
    config = RunConfig(
        alt=AltChoice(mode=mode),
        output=Output(root=root, write_mode="mirror"),
        write=True,
    )
    prepared = Prepared(
        assignment=Assignment(path="sem/hw/1", kind="homework", driver="sol1.tex", tex_files=1),
        driver=driver,
        work_dir=work,
    )

    # The worklog, written by hand the way course staff fill one in. Written
    # directly rather than by scanning first and re-running: `placeholders`
    # wraps an undescribed figure on its first pass, and the second pass then
    # correctly skips it as already described. That idempotency is right and it
    # is not what is under test here.
    from latexally.scan import scan_file

    identity = scan_file(driver, profile)[0].id
    worklog = config.output.worklog_dir() / "test_fig_alt_texts.yaml"
    worklog.parent.mkdir(parents=True, exist_ok=True)
    worklog.write_text(
        f"{identity}:\n"
        f"  at: {driver.name}:2\n"
        f"  alt_text: A rising diagonal line.\n"
    )

    assert apply_descriptions(prepared, config, profile) >= 1, (
        f"{mode}: a description that is written must reach the document"
    )
    assert "\\begin{Described}" in driver.read_text()


def test_off_is_still_the_way_to_do_nothing(tmp_path: Path):
    """The one mode that must not touch the mirror, so the gate stays meaningful."""
    from latexally.build import Prepared, apply_descriptions
    from latexally.run import AltChoice, Output, RunConfig

    work = tmp_path / "out" / "tex"
    work.mkdir(parents=True)
    driver = work / "sol1.tex"
    before = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\begin{figure}\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}"
        "\\caption{A line.}\\end{figure}\n\\end{document}\n"
    )
    driver.write_text(before)

    profile = Profile(name="test", corpus=CorpusScope(root=work, include=("**/*.tex",)))
    config = RunConfig(
        alt=AltChoice(mode="off"),
        output=Output(root=tmp_path / "out", write_mode="mirror"),
        write=True,
    )
    prepared = Prepared(
        assignment=Assignment(path="sem/hw/1", kind="homework", driver="sol1.tex", tex_files=1),
        driver=driver,
        work_dir=work,
    )
    assert apply_descriptions(prepared, config, profile) == 0
    assert driver.read_text() == before

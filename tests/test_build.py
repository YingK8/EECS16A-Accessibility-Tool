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

from latexa11y.build import (
    _log_findings,
    inject,
    materialise,
    mirror_dependencies,
    relative_dependencies,
    require_clean_worktree,
)
from latexa11y.config import CorpusScope, Profile
from latexa11y.errors import LatexA11yError
from latexa11y.run import Assignment, Output, RunConfig
from latexa11y.texlex import TexSource

LINES = [
    "\\DocumentMetadata{lang=en-US,pdfversion=2.0,testphase={phase-III}}",
    "\\usepackage{latexa11y-ee16}",
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
    assert lines.index("\\usepackage{latexa11y-ee16}") < lines.index("\\input{body}")


def test_packages_land_before_begin_document_when_the_driver_has_one(tmp_path: Path):
    driver = tmp_path / "standalone.tex"
    driver.write_text("\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n")
    lines = inject(TexSource.from_path(driver), LINES).splitlines()
    assert lines.index("\\usepackage{latexa11y-ee16}") < lines.index("\\begin{document}")


def test_injection_refuses_a_file_that_is_not_a_driver(tmp_path: Path):
    fragment = tmp_path / "fragment.tex"
    fragment.write_text("Just a paragraph of a question.\n")
    with pytest.raises(LatexA11yError):
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
    with pytest.raises(LatexA11yError, match="not a git repository"):
        require_clean_worktree(corpus)


def test_in_place_refuses_a_dirty_worktree(corpus: Path):
    _git(corpus, "init", "-q")
    _git(corpus, "config", "user.email", "t@example.com")
    _git(corpus, "config", "user.name", "T")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-qm", "initial")
    require_clean_worktree(corpus)  # clean: allowed

    (corpus / "sem" / "hw" / "3" / "body.tex").write_text("edited\n")
    with pytest.raises(LatexA11yError, match="uncommitted"):
        require_clean_worktree(corpus)


def test_in_place_write_is_blocked_before_touching_anything(
    corpus: Path, profile: Profile, assignment: Assignment, tmp_path: Path
):
    before = _tree_digest(corpus)
    config = RunConfig(
        output=Output(root=tmp_path / "out", write_mode="in-place"), write=True
    )
    with pytest.raises(LatexA11yError):
        materialise(assignment, config, profile, lines=LINES)
    assert _tree_digest(corpus) == before


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
    """`-o a11y-out` must not land inside the directory being built.

    pdflatex is run with cwd set to the source/mirror directory, and resolves a
    relative -output-directory against THAT. A relative output root therefore
    wrote the PDF into the mirrored source tree, and the log lookup that
    followed found nothing and crashed. Every artifact path is absolute by the
    time it leaves the model.
    """
    monkeypatch.chdir(tmp_path)
    output = Output(root=Path("a11y-out"))
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
    output = Output(root=Path("a11y-out"))
    output.set_path("descriptions", "shared/alt")
    assert output.as_dict()["root"] == "a11y-out"
    assert output.as_dict()["paths"]["descriptions"] == "shared/alt"


# ---------------------------------------------------------------------- #
# bookmarks that do not navigate
# ---------------------------------------------------------------------- #


def test_an_outline_stuck_on_one_page_is_reported():
    """The signature of anchors that were never placed at the headings.

    Detected structurally rather than by inspecting the LaTeX: any route that
    produces this shape is broken, whoever wrote it.
    """
    from latexa11y.check.rules import _bookmark_navigation

    class Stub:
        page_count = 13
        outline_targets = [(f"H{i}", 1, "/Fit") for i in range(12)]

    rules = {finding.rule for finding in _bookmark_navigation(Stub(), "x.pdf")}
    assert "A11Y-PDF-025" in rules  # all on one page
    assert "A11Y-PDF-024" in rules  # and none positional


def test_a_healthy_outline_is_not_reported():
    from latexa11y.check.rules import _bookmark_navigation

    class Stub:
        page_count = 13
        outline_targets = [(f"H{i}", 1 + i % 13, "/XYZ") for i in range(12)]

    assert _bookmark_navigation(Stub(), "x.pdf") == []


def test_a_dead_destination_is_reported():
    from latexa11y.check.rules import _bookmark_navigation

    class Stub:
        page_count = 4
        outline_targets = [("A", 1, "/XYZ"), ("B", None, None), ("C", 3, "/XYZ")]

    findings = _bookmark_navigation(Stub(), "x.pdf")
    assert [f.rule for f in findings] == ["A11Y-PDF-023"]
    assert "'B'" in findings[0].message


def test_a_single_page_document_is_not_flagged():
    """Every bookmark on page 1 of a one-page document is correct, not broken."""
    from latexa11y.check.rules import _bookmark_navigation

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

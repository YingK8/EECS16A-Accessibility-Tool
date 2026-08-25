r"""Revert must be a byte-for-byte no-op. This is the test that proves it.

The claim is narrow and total: after ``edit`` mode has rewritten course
sources, ``latexally revert`` leaves the folder indistinguishable from before
the run. Not "close enough to review" -- identical bytes, no leftover files.

The sample is drawn from the real questionBank, evenly spaced across the sorted
path list so it spans semesters, homeworks, discussions, notes and the shared
bank rather than clustering in whichever directory happens to be first. Every
file is *copied* into a throwaway git repository first: a bug in the code under
test must not be able to reach the course repository.

Three assertions, and all three are needed:

* the hashes match          -- content is restored
* ``git status`` is empty   -- git agrees, including about deletions
* the file listing matches  -- nothing new is left behind

The third is the one that catches this tool's own droppings. The corpus
``.gitignore`` covers ``*.pdf``, ``*.log``, ``*.aux`` and ``*.annotations``, so
a leftover ``descriptions.yaml`` or an installed ``latexally-core.sty`` would
pass the second assertion while sitting in somebody's homework folder.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from latexally.apply import apply_scope
from latexally.catalog import WORKLOG_NAME, build_catalog
from latexally.config import CorpusScope, Profile
from latexally.errors import LatexAllyError
from latexally.revert import do_revert, plan_revert
from latexally.run import Output, RunConfig

#: The live corpus. Only ever read, and only to copy out of.
BANK = Path(__file__).resolve().parents[2] / "questionBank"

#: How many files to sample. Large enough to span the corpus, small enough that
#: the fast tier stays a few seconds.
SAMPLE = 120


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _corpus_files() -> list[Path]:
    """Every .tex the profile would actually convert, sorted by path.

    The two excludes mirror ``profiles/eecs16a.yaml``: the frozen per-semester
    snapshots are ~10k of the 17.6k files and are not live material.
    """
    return sorted(
        path
        for path in BANK.rglob("*.tex")
        if not any(
            part.endswith("_questionBank") or part in ("candidateQuestions", "candidate_questions")
            for part in path.relative_to(BANK).parts
        )
    )


def _evenly_sampled(files: list[Path], count: int) -> list[Path]:
    """``count`` files spread evenly through ``files``.

    Index arithmetic rather than ``files[::step]`` so the sample size is what
    was asked for regardless of how the corpus grows; a step computed by
    integer division drifts by dozens of files once the corpus changes size.
    """
    if len(files) <= count:
        return files
    return [files[round(i * (len(files) - 1) / (count - 1))] for i in range(count)]


def _manifest(root: Path) -> dict[str, str]:
    """Every file under ``root``, as path -> sha256. ``.git`` excluded."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _repo_from(files: list[Path], destination: Path) -> Path:
    """Copy ``files`` into a fresh git repo, preserving corpus-relative layout.

    An empty ``files`` commits whatever is already there, for a caller that
    has assembled the tree itself.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for source in files:
        target = destination / source.relative_to(BANK)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _git(destination, "init", "-q")
    _git(destination, "config", "user.email", "test@example.invalid")
    _git(destination, "config", "user.name", "test")
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "corpus sample")
    return destination


def _config(root: Path, out: Path) -> RunConfig:
    return RunConfig(
        profile="test",
        output=Output(root=out, write_mode="edit"),
        write=True,
    )


def _profile(root: Path) -> Profile:
    return Profile(name="test", corpus=CorpusScope(root=root, include=("**/*.tex",)))


@pytest.fixture(scope="module")
def sample() -> list[Path]:
    if not BANK.is_dir():
        pytest.skip(f"no corpus at {BANK}")
    files = _corpus_files()
    if not files:
        pytest.skip("corpus holds no convertible .tex")
    return _evenly_sampled(files, SAMPLE)


def test_the_sample_spans_the_corpus(sample: list[Path]):
    """A sample clustered in one directory would prove nothing about the rest.

    Asserted rather than assumed: if the exclude patterns or the sampling ever
    collapse to one folder, every test below would still pass while covering a
    fraction of what it claims to.
    """
    tops = {path.relative_to(BANK).parts[0] for path in sample}
    assert len(sample) == SAMPLE
    assert len(tops) >= 5, f"sample only touches {sorted(tops)}"


def test_revert_restores_every_sampled_file_byte_for_byte(
    sample: list[Path], tmp_path: Path
):
    """The whole claim, on ~120 real files spread across the corpus."""
    root = _repo_from(sample, tmp_path / "corpus")
    out = tmp_path / "ally-out"
    profile, config = _profile(root), _config(root, out)

    before = _manifest(root)
    assert before, "fixture copied nothing"

    # Everything `edit` mode does to sources: placeholders into the .tex, and
    # the worklog written beside them.
    catalog = build_catalog(profile, write=True, beside=root)
    apply_scope(
        profile, None, catalog.entries, dry_run=False, placeholders=True
    )

    dirty = _git(root, "status", "--porcelain").stdout
    assert dirty.strip(), "nothing was written, so the revert below proves nothing"

    do_revert(plan_revert(config, profile))

    after = _manifest(root)
    changed = {k for k in before.keys() & after.keys() if before[k] != after[k]}
    assert not changed, f"{len(changed)} file(s) differ after revert: {sorted(changed)[:5]}"
    assert before.keys() == after.keys(), (
        f"added: {sorted(after.keys() - before.keys())[:5]}  "
        f"lost: {sorted(before.keys() - after.keys())[:5]}"
    )
    assert not _git(root, "status", "--porcelain").stdout.strip()


def test_revert_removes_the_worklogs_it_wrote_beside_the_sources(
    sample: list[Path], tmp_path: Path
):
    """`descriptions.yaml` is untracked and gitignored-adjacent, so git alone
    would never notice it. It is the file this mode adds to a folder, and the
    one a revert most obviously has to take away again."""
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root, tmp_path / "ally-out")

    build_catalog(profile, write=True, beside=root)
    worklogs = list(root.rglob(WORKLOG_NAME))
    assert worklogs, "no worklog was written beside the sources"

    do_revert(plan_revert(config, profile))
    assert not list(root.rglob(WORKLOG_NAME))


def test_revert_leaves_a_file_the_tool_did_not_write(sample: list[Path], tmp_path: Path):
    """The reason revert does not run `git clean`.

    The course repository ignores `*.pdf` and holds 63 of them a TA built by
    hand. A revert that tidies the folder by deleting everything untracked
    destroys work git cannot give back.
    """
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root, tmp_path / "ally-out")

    theirs = root / "hand-built.pdf"
    theirs.write_bytes(b"%PDF-1.7 not ours\n")
    notes = root / "notes-to-self.txt"
    notes.write_text("do not delete me")

    build_catalog(profile, write=True, beside=root)
    do_revert(plan_revert(config, profile))

    assert theirs.is_file(), "revert deleted a PDF it did not write"
    assert notes.read_text() == "do not delete me"


def test_revert_deletes_the_artifacts_it_did_write(sample: list[Path], tmp_path: Path):
    """The other half: a PDF this tool made is named for it, and does go."""
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root, tmp_path / "ally-out")

    assignment = sorted(p for p in root.rglob("*") if p.is_dir())[0]
    ours = assignment / "sem-hw-1-solution-accessible.pdf"
    ours.write_bytes(b"%PDF-1.7 ours\n")
    stray = assignment / "sem-hw-1-solution-accessible.aux"
    stray.write_text("\\relax")
    installed = assignment / "latexally-core.sty"
    installed.write_text("% installed for a bare pdflatex")

    do_revert(plan_revert(config, profile))

    assert not ours.exists()
    assert not stray.exists()
    assert not installed.exists()


def test_revert_refuses_outside_a_git_repository(tmp_path: Path):
    """Revert restores with git. Without git there is nothing to restore from,
    and saying so is better than deleting the output tree and calling it done."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "q.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}")

    with pytest.raises(LatexAllyError, match="not a git repository"):
        plan_revert(_config(root, tmp_path / "out"), _profile(root))


def test_revert_reports_rather_than_hides_what_it_could_not_restore(
    sample: list[Path], tmp_path: Path
):
    """A half-done revert must not read as a clean one.

    Someone else's uncommitted edit is outside the plan, so it survives the
    `git checkout` -- and the verification pass says so instead of returning
    quietly and letting the folder look reverted.
    """
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root, tmp_path / "ally-out")

    plan = plan_revert(config, profile)
    victim = sorted(root.rglob("*.tex"))[0]
    victim.write_text(victim.read_text() + "\n% somebody else was here\n")

    with pytest.raises(LatexAllyError, match="still modified"):
        do_revert(plan)


# ---------------------------------------------------------------------- #
# the slow tier: a real build, in edit mode, then reverted
# ---------------------------------------------------------------------- #


def _closure(driver: Path) -> set[Path]:
    """Every file the driver reaches by a relative path, plus its figures.

    16A drivers say ``\\usepackage{../../../ee16}`` and
    ``\\input{../../../questionBank/hw/13/q_perpetual_motion}``, so an
    assignment folder copied on its own does not build -- before this tool
    touches it. Copying the closure at the same relative offsets is what makes
    the throwaway repository a faithful stand-in for the corpus.
    """
    from latexally.build import relative_dependencies

    files = set(relative_dependencies(driver))
    for path in list(files):
        figures = path.parent / "figures"
        if figures.is_dir():
            files.update(item for item in figures.rglob("*") if item.is_file())
    return files


@pytest.mark.corpus
def test_an_edit_mode_build_is_undone_completely(tmp_path: Path):
    r"""The end-to-end claim, with a real pdflatex in the middle.

    This is the only test that exercises :func:`~latexally.build.copy_back` --
    the step that writes converted sources over the corpus originals -- and the
    ``latexally-*.sty`` install that lets the folder build with a bare
    ``pdflatex`` afterwards. Everything in the fast tier above stops at
    ``apply_scope``.
    """
    from latexally.build import build_run

    assignment = BANK / "sp26" / "hw" / "13"
    driver = assignment / "prob13.tex"
    if not driver.is_file():
        pytest.skip(f"no driver at {driver}")

    root = tmp_path / "corpus"
    for source in sorted(_closure(driver)):
        try:
            relative = source.relative_to(BANK)
        except ValueError:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _repo_from([], root)

    profile = _profile(root)
    config = _config(root, tmp_path / "ally-out")
    config.assignments = ("sp26/hw/13",)
    config.variants = ("problem",)

    before = _manifest(root)
    reports = build_run(config, profile)
    built = [report for report in reports if report.pdf is not None]
    if not built:
        pytest.skip(
            "pdflatex produced no PDF here: "
            + "; ".join(report.note for report in reports if report.note)
        )

    # copy_back only runs on a clean build, so this is the assertion that says
    # the mode did its job rather than quietly doing nothing.
    assert any(report.edited for report in built), "edit mode wrote nothing back"
    folder = root / "sp26" / "hw" / "13"
    assert (folder / WORKLOG_NAME).is_file(), "no worklog beside the sources"
    assert list(folder.glob("latexally-*.sty")), "no package installed for bare pdflatex"

    do_revert(plan_revert(config, profile))

    after = _manifest(root)
    changed = {k for k in before.keys() & after.keys() if before[k] != after[k]}
    assert not changed, f"{len(changed)} file(s) differ after revert"
    assert before.keys() == after.keys(), (
        f"added: {sorted(after.keys() - before.keys())[:5]}  "
        f"lost: {sorted(before.keys() - after.keys())[:5]}"
    )
    assert not _git(root, "status", "--porcelain").stdout.strip()

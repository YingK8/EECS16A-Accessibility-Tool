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

Nothing here compiles anything. The half that needs a real LaTeX run -- does
``--edit`` actually leave a folder a bare ``pdflatex`` can build, and is *that*
undone completely -- is ``tests/revert_e2e.py``, a script rather than a test
because inspecting a built PDF imports ``pymupdf``, whose native module
deadlocks on import under pytest in this virtualenv.
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
            part.endswith("_questionBank")
            or part in ("candidateQuestions", "candidate_questions", "ally-out")
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


def _config(root: Path, out: Path | None = None) -> RunConfig:
    """A run config whose output root matches where the tool actually writes.

    Defaulted to `<corpus>/ally-out`, the same anchoring the CLI applies. A
    config pointing somewhere else describes a run that never happened, and a
    revert built from it would leave the real output on disk.
    """
    return RunConfig(
        profile="test",
        output=Output(root=out or (root / "ally-out"), write_mode="edit"),
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
    catalog = build_catalog(profile, write=True)
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


def test_revert_removes_the_worklogs_it_wrote(sample: list[Path], tmp_path: Path):
    """The worklogs are untracked, so git alone would never notice them. They
    are what a run adds to the corpus, and the first thing a revert has to take
    away again."""
    from latexally.catalog import worklog_dir

    root = _repo_from(sample, tmp_path / "corpus")
    profile = _profile(root)
    config = _config(root)

    build_catalog(profile, write=True)
    written = sorted(worklog_dir(profile).rglob("*_fig_alt_texts.yaml"))
    assert written, "no worklog was written"

    do_revert(plan_revert(config, profile))
    assert not list(root.rglob("*_fig_alt_texts.yaml"))


def test_revert_leaves_a_file_the_tool_did_not_write(sample: list[Path], tmp_path: Path):
    """The reason revert does not run `git clean`.

    The course repository ignores `*.pdf` and holds 63 of them a TA built by
    hand. A revert that tidies the folder by deleting everything untracked
    destroys work git cannot give back.
    """
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root)

    theirs = root / "hand-built.pdf"
    theirs.write_bytes(b"%PDF-1.7 not ours\n")
    notes = root / "notes-to-self.txt"
    notes.write_text("do not delete me")

    build_catalog(profile, write=True)
    do_revert(plan_revert(config, profile))

    assert theirs.is_file(), "revert deleted a PDF it did not write"
    assert notes.read_text() == "do not delete me"


def test_revert_deletes_the_artifacts_it_did_write(sample: list[Path], tmp_path: Path):
    """The other half: a PDF this tool made is named for it, and does go."""
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root)

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
        plan_revert(_config(root), _profile(root))


def test_revert_reports_rather_than_hides_what_it_could_not_restore(
    sample: list[Path], tmp_path: Path
):
    """A half-done revert must not read as a clean one.

    Someone else's uncommitted edit is outside the plan, so it survives the
    `git checkout` -- and the verification pass says so instead of returning
    quietly and letting the folder look reverted.
    """
    root = _repo_from(sample, tmp_path / "corpus")
    profile, config = _profile(root), _config(root)

    plan = plan_revert(config, profile)
    victim = sorted(root.rglob("*.tex"))[0]
    victim.write_text(victim.read_text() + "\n% somebody else was here\n")

    with pytest.raises(LatexAllyError, match="still modified"):
        do_revert(plan)


# ---------------------------------------------------------------------- #
# the guard that lets the fill-in loop work
# ---------------------------------------------------------------------- #


def test_the_clean_worktree_guard_steps_over_the_tool_s_own_files(
    sample: list[Path], tmp_path: Path
):
    """Otherwise the documented workflow dies at its second step.

    `--edit` writes `descriptions.yaml` beside the sources so a TA can fill it
    in. The moment they do, the next run has an untracked file in the corpus --
    the very file it asked them to edit -- and the guard used to refuse to
    start.
    """
    from latexally.build import require_clean_worktree

    root = _repo_from(sample, tmp_path / "corpus")
    require_clean_worktree(root)  # clean

    folder = sorted(p for p in root.rglob("*") if p.is_dir() and ".git" not in p.parts)[0]
    (folder / WORKLOG_NAME).write_text("fig-1:\n  alt_text: written by a TA\n")
    (folder / "latexally-core.sty").write_text("% installed by copy_back")
    (folder / "sem-hw-1-problem-accessible.pdf").write_bytes(b"%PDF-1.7\n")
    require_clean_worktree(root)  # still clean: all three are ours


def test_the_guard_still_refuses_somebody_else_s_work(
    sample: list[Path], tmp_path: Path
):
    """The guard's actual job, which the exemption above must not erode.

    A modified `.tex` is never waved through, not even one a previous run
    modified: at that point the honest next move is `revert` or a commit, not a
    second conversion layered on the first.
    """
    from latexally.build import require_clean_worktree

    root = _repo_from(sample, tmp_path / "corpus")
    victim = sorted(root.rglob("*.tex"))[0]
    victim.write_text(victim.read_text() + "\n% mine\n")
    with pytest.raises(LatexAllyError, match="uncommitted change"):
        require_clean_worktree(root)

    _git(root, "checkout", "--", str(victim.relative_to(root)))
    (root / "notes.txt").write_text("also mine")
    with pytest.raises(LatexAllyError, match="uncommitted change"):
        require_clean_worktree(root)


# ---------------------------------------------------------------------- #
# where you are standing is the scope
# ---------------------------------------------------------------------- #


def test_the_working_directory_is_the_scope(sample: list[Path], tmp_path: Path, monkeypatch):
    """No flag says which assignment: the directory you are in does.

    This replaced a `--here` flag. A flag that has to be passed on every
    command to make the tool mean the folder you are standing in is a flag that
    should not exist -- the working directory already carries that fact.
    """
    from latexally.discover import scope_from_cwd

    root = _repo_from(sample, tmp_path / "corpus")
    profile = _profile(root)
    folder = sorted(
        path for path in root.rglob("*")
        if path.is_dir() and ".git" not in path.relative_to(root).parts
    )[0]

    monkeypatch.chdir(folder)
    assert scope_from_cwd(profile) == folder.relative_to(root).as_posix()

    # At the top of the corpus, "" -- the whole corpus, not "no scope".
    monkeypatch.chdir(root)
    assert scope_from_cwd(profile) == ""


def test_outside_the_corpus_means_the_whole_corpus(tmp_path: Path, monkeypatch):
    """Driving the corpus from the tool's own checkout, and CI. Both are
    outside it, and both mean everything -- not an error."""
    from latexally.discover import scope_from_cwd

    root = tmp_path / "corpus"
    (root / "hw").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert scope_from_cwd(_profile(root)) is None


def test_worklogs_are_filed_by_semester_and_kind(sample: list[Path], tmp_path: Path):
    """`<corpus>/ally-out/descriptions/<semester>/<kind>_fig_alt_texts.yaml`.

    One directory rather than one file per source folder: a description is
    content-addressed and serves every assignment using the figure, so copies
    scattered through the tree make the shared ones ambiguous.
    """
    from latexally.catalog import build_catalog, worklog_dir

    root = _repo_from(sample, tmp_path / "corpus")
    profile = _profile(root)
    result = build_catalog(profile, write=True)
    assert result.worklogs, "nothing catalogued"

    directory = worklog_dir(profile)
    assert directory == root / "ally-out" / "descriptions"
    for path in result.worklogs:
        assert path.name.endswith("_fig_alt_texts.yaml"), path.name
        # exactly one semester folder between the root and the file
        assert path.parent.parent == directory, path


def test_the_tools_own_output_is_not_course_material(tmp_path: Path):
    r"""ally-out lives inside the corpus now, and must not be scanned.

    Its `tex/` holds a converted copy of every .tex a run touched. Without an
    exclusion the next run reads them as source: figures counted twice,
    already-converted files converted again, and a mirror of the mirror. Caught
    by a sampled test that started copying `ally-out/tex/**` out of the live
    corpus after a real run had put it there.
    """
    root = tmp_path / "corpus"
    (root / "hw" / "1").mkdir(parents=True)
    (root / "hw" / "1" / "q.tex").write_text("real\n")
    (root / "ally-out" / "tex" / "hw" / "1").mkdir(parents=True)
    (root / "ally-out" / "tex" / "hw" / "1" / "q.tex").write_text("converted copy\n")

    found = {path.name for path in _profile(root).iter_files(None)}
    assert found == {"q.tex"}
    assert not any(
        "ally-out" in path.parts for path in _profile(root).iter_files(None)
    )

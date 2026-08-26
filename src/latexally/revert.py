r"""Undo a run: put the corpus back, and take the output tree with it.

``edit`` mode rewrites course material. Every other safeguard in this package
is about making sure what it writes is correct; this module is the one that
assumes it was not, and gives the folder back.

Three groups, because they need three different mechanisms and confusing them
is how a revert destroys something:

``restore``  tracked files the run modified. ``git checkout`` is the whole
             mechanism -- the corpus is a git repository and
             :func:`~latexally.build.require_clean_worktree` refuses to start
             an in-place run unless it is clean, so at the moment a revert runs
             the modifications in scope are this tool's.
``remove``   files the run *created* inside the corpus. Git cannot restore a
             file that was never committed, so these are deleted by name.
``outputs``  the run's own output tree, which is entirely this tool's and goes
             wholesale.

**Why not ``git clean``.** It is the obvious way to do the middle group and it
is wrong here. The course repository ignores ``*.pdf``, ``*.log``, ``*.aux``
and ``*.annotations``, and holds 63 PDFs and 29 logs a TA built by hand.
``git clean -fdx`` deletes every one of them, and nothing in git would bring
them back. So the middle group is matched against the names this tool writes
and nothing else: an unrecognised file is left alone, which is the failure
worth having.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import WORKLOG_NAME
from .catalog.worklog import read_worklog
from .config import Profile
from .errors import LatexAllyError
from .run import ARTIFACTS, RunConfig

__all__ = ["RevertPlan", "artifact_globs", "plan_revert", "do_revert"]


def artifact_globs() -> tuple[str, ...]:
    """Glob patterns for everything this tool writes into a corpus folder.

    Built from the suffixes the build engine itself stamps on its output, so a
    rename there cannot leave this module quietly matching nothing.
    """
    from .build import ACCESSIBLE_SUFFIX, ORIGINAL_SUFFIX

    stamped = tuple(
        f"*-{suffix}.{extension}"
        for suffix in (ACCESSIBLE_SUFFIX, ORIGINAL_SUFFIX)
        # .pdf is the deliverable; the rest are what pdflatex leaves behind and
        # `_collect_log` only tidies when its moves succeed.
        for extension in ("pdf", "log", "aux", "out", "annotations", "synctex.gz")
    )
    # The worklog, and the packages `copy_back` installs so a bare pdflatex can
    # find them. Both are named by this package, which is what makes them
    # recognisable a week later.
    #
    # `*-mathml-dummy.html` is there because the documented workflow ends in
    # the user running `pdflatex` themselves. The build engine unlinks its own
    # copy (`_collect_log`); a hand-run pdflatex leaves one behind under the
    # document's own jobname, and it exists only because this tool put the
    # math-speech machinery in the preamble.
    return stamped + (
        # The worklog, under both names it has had. The old one put a
        # `descriptions.yaml` in each source folder; a corpus converted before
        # the move still has them, and a revert that does not know the old name
        # leaves them scattered through the tree forever.
        "*_fig_alt_texts.yaml",
        WORKLOG_NAME,
        "latexally-*.sty",
        "latexally-*.cls",
        "*-mathml-dummy.html",
    )


@dataclass(slots=True)
class RevertPlan:
    """What a revert would do. Nothing here has happened yet."""

    root: Path
    restore: list[Path] = field(default_factory=list)
    remove: list[Path] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    #: Worklogs left alone because somebody has written descriptions in them.
    #: Reported, never deleted -- see :func:`_holds_human_text`.
    kept: list[Path] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        # `kept` deliberately does not count: a plan whose only finding is "I
        # left your descriptions alone" has nothing to do, and should say so.
        return not (self.restore or self.remove or self.outputs)

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "restore": [str(path) for path in self.restore],
            "remove": [str(path) for path in self.remove],
            "outputs": [str(path) for path in self.outputs],
            "kept": [str(path) for path in self.kept],
        }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _require_repository(root: Path) -> None:
    """Revert restores with git, so refuse early and by name when it cannot."""
    if shutil.which("git") is None:
        raise LatexAllyError(
            "revert restores your .tex with git, and git is not installed",
            hint="install git, or restore the files from your own backup",
        )
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise LatexAllyError(
            f"{root} is not a git repository, so there is nothing to restore from",
            hint=(
                "only `edit` mode changes the corpus, and it refuses to run "
                "outside a clean git repository for exactly this reason"
            ),
        )


def _modified(root: Path, scope: Path | None) -> list[Path]:
    """Tracked files git reports as changed, inside ``scope``.

    Porcelain v1, read by field rather than by column: a rename is
    ``R  old -> new`` and taking the first path would restore the wrong one.
    """
    args = ["status", "--porcelain", "--untracked-files=no"]
    if scope is not None:
        args += ["--", str(scope)]
    result = _git(root, *args)
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append((root / entry.strip().strip('"')).resolve())
    return sorted(set(paths))


def plan_revert(
    config: RunConfig,
    profile: Profile,
    scope: str | None = None,
    *,
    restore: bool = True,
    force: bool = False,
) -> RevertPlan:
    """Work out what undoing this run means. Writes nothing.

    ``restore=False`` is ``clean``: delete what the run produced and leave every
    source file alone. That is the whole difference between the two commands,
    and it is worth having as its own because the halves carry different risk.
    Deleting a file this tool wrote is recoverable by re-running it. Restoring
    a tracked ``.tex`` is a ``git checkout`` over somebody's working tree, and
    it needs git, a repository, and a person who knows what is in it.

    ``force`` also removes worklogs somebody has written descriptions into. Off
    by default: those were never committed, so nothing can give them back.
    """
    root = profile.corpus.root.resolve()
    if restore:
        _require_repository(root)

    target = (root / scope).resolve() if scope else root
    if target != root and root not in target.parents:
        raise LatexAllyError(
            f"scope {scope!r} is outside the corpus root {root}",
            hint="pass a path inside the corpus, or leave it off for all of it",
        )

    plan = RevertPlan(root=root)
    if restore:
        plan.restore = _modified(root, None if target == root else target)

    # Everywhere this scope's material actually lives, not just the folder
    # named. A worklog goes beside the .tex holding the FIGURE, and three
    # quarters of sp26 graphics are \input from the shared bank -- so a revert
    # that swept only the assignment directory left every worklog it wrote
    # sitting in questionBank/, and reported "nothing to revert" while doing it.
    searched = {target} | _reached_directories(profile, scope, root)

    # One `git ls-files` for the whole scope, not one `--error-unmatch` per
    # candidate: a corpus-wide revert matches thousands of files and that is
    # thousands of subprocesses.
    tracked = _tracked(root, None if target == root else target)
    for directory in searched:
        for pattern in artifact_globs():
            for path in directory.rglob(pattern) if directory == target else directory.glob(pattern):
                # Deleted only if git has never seen it. A `latexally-core.sty`
                # a course committed itself is theirs, and `git checkout` owns
                # it.
                resolved = path.resolve()
                if not path.is_file() or resolved in tracked:
                    continue
                if not force and _holds_human_text(resolved):
                    plan.kept.append(resolved)
                    continue
                plan.remove.append(resolved)
    plan.remove = sorted(set(plan.remove))
    plan.kept = sorted(set(plan.kept))

    seen: set[Path] = set()
    for slug, _, _ in ARTIFACTS:
        directory = config.output.path_for(slug)
        if not directory.is_dir() or directory in seen:
            continue
        seen.add(directory)
        if slug == "descriptions":
            # NOT deleted wholesale. This is where the worklogs live now, and
            # rmtree over the directory walks straight past the check that
            # keeps a description somebody typed -- which is the one file here
            # that git never had and nothing can rebuild. Its files go through
            # the same per-file decision as everything else, and the directory
            # itself only goes if none of them was worth keeping.
            for path in sorted(directory.rglob("*.yaml")):
                resolved = path.resolve()
                if not force and _holds_human_text(resolved):
                    plan.kept.append(resolved)
                else:
                    plan.remove.append(resolved)
            if not plan.kept:
                plan.outputs.append(directory)
            continue
        plan.outputs.append(directory)
    plan.remove = sorted(set(plan.remove))
    plan.kept = sorted(set(plan.kept))
    for name in ("run.yaml", "build-log.txt"):
        stray = (config.output.root / name).resolve()
        if stray.is_file():
            plan.outputs.append(stray)
    return plan


def _reached_directories(profile: Profile, scope: str | None, root: Path) -> set[Path]:
    r"""Every directory holding a ``.tex`` the scope's assignments actually use.

    ``source_files_for`` is the same walk the build does to decide what a
    conversion means, so the directories it returns are exactly the ones a run
    could have written a worklog into. Reusing it here is what keeps "where
    revert looks" and "where the run wrote" the same answer.
    """
    from .build import source_files_for
    from .discover import discover_assignments

    directories: set[Path] = set()
    try:
        assignments = discover_assignments(profile, scope)
    except LatexAllyError:
        return directories
    for assignment in assignments:
        for path in source_files_for(assignment, profile):
            directory = path.parent.resolve()
            if directory == root or root in directory.parents:
                directories.add(directory)
    return directories


def _holds_human_text(path: Path) -> bool:
    """Has anyone written a description in this worklog?

    A worklog the tool wrote and nobody has touched is pure output and goes.
    One with even a single filled-in ``alt_text`` is somebody's afternoon, and
    git cannot give it back -- it was never committed. So it is reported and
    left, which is the failure worth having.

    Only worklogs are asked: a PDF or a ``.sty`` this tool wrote carries no
    typing of anybody's.
    """
    if path.name != WORKLOG_NAME and not path.name.endswith("_fig_alt_texts.yaml"):
        return False
    try:
        return any(
            entry.description.strip()
            for entry in read_worklog(path).entries.values()
        )
    except OSError:
        # Unreadable: assume it matters rather than delete it.
        return True


def _tracked(root: Path, scope: Path | None) -> set[Path]:
    """Every path git has under version control in ``scope``, resolved.

    Empty outside a repository, which is the right answer for ``clean``: with
    no git there is nothing tracked to protect, and every candidate reached
    this point by matching a name only this tool writes.
    """
    if shutil.which("git") is None:
        return set()
    args = ["ls-files", "-z"]
    if scope is not None:
        args += ["--", str(scope)]
    result = _git(root, *args)
    return {
        (root / entry).resolve()
        for entry in result.stdout.split("\0")
        if entry
    }


def _prune_empty(outputs: list[Path]) -> None:
    """Remove the output root once nothing is left in it.

    Emptying `ally-out/` and leaving the directory behind is a clean that does
    not look like one: the folder is still there in a listing, and there is no
    way to tell from outside whether it holds a run or nothing at all. Only
    ever removed when genuinely empty -- a worklog kept because somebody wrote
    in it keeps its directory too.
    """
    for root in {path.parent for path in outputs}:
        try:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:  # pragma: no cover - raced or not ours to remove
            pass


def do_revert(plan: RevertPlan, *, verify: bool = True) -> RevertPlan:
    """Carry the plan out, then check that it worked.

    The verification is not decoration. A revert that half-succeeds leaves
    course material in a state nobody chose, and the person who ran it has
    every reason to believe it is clean.
    """
    if plan.restore:
        relative = [str(path.relative_to(plan.root)) for path in plan.restore]
        result = _git(plan.root, "checkout", "--", *relative)
        if result.returncode != 0:
            raise LatexAllyError(
                f"git could not restore {len(relative)} file(s): "
                f"{result.stderr.strip()}",
                hint="resolve it in the corpus, then run revert again",
            )
    for path in plan.remove:
        path.unlink(missing_ok=True)
    for path in plan.outputs:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    _prune_empty(plan.outputs)

    # Nothing was restored, so there is nothing to check: `clean` leaves every
    # modification in place on purpose, and reporting them as failures would
    # make a successful clean look broken.
    left = _modified(plan.root, None) if verify else []
    if left:
        listed = "\n    ".join(str(path) for path in left[:8])
        more = f"\n    …and {len(left) - 8} more" if len(left) > 8 else ""
        raise LatexAllyError(
            f"revert ran but {len(left)} file(s) are still modified:\n    "
            f"{listed}{more}",
            hint="these were not this tool's to restore; `git diff` shows them",
        )
    return plan

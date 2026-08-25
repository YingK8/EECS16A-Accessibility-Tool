r"""The whole ``--edit`` workflow, end to end, and the revert that undoes it.

A runnable harness rather than a pytest test, for the same reason
:mod:`tests.corpus_compile` and :mod:`tests.corpus_sweep` are: it costs a real
LaTeX build. There is a second reason here, and it is not a matter of taste --
inspecting a built PDF imports ``pymupdf``, whose native module **deadlocks on
import under pytest** in this virtualenv. That is why the extraction check in
``test_latex_golden.py`` skips and why anything that builds a PDF hangs rather
than fails. Run this directly:

    uv run python tests/revert_e2e.py

The fast assertions -- restore, delete-by-name, refuse-outside-git -- live in
``tests/test_revert.py`` and run in CI. What only this can prove is the part
with a compiler in it:

1. ``--edit`` writes the converted sources back over the corpus originals
2. it installs the ``latexally-*.sty`` they need beside them
3. the worklog lands beside the ``.tex`` holding the figure
4. a **bare pdflatex** then builds the tagged PDF -- no TEXINPUTS, no
   ``-output-directory``, no latexmk. If this fails the README recipe is a lie
5. ``revert`` leaves the folder byte-identical to before

The corpus is never touched: the assignment's whole dependency closure is
copied into a throwaway git repository first, because 16A drivers reach out of
their own directory (``\usepackage{../../../ee16}``) and a folder copied alone
does not build even before this tool sees it.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from latexally.build import build_assignment, relative_dependencies
from latexally.catalog import WORKLOG_NAME
from latexally.config import load_profile
from latexally.discover import iter_selected
from latexally.revert import do_revert, plan_revert
from latexally.run import Output, RunConfig

BANK = Path(__file__).resolve().parents[2] / "questionBank"
#: The default is one real assignment, chosen because its body \\inputs
#: questions from the shared bank two directories away -- which is the normal
#: case, not the exception, and the case a folder-local tool would get wrong.
#: Override both from the command line to walk through any other folder:
#:
#:     uv run python tests/revert_e2e.py sp26/hw/10
#:     uv run python tests/revert_e2e.py sp26/hw/10 sol10.tex
ASSIGNMENT = "sp26/hw/13"
DRIVER = "prob13.tex"
#: Kept after a run instead of deleted, so the walkthrough can be inspected.
#: Printed at the end; the next run clears it.
KEEP = False

_START = time.time()


def say(*parts: object) -> None:
    print(f"[{time.time() - _START:6.1f}s]", *parts, flush=True)


def closure(driver: Path) -> set[Path]:
    """Everything the driver reaches by a relative path, plus nearby figures."""
    files = set(relative_dependencies(driver))
    for path in list(files):
        figures = path.parent / "figures"
        if figures.is_dir():
            files.update(item for item in figures.rglob("*") if item.is_file())
    return files


def manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    ).stdout


def main() -> int:
    driver = BANK / ASSIGNMENT / DRIVER
    if not driver.is_file():
        say(f"FAIL no driver at {driver}")
        return 2

    work = Path("/tmp/latexally-revert-e2e")
    shutil.rmtree(work, ignore_errors=True)
    root = work / "corpus"
    for source in sorted(closure(driver)):
        try:
            relative = source.relative_to(BANK)
        except ValueError:
            continue
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, root / relative)
    git(root, "init", "-q")
    git(root, "config", "user.email", "e2e@example.invalid")
    git(root, "config", "user.name", "e2e")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "before")

    profile = load_profile("eecs16a", corpus_root=root)
    config = RunConfig(
        profile=profile.name,
        assignments=(ASSIGNMENT,),
        variants=("problem",),
        output=Output(root=work / "ally-out", write_mode="edit"),
        write=True,
    )

    before = manifest(root)
    say(f"corpus copy: {len(before)} files")

    failures: list[str] = []

    def check(ok: bool, label: str, detail: object = "") -> None:
        say(("  ok  " if ok else "  FAIL") + f" {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    # `compare=False` skips the before/after pixel diff, which is the only step
    # that needs pymupdf. Everything else runs exactly as a real run does.
    reports = [
        build_assignment(
            item, config, profile, variant="problem", driver=DRIVER, compare=False
        )
        for item in iter_selected(profile, config)
    ]
    for report in reports:
        say(f"built {report.assignment} {report.variant}: ok={report.ok} "
            f"pdf={report.pdf is not None} edited={len(report.edited)}")
        for line in report.errors[:4]:
            say("   ERR:", line[:150])

    built = [report for report in reports if report.pdf is not None]
    check(bool(built), "a PDF came out")
    if not built:
        return 1

    folder = root / ASSIGNMENT
    check(any(report.edited for report in built), "edit mode wrote sources back",
          sum(len(report.edited) for report in built))
    installed = sorted(path.name for path in folder.glob("latexally-*.sty"))
    check(bool(installed), "packages installed for a bare pdflatex", installed)
    worklogs = sorted(path.relative_to(root).as_posix() for path in root.rglob(WORKLOG_NAME))
    check(bool(worklogs), "worklog written beside the sources", worklogs)

    bare = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", DRIVER],
        cwd=folder,
        capture_output=True,
        text=True,
    )
    check(bare.returncode == 0, "bare pdflatex builds the edited folder",
          next((line for line in bare.stdout.splitlines() if line.startswith("!")), ""))
    check((folder / f"{Path(DRIVER).stem}.pdf").is_file(), "and produced a PDF")

    if KEEP:
        say("--keep: stopping before revert. Look at:")
        say(f"   {folder}")
        for path in sorted(folder.iterdir()):
            say(f"     {path.name}")
        for path in worklogs:
            say(f"   {root / path}")
        say(f"   undo it with: latexally --corpus {root} revert {ASSIGNMENT} --write")
        return 1 if failures else 0

    do_revert(plan_revert(config, profile))

    # What a bare pdflatex leaves under the document's OWN jobname belongs to
    # whoever ran it, and revert deliberately does not touch it -- in the real
    # corpus those files already exist and were built by hand.
    mine = {
        f"{ASSIGNMENT}/{Path(DRIVER).stem}{suffix}"
        for suffix in (".pdf", ".aux", ".log", ".out", ".annotations", ".synctex.gz")
    }
    after = {k: v for k, v in manifest(root).items() if k not in mine}
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    added = sorted(after.keys() - before.keys())
    lost = sorted(before.keys() - after.keys())
    left = {line[3:] for line in git(root, "status", "--porcelain").splitlines()} - mine

    check(not changed, "every file restored byte for byte", changed[:5])
    check(not added, "nothing left behind", added[:5])
    check(not lost, "nothing deleted that should not be", lost[:5])
    check(not left, "git status clean", sorted(left)[:5])

    say("FAILURES:", failures or "none")
    return 1 if failures else 0


def _driver_for(assignment: str) -> str:
    """The problem variant if the profile knows one, else any driver there.

    Saves the caller naming a file: `sp26/hw/10` is the thing they have in
    mind, and which of `prob10.tex` / `sol10.tex` drives it is the tool's
    question to answer, not theirs.
    """
    profile = load_profile("eecs16a")
    for item in iter_selected(profile, RunConfig(assignments=(assignment,))):
        drivers = item.drivers
        for variant in ("problem", "solution", "answer", "document"):
            if variant in drivers:
                return drivers[variant]
        if drivers:
            return sorted(drivers.values())[0]
        if item.driver:
            return item.driver
    raise SystemExit(f"no driver found in {assignment}")


if __name__ == "__main__":
    KEEP = "--keep" in sys.argv
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if positional:
        ASSIGNMENT = positional[0].strip("/")
        DRIVER = positional[1] if len(positional) > 1 else _driver_for(ASSIGNMENT)
    raise SystemExit(main())

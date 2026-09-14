"""The batch authoring driver, end to end against a stub describer.

The describer is a command, not an SDK, so it can be replaced with a shell
script in a test -- which is most of why it is a command. Nothing here calls a
model, so this runs in CI beside everything else.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "hw" / "1").mkdir(parents=True)
    (root / "hw" / "1" / "q.tex").write_text(
        "\\qns{Charge Sharing}\n"
        "\\begin{tikzpicture}\n"
        "    \\node (a) at (0,0) {A};\n"
        "    \\node (b) at (1,0) {B};\n"
        "    \\draw (a) -- (b);\n"
        "\\end{tikzpicture}\n"
    )
    return root


@pytest.fixture
def profile_path(tmp_path: Path, corpus: Path) -> Path:
    path = tmp_path / "test.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "test",
                "corpus": {"root": str(corpus), "include": ["**/*.tex"]},
            }
        )
    )
    return path


def _describer(tmp_path: Path, answer: str) -> str:
    """A stub that ignores the prompt and always says the same thing."""
    script = tmp_path / "describer.sh"
    script.write_text(f"#!/bin/sh\ncat > /dev/null\nprintf '%s' {answer!r}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _run(profile_path: Path, tmp_path: Path, describer: str, *extra: str):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [
            sys.executable, "-m", "tools.describe_lab",
            "-p", str(profile_path),
            "--describer", describer,
            "--log", str(tmp_path / "log.jsonl"),
            *extra,
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def _scan(profile_path: Path):
    from latexally.catalog import build_catalog
    from latexally.config import load_profile

    build_catalog(load_profile(str(profile_path)), write=True)


def test_a_dry_run_writes_no_description(profile_path: Path, tmp_path: Path, corpus: Path):
    _scan(profile_path)
    result = _run(
        profile_path, tmp_path,
        _describer(tmp_path, "Two nodes A and B joined by a single edge."),
        "--limit", "1", "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "nothing written" in result.stdout
    worklogs = list((corpus / "ally-out" / "descriptions").rglob("*.yaml"))
    assert worklogs, "scan should have written a worklog"
    assert "alt_text: \n" in worklogs[0].read_text() or "alt_text:\n" in worklogs[0].read_text()


def test_a_real_run_writes_the_description_into_the_worklog(
    profile_path: Path, tmp_path: Path, corpus: Path
):
    _scan(profile_path)
    answer = "Two nodes A and B joined by a single edge."
    result = _run(profile_path, tmp_path, _describer(tmp_path, answer), "--limit", "1")

    assert result.returncode == 0, result.stderr
    written = "\n".join(
        p.read_text() for p in (corpus / "ally-out" / "descriptions").rglob("*.yaml")
    )
    assert answer in written


def test_every_submission_is_logged_for_review(profile_path: Path, tmp_path: Path):
    """The harness has no review gate, so the log is the only way back."""
    _scan(profile_path)
    answer = "Two nodes A and B joined by a single edge."
    _run(profile_path, tmp_path, _describer(tmp_path, answer), "--limit", "1")

    records = [
        json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]["description"] == answer
    assert records[0]["written"] is True


def test_a_description_that_breaks_the_spec_is_never_written(
    profile_path: Path, tmp_path: Path, corpus: Path
):
    r"""`validate_description` runs before the write, and the loop honours it."""
    _scan(profile_path)
    bad = "This figure shows $\\frac{1}{2}$ of the circuit."
    result = _run(profile_path, tmp_path, _describer(tmp_path, bad), "--limit", "1")

    assert result.returncode == 1
    written = "\n".join(
        p.read_text() for p in (corpus / "ally-out" / "descriptions").rglob("*.yaml")
    )
    assert "frac" not in written
    record = json.loads((tmp_path / "log.jsonl").read_text().splitlines()[0])
    assert record["written"] is False
    assert record["rejections"], "the reason has to survive into the log"


def test_a_failing_describer_is_reported_not_swallowed(profile_path: Path, tmp_path: Path):
    _scan(profile_path)
    script = tmp_path / "broken.sh"
    script.write_text("#!/bin/sh\ncat > /dev/null\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    result = _run(profile_path, tmp_path, str(script), "--limit", "1")

    assert result.returncode == 1
    assert "exited 3" in result.stderr

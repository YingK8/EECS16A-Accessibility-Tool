"""The CLI's own wiring -- flags reaching the functions they claim to.

`apply.py`'s caption logic and `CorpusScope`'s scope resolution both have
their own, thorough test suites. What broke was neither of those: it was
`latexally apply` never passing `captions=True` through to `apply_scope` at
all, so the flag could not have worked no matter how correct the logic behind
it was. This file is for exactly that class of bug -- a CLI option that looks
wired but is not -- caught by actually invoking the command, not by calling
the Python function it is supposed to call.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from latexally.cli import main


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    folder = corpus / "sem" / "hw" / "3"
    folder.mkdir(parents=True)
    (folder / "sol3.tex").write_text(
        "\\documentclass{article}\n\\input{body}\n"
    )
    (folder / "body.tex").write_text(
        "\\begin{document}\n"
        "\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}\n"
        "\\end{document}\n"
    )
    return corpus


def _profile_args(tmp_path: Path, corpus: Path) -> list[str]:
    """`-p`/`--corpus`, self-contained regardless of what is installed on the
    machine running the tests -- a bare `--corpus` alone refuses with "more
    than one profile installed" on any machine that has more than one, which
    is exactly the ambiguity a named profile file exists to resolve."""
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("name: test\n")
    return ["-p", str(profile_yaml), "--corpus", str(corpus)]


def test_apply_captions_needs_no_worklog(tmp_path: Path):
    r"""The bug this pins: `apply` refused with "no worklogs found; run
    `latexally scan` first" before it had even looked at `--captions`, which
    reads no worklog at all -- so a caption-only run could never get past the
    gate no matter what scope was given.
    """
    corpus = _corpus(tmp_path)
    result = CliRunner().invoke(
        main, [*_profile_args(tmp_path, corpus), "apply", "sem/hw/3/body.tex", "--captions"]
    )
    assert result.exit_code == 0, result.output
    assert "no worklogs found" not in result.output


def test_apply_captions_actually_captions(tmp_path: Path):
    r"""The other half of the same bug: even past the worklog gate, `--captions`
    was parsed and then silently dropped -- `apply_scope` was never called
    with `captions=True`. A floatless figure needs a `\begin{figure}` wrapper
    written around it to get one, so "nothing changed" was the only possible
    outcome regardless of scope.
    """
    corpus = _corpus(tmp_path)
    result = CliRunner().invoke(
        main,
        [*_profile_args(tmp_path, corpus), "apply", "sem/hw/3/body.tex", "--captions", "--write"],
    )
    assert result.exit_code == 0, result.output
    assert "1 captioned" in result.output or "captioned" in result.output
    body = (corpus / "sem" / "hw" / "3" / "body.tex").read_text()
    assert "\\caption{" in body
    assert "\\begin{figure}" in body


def test_apply_still_refuses_descriptions_without_a_worklog(tmp_path: Path):
    """Only `--captions` gets the exemption -- a description genuinely has
    nowhere to come from without `scan` having written one."""
    corpus = _corpus(tmp_path)
    result = CliRunner().invoke(
        main, [*_profile_args(tmp_path, corpus), "apply", "sem/hw/3/body.tex"]
    )
    assert result.exit_code != 0
    assert "no worklogs found" in result.output


def test_a_bare_file_scope_resolves_through_the_real_cli(tmp_path: Path):
    """The scope-resolution half of the same bug, exercised through `apply`
    rather than by calling `discover_assignments` directly (see
    test_discover.py for that)."""
    corpus = _corpus(tmp_path)
    result = CliRunner().invoke(
        main, [*_profile_args(tmp_path, corpus), "apply", "sem/hw/3/body.tex", "--captions"]
    )
    assert result.exit_code == 0, result.output
    assert "unknown scope" not in result.output

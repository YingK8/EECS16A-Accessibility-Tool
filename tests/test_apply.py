"""What `latexally apply` writes around a figure.

Both cases here were found by applying descriptions to real question-bank
files and then compiling the result.
"""

from pathlib import Path

import pytest

from latexally.apply import plan_file
from latexally.catalog.worklog import Entry
from latexally.config import CorpusScope, Profile


@pytest.fixture
def profile(tmp_path: Path) -> Profile:
    return Profile(name="test", corpus=CorpusScope(root=tmp_path, include=("**/*.tex",)))


def _apply(profile: Profile, path: Path, **entry_fields) -> str:
    """Plan the file and return the text that would be written."""
    plan = plan_file(path, profile, {})
    ids = [ref.id for ref in _refs(path, profile)]
    assert ids, "fixture produced no figure"
    entry = Entry(id=ids[0], status="approved", **entry_fields)
    plan = plan_file(path, profile, {entry.id: entry})
    return plan.buffer.apply(plan.original)


def _refs(path: Path, profile: Profile):
    from latexally.scan import scan_file

    return list(scan_file(path, profile))


def test_figure_sharing_its_line_keeps_the_inline_form(profile: Profile, tmp_path: Path):
    r"""A trailing ``\\`` must survive.

    `\end{Described}` leaves vertical mode, so a `\\` after it has no line to
    end and the build fails outright -- as questionBank/hw/12/q_pagerank.tex
    did, where the graph's closing line reads `\end{tikzpicture} \\`.
    """
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{center}\n"
        "    \\begin{tikzpicture}\n"
        "        \\node {1};\n"
        "    \\end{tikzpicture} \\\\\n"
        "    Graph B\n"
        "\\end{center}\n"
    )

    out = _apply(profile, path, description="Single node labelled 1.")

    assert "\\described{Single node labelled 1.}{%" in out
    assert "\\begin{Described}" not in out
    assert "\\end{tikzpicture}} \\\\" in out  # the author's line break, untouched


def test_long_description_is_written_after_the_figure(profile: Profile, tmp_path: Path):
    """Rule 8's `long` field has to reach the document to mean anything."""
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{figure}\n"
        "\\begin{tikzpicture}\n"
        "    \\node {1};\n"
        "\\end{tikzpicture}\n"
        "\\end{figure}\n"
    )

    out = _apply(
        profile,
        path,
        description="Single node labelled 1.",
        long_description="The node sits at the origin; nothing else is drawn.",
    )

    assert "\\begin{Described}{Single node labelled 1.}" in out
    assert out.index("\\end{Described}") < out.index("\\LongDescription{")
    assert "\\LongDescription{The node sits at the origin; nothing else is drawn.}" in out


def test_long_description_is_skipped_when_there_is_no_room(profile: Profile, tmp_path: Path):
    r"""`\LongDescription` opens with `\par`, which a shared line cannot take."""
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{center}\n"
        "    \\begin{tikzpicture}\n"
        "        \\node {1};\n"
        "    \\end{tikzpicture} \\\\\n"
        "\\end{center}\n"
    )

    from latexally.scan import scan_file

    fid = next(iter(scan_file(path, profile))).id
    entry = Entry(
        id=fid,
        status="approved",
        description="Single node labelled 1.",
        long_description="A longer account of the drawing.",
    )
    plan = plan_file(path, profile, {fid: entry})
    out = plan.buffer.apply(plan.original)

    assert "\\LongDescription" not in out
    assert any("long description not written" in reason for _, reason in plan.skipped)


@pytest.mark.parametrize(
    ("body", "wrapper"),
    [
        # Inline form: what every raster and every line-continuing figure gets.
        ("\\includegraphics{fig.png} and text\n", "\\described{"),
        # Block form.
        ("\\begin{tikzpicture}\n\\node {1};\n\\end{tikzpicture}\n", "\\begin{Described}"),
    ],
)
def test_applying_twice_wraps_once(profile: Profile, tmp_path: Path, body, wrapper):
    r"""A second run must be a no-op.

    ``_ALREADY`` used to recognise only the ``\begin{Described}`` ENVIRONMENT,
    never the inline ``\described{...}{%`` command -- so every raster was
    re-wrapped on each run, nesting a fresh wrapper inside the previous one.
    The description was then typeset, and spoken, once per run.
    """
    path = tmp_path / "q.tex"
    path.write_text(body)

    once = _apply(profile, path, description="A description long enough to push the "
                  "wrapper past a narrow lookbehind window, which is how this "
                  "went unnoticed for rasters carrying real sentences.")
    path.write_text(once)
    twice = _apply(profile, path, description="irrelevant, it must not be written")

    assert once.count(wrapper) == 1
    assert twice == once

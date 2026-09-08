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


def test_the_wrapper_keeps_the_figure_at_its_own_indentation(
    profile: Profile, tmp_path: Path
):
    r"""Found in ally-out hw/11/q_pca_movie.tex: the wrap dedented the figure.

    The prefix ends in a newline and the suffix opens with one, so without the
    figure's own leading whitespace the wrapped `\begin{tikzpicture}` and the
    closing `\end{Described}` land in column 0 while everything around them
    stays indented. It compiles, and it reads as damage in the diff.

    Interior lines keep the author's indentation -- the wrapper matches the
    figure's column, it does not nest the body one level in.
    """
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{figure}\n"
        "    \\begin{tikzpicture}[x=0.22cm]\n"
        "        \\node {1};\n"
        "    \\end{tikzpicture}\n"
        "\\end{figure}\n"
    )

    out = _apply(profile, path, description="Single node labelled 1.")

    assert "    \\begin{Described}{Single node labelled 1.}\n" in out
    assert "    \\begin{tikzpicture}[x=0.22cm]\n" in out, "dedented by the wrapper"
    assert "    \\end{Described}" in out
    assert "\n\\end{Described}" not in out, "closer must match the opener's column"
    assert "        \\node {1};\n" in out, "interior lines are left alone"


def test_the_long_description_lands_at_the_figure_s_column(
    profile: Profile, tmp_path: Path
):
    r"""The same dedent, one line further on.

    ``\LongDescription`` is appended after ``\end{Described}`` and opens with a
    newline of its own, so it went to column 0 even once the wrapper itself was
    fixed. It is body text belonging to the figure; it reads at the figure's
    column.
    """
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{figure}\n"
        "    \\begin{tikzpicture}[x=0.22cm]\n"
        "        \\node {1};\n"
        "    \\end{tikzpicture}\n"
        "\\end{figure}\n"
    )

    out = _apply(
        profile,
        path,
        description="Single node labelled 1.",
        long_description="The node sits at the origin.",
    )

    assert "    \\LongDescription{The node sits at the origin.}" in out
    assert "\n\\LongDescription" not in out, "must not land in column 0"


def test_a_graphic_on_the_artifact_allowlist_is_wrapped_decorative(
    profile: Profile, tmp_path: Path
):
    r"""A banner listed in the profile becomes an artifact, with no worklog entry.

    The worklog carries `at` and `alt_text` and nothing else, so a disposition
    computed by the scan does not survive being written to disk and read back.
    Before this, `_wrap_decorative` was unreachable and the three banners in
    `profiles/ee66.yaml` needed `\begin{Decorative}` typed around them by hand.
    """
    profile.figures.artifact_allowlist = ("figures/Berkeley_banner_1.jpg",)
    path = tmp_path / "q.tex"
    path.write_text("\\includegraphics{figures/Berkeley_banner_1.jpg}\n")

    plan = plan_file(path, profile, {})

    assert plan.artifacts == 1
    assert plan.wrapped == 0
    out = plan.buffer.apply(plan.original)
    assert "\\begin{Decorative}" in out
    assert "\\end{Decorative}" in out


def test_an_unlisted_graphic_is_still_described(profile: Profile, tmp_path: Path):
    """The allowlist is a list, not a heuristic: everything else gets described."""
    profile.figures.artifact_allowlist = ("figures/Berkeley_banner_1.jpg",)
    path = tmp_path / "q.tex"
    path.write_text("\\includegraphics{figures/lefthalfpic.jpg}\n")

    out = _apply(profile, path, description="The left half of the lecture-hall panorama.")

    assert "\\begin{Decorative}" not in out
    assert "The left half of the lecture-hall panorama." in out


def test_wrapping_decorative_twice_changes_nothing(profile: Profile, tmp_path: Path):
    """Re-running apply must not nest a second artifact inside the first."""
    profile.figures.artifact_allowlist = ("figures/Berkeley_banner_1.jpg",)
    path = tmp_path / "q.tex"
    path.write_text("\\includegraphics{figures/Berkeley_banner_1.jpg}\n")

    once = plan_file(path, profile, {}).buffer.apply(
        plan_file(path, profile, {}).original
    )
    path.write_text(once)
    again = plan_file(path, profile, {})

    assert again.artifacts == 0
    assert not again.changed


def test_floatless_figure_is_floated_so_its_caption_compiles(
    profile: Profile, tmp_path: Path
):
    r"""`caption` mode's promise is every figure, not just the ones already
    sitting in a `figure` environment.

    `\caption` outside a float is a hard LaTeX error, so a `tikzpicture`
    standing alone on its own line gets wrapped in one -- the only way to
    honour that promise for it.
    """
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{document}\n"
        "\\begin{tikzpicture}\n"
        "    \\node {1};\n"
        "\\end{tikzpicture}\n"
        "\\end{document}\n"
    )

    plan = plan_file(path, profile, {}, captions=True)
    out = plan.buffer.apply(plan.original)

    assert plan.captioned == 1
    assert not plan.skipped
    assert "\\begin{figure}[h!]" in out
    assert "\\centering" in out
    assert "\\end{figure}" in out
    assert out.index("\\begin{tikzpicture}") < out.index("\\caption{<<TODO:")
    # the drawing between the wrapper's two insertions is untouched
    assert "\\begin{tikzpicture}\n    \\node {1};\n\\end{tikzpicture}" in out


def test_inline_raster_is_left_alone_rather_than_floated(
    profile: Profile, tmp_path: Path
):
    r"""Floating a graphic mid-sentence would break the sentence around it.

    Same call `_wrap_described`/`_wrap_placeholder` already make between their
    inline and block forms; a caption follows it rather than overriding it.
    """
    path = tmp_path / "q.tex"
    path.write_text(
        "\\begin{document}\n"
        "See \\includegraphics{figures/diagram.png} above.\n"
        "\\end{document}\n"
    )

    plan = plan_file(path, profile, {}, captions=True)

    assert plan.captioned == 0
    assert plan.skipped and "inline" in plan.skipped[0][1]
    assert plan.buffer.apply(plan.original) == plan.original

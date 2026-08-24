"""Tests for the deterministic describers, the worklog and the apply layer."""

from __future__ import annotations

import pytest

from latexally.apply import DescriptionRejected, escape_description
from latexally.catalog.worklog import Entry, Worklog, read_worklog, write_worklog
from latexally.describe import describe
from latexally.catalog.worklog import PLACEHOLDER
from latexally.config import CorpusScope, Profile
from latexally.describe.common import latex_to_text, parse_options, split_top_level
from latexally.texlex import TexSource


def _skeleton(body: str, kind: str):
    source = TexSource(body)
    return describe(kind=kind, source=source, start=0, end=len(body))


# ---------------------------------------------------------------------- #
# option / text helpers
# ---------------------------------------------------------------------- #


def test_split_top_level_respects_nesting():
    assert split_top_level("a, b={c, d}, e") == ["a", "b={c, d}", "e"]


def test_parse_options_keeps_bare_flags():
    options = parse_options("only marks, xlabel=$a$, ymin=0")
    assert options["only marks"] == ""
    assert options["xlabel"] == "$a$"
    assert options["ymin"] == "0"


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"$C_1$", "C1"),
        (r"$V_{BB,min}$", "VBB,min"),
        (r"$\vec{x}$", "x"),
        (r"$\mathbf{A}$", "A"),
        (r"$\frac{1}{2}$", "1 over 2"),
    ],
)
def test_latex_to_text(source, expected):
    assert latex_to_text(source) == expected


# ---------------------------------------------------------------------- #
# pgfplots
# ---------------------------------------------------------------------- #

PLOT = r"""
\begin{tikzpicture}
\begin{axis}[xlabel=$a$, ylabel=$b$, xmin=0, xmax=9, ymin=0, ymax=9,
             xtick={0,1,...,9}]
\addplot+[only marks, blue] coordinates {(2,2) (4,6) (6,7) (8,8)};
\addplot[domain=0:9]{0.95 * x + 1};
\end{axis}
\end{tikzpicture}
"""


def test_pgfplots_reads_axes_ranges_and_data():
    skeleton = _skeleton(PLOT, "tikzpicture")
    assert skeleton.genre == "plot"
    assert skeleton.confidence == "high"
    assert "b versus a" in skeleton.summary
    joined = " ".join(skeleton.details)
    assert "(2, 2)" in joined and "(8, 8)" in joined
    assert "0.95 * x + 1" in joined


def test_pgfplots_expands_tick_ellipsis():
    skeleton = _skeleton(PLOT, "tikzpicture")
    # {0,1,...,9} must become ten ticks, not three literal entries.
    assert "ticks at 0, 1, 2, 3, 4, 5, 6, 7, 8, 9" in " ".join(skeleton.details)


def test_pgfplots_emits_a_table_when_there_are_too_many_points():
    points = " ".join(f"({i},{i * 2})" for i in range(20))
    body = (
        r"\begin{axis}[xlabel=$t$, ylabel=$y$]"
        rf"\addplot coordinates {{{points}}};"
        r"\end{axis}"
    )
    skeleton = _skeleton(body, "axis")
    assert len(skeleton.table) == 20
    assert skeleton.table_header == ("t", "y")
    assert any("data table" in need for need in skeleton.needs)


# ---------------------------------------------------------------------- #
# circuitikz
# ---------------------------------------------------------------------- #

CIRCUIT = r"""
\begin{circuitikz}
\draw (0, 0) node[ground]{}
  (0, 3) to [C=$C_1$,v=$V_1$] ++ (0, -3)
  (0, 3) to [switch, l=$S_1$] ++ (6, 0)
        to [C=$C_2$, v=$V_2$] ++ (0, -3)
  node[ground]{};
\end{circuitikz}
"""


def test_circuit_lists_components_with_labels():
    skeleton = _skeleton(CIRCUIT, "circuitikz")
    assert skeleton.genre == "circuit"
    assert "2 capacitors (C1, C2)" in skeleton.summary
    assert "1 switch (S1)" in skeleton.summary
    assert "2 ground connections" in skeleton.summary


def test_circuit_edges_connect_distinct_nodes():
    # Regression: taking the LAST coordinate before the next component recorded
    # C1 as connecting (0,3) to (0,3), because the text between two components
    # holds both the endpoint and the start of the next branch.
    skeleton = _skeleton(CIRCUIT, "circuitikz")
    capacitor = next(d for d in skeleton.details if d.startswith("Capacitor C1"))
    endpoints = capacitor.split(" connects ")[1].split(" to ")
    assert endpoints[0].strip() != endpoints[1].split(",")[0].strip()


def test_circuit_records_labelled_voltages():
    skeleton = _skeleton(CIRCUIT, "circuitikz")
    assert any("voltage V1" in detail for detail in skeleton.details)


def test_circuit_wrapping_an_image_says_so():
    body = (
        r"\begin{circuitikz}\node (b) at (0,0) "
        r"{\includegraphics{photo.pdf}};\end{circuitikz}"
    )
    skeleton = _skeleton(body, "circuitikz")
    assert any("photo.pdf" in need for need in skeleton.needs)
    assert skeleton.confidence == "low"


# ---------------------------------------------------------------------- #
# tikz nodes and state machines
# ---------------------------------------------------------------------- #


def test_node_labels_survive_nested_braces():
    # `{$V_{in}$}` is the commonest label shape in this corpus and defeats a
    # `\{([^{}]+)\}` pattern outright.
    body = r"\begin{tikzpicture}\node at (0,0) {$V_{in}$};\end{tikzpicture}"
    skeleton = _skeleton(body, "tikzpicture")
    assert "Vin" in " ".join(skeleton.details)


def test_path_form_nodes_without_a_backslash_are_found():
    body = r"\begin{tikzpicture}\draw (0,0) -- node[above] {Gain} (2,0);\end{tikzpicture}"
    skeleton = _skeleton(body, "tikzpicture")
    assert "Gain" in " ".join(skeleton.details)


def test_state_machine_reports_states_and_transitions():
    body = r"""
    \begin{tikzpicture}
    \node[state] (q_2) {$A$};
    \node[state] (q_3) [right of=q_2] {$B$};
    \path[->] (q_2) edge [bend left] node {1} (q_3)
              (q_3) edge [bend left] node {3/4} (q_2)
                    edge [loop above] node {1/4} ();
    \end{tikzpicture}
    """
    skeleton = _skeleton(body, "tikzpicture")
    assert skeleton.genre == "state-machine"
    assert "2 states" in skeleton.summary
    joined = " ".join(skeleton.details)
    assert "A to B" in joined and "self-loop" in joined


# ---------------------------------------------------------------------- #
# rasters
# ---------------------------------------------------------------------- #


def test_raster_offers_no_invented_description():
    skeleton = describe(
        kind="includegraphics",
        source=TexSource(""),
        start=0,
        end=0,
        image_path="figs/photo.png",
    )
    assert skeleton.summary == ""
    assert skeleton.needs


# ---------------------------------------------------------------------- #
# escaping
# ---------------------------------------------------------------------- #


def test_escape_description_speaks_braces_rather_than_refusing_them():
    """The previous tool refused any description containing braces, which in a
    linear-algebra course rules out most natural phrasings."""
    assert escape_description("the set {0, 1}") == "the set 0, 1"


def test_escape_description_words_tex_specials_never_escapes_them():
    r"""tagpdf writes /Alt byte for byte, so a `\%` would be SPOKEN as
    "backslash percent". The only safe output is prose with no specials left."""
    spoken = escape_description("50% of R_1 & C_2")
    assert spoken == "50 percent of R sub 1 and C sub 2"
    assert "\\" not in spoken


def test_escape_description_rejects_empty():
    with pytest.raises(DescriptionRejected):
        escape_description("   ")


def test_escape_description_collapses_whitespace():
    assert escape_description("a\n  b\tc") == "a b c"


# ---------------------------------------------------------------------- #
# worklog round-trip
# ---------------------------------------------------------------------- #


def test_worklog_round_trip_preserves_human_fields(tmp_path):
    entry = Entry(
        id="fig-0123456789ab",
        kind="circuitikz",
        genre="circuit",
        sites=[("a/b.tex", 12)],
        caption="A caption",
    )
    worklog = Worklog(scope="demo", path=tmp_path / "demo.md", entries={entry.id: entry})
    worklog.path.write_text(write_worklog(worklog, scope="demo"), encoding="utf-8")

    text = worklog.path.read_text()
    text = text.replace(
        PLACEHOLDER, "Two capacitors joined by a switch."
    ).replace("- status: todo", "- status: approved")
    worklog.path.write_text(text)

    reloaded = read_worklog(worklog.path)
    restored = reloaded.entries[entry.id]
    assert restored.description == "Two capacitors joined by a switch."
    assert restored.status == "approved"
    assert restored.is_done


def test_unfilled_worklog_entry_is_not_done(tmp_path):
    entry = Entry(id="fig-aaaaaaaaaaaa", kind="tikzpicture")
    worklog = Worklog(scope="s", path=tmp_path / "s.md", entries={entry.id: entry})
    worklog.path.write_text(write_worklog(worklog, scope="s"), encoding="utf-8")
    restored = read_worklog(worklog.path).entries[entry.id]
    # The placeholder comment must not be read back as a description.
    assert restored.description == ""
    assert not restored.is_done


def test_descriptions_survive_a_different_output_directory(tmp_path):
    """Human text lives with the corpus, not with one run's output folder.

    Found by building the demos: `-o ally-out` made every scan report "0
    described, 17 outstanding" while approved descriptions for six of those
    figures sat in the corpus catalogue. The build then shipped the figures
    with no /Alt, and the only visible symptom was a figure that said nothing.
    """
    from latexally.catalog import build_catalog, worklog_dir
    from latexally.catalog.worklog import read_worklog

    corpus = tmp_path / "corpus"
    (corpus / "hw").mkdir(parents=True)
    (corpus / "hw" / "q.tex").write_text(
        "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n"
    )
    profile = Profile(corpus=CorpusScope(root=corpus))

    # A person describes the figure, in the corpus catalogue.
    first = build_catalog(profile, files=[corpus / "hw" / "q.tex"])
    identity = next(iter(first.entries))
    shard = next(iter(first.worklogs)).name
    base = worklog_dir(profile)
    base.mkdir(parents=True, exist_ok=True)
    log = read_worklog(next(iter(first.worklogs)))
    entry = log.entries[identity]
    entry.description = "A line rising to one comma one."
    entry.status = "approved"
    (base / shard).write_text(write_worklog(log, scope=log.scope))

    # A later run sends its worklogs somewhere else entirely.
    elsewhere = build_catalog(
        profile, files=[corpus / "hw" / "q.tex"], output_root=tmp_path / "out"
    )

    carried = elsewhere.entries[identity]
    assert carried.is_done, "approved description was lost by redirecting output"
    assert carried.description == "A line rising to one comma one."

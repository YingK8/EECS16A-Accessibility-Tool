"""Deriving a conforming colour from the one the course already uses.

The behaviour these pin down is "change it as little as possible", not "make it
conform". Both are easy; only the first keeps the document looking like itself.
"""

import colorsys
import shutil

import pytest

from latexally.check.contrast import (
    contrast_ratio,
    minimum_conforming,
    palette_value,
    rgb_to_hex,
)
from latexally.config import ColorPolicy, CorpusScope, Profile
from latexally.run import ColorChoice, hex_to_rgb

WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)


def ratio(hexed: str, background=WHITE) -> float:
    return contrast_ratio(hex_to_rgb(hexed), background)


def test_a_conforming_colour_is_left_alone():
    """#B31AB3 measures 5.65:1. Returning a 'fix' for it would be noise."""
    assert minimum_conforming(hex_to_rgb("#B31AB3"), background=WHITE, target=4.5) is None


def test_the_course_blue_is_darkened_only_as_far_as_needed():
    proposed = minimum_conforming(hex_to_rgb("#3399E6"), background=WHITE, target=4.5)

    assert ratio(proposed) >= 4.5
    # The floor, not a comfortable distance past it. #0645AD conformed at
    # 8.53:1 and was reported as harder to read than the colour it replaced.
    assert ratio(proposed) < 5.0
    assert ratio("#0645AD") > 8.0  # what this replaced, for the contrast


def test_hue_and_saturation_survive_the_darkening():
    """A darker blue, not a different colour."""
    original = hex_to_rgb("#3399E6")
    proposed = hex_to_rgb(minimum_conforming(original, background=WHITE, target=4.5))

    hue_before, _, sat_before = colorsys.rgb_to_hls(*original)
    hue_after, _, sat_after = colorsys.rgb_to_hls(*proposed)

    assert hue_after == pytest.approx(hue_before, abs=0.01)
    assert sat_after == pytest.approx(sat_before, abs=0.05)


@pytest.mark.parametrize("original", ["#3399E6", "#FF0000", "#7FBF7F", "#CCCC00"])
def test_the_rounded_hex_is_what_conforms(original):
    """The search steps 8-bit values, because the 8-bit value is what ships.

    A float that just clears 4.5:1 can round to a hex that does not, and
    nothing downstream would ever notice -- the profile, the .tex and the PDF
    all carry the rounded one.
    """
    proposed = minimum_conforming(hex_to_rgb(original), background=WHITE, target=4.5)
    assert proposed == rgb_to_hex(hex_to_rgb(proposed))  # already on the grid
    assert ratio(proposed) >= 4.5


def test_a_dark_page_lightens_instead_of_darkening():
    """Darkening #0645AD on black would walk it to invisibility."""
    proposed = minimum_conforming(hex_to_rgb("#0645AD"), background=BLACK, target=4.5)

    assert ratio(proposed, BLACK) >= 4.5
    assert sum(hex_to_rgb(proposed)) > sum(hex_to_rgb("#0645AD"))


def _profile() -> Profile:
    return Profile(
        name="test",
        corpus=CorpusScope(root=".", include=("**/*.tex",)),
        colors=ColorPolicy(
            originals={
                "solutionColor": "#3399E6",  # 3.07:1
                "redish": "#FF0000",  # 4.00:1
                "blueish": "#B31AB3",  # 5.65:1, conforms
            },
        ),
    )


def test_replacements_are_derived_and_skip_what_already_conforms():
    """`conforming` mode, which is now the narrower of the two remaps.

    Explicit about the mode: the default moved to `palette`, and this test is
    about the DERIVATION -- darken each name just enough, leave a name that
    already passes alone -- which only `conforming` performs.
    """
    replacements = ColorChoice(mode="conforming").replacements(_profile())

    assert replacements == {"solutionColor": "#187AC4", "redish": "#EE0000"}
    assert "blueish" not in replacements


def test_palette_mode_applies_everything_without_being_asked():
    """The palette is applied, not proposed.

    `\\accesspalette` binds these in the .sty whether or not anyone opens the
    colour screen, so `replacements` has to say so. It used to return only what
    a person had confirmed, which meant the colour table and `run.yaml` both
    described a run where nothing changed, next to a build that changed
    everything.
    """
    assert ColorChoice().mode == "palette"
    assert ColorChoice().replacements(_profile()) == {
        # the profile's own names
        "solutionColor": "#1754FF",
        "redish": "#D20000",
        "blueish": "#B800B8",
        # and xcolor's, which `\accesspalette` binds too and this profile never
        # declares. A corpus drawing in `green!70!black` had its green remapped
        # by a run that never mentioned green.
        "red": "#D20000",
        "blue": "#1754FF",
        "green": "#007900",
        "purple": "#B800B8",
        "orange": "#A55000",
        "solansColor": "#1754FF",
        "answerColor": "#1754FF",
    }


def test_a_hand_typed_hex_wins_over_the_palette():
    chosen = ColorChoice()
    chosen.set("solutionColor", "#123456")
    assert chosen.replacements(_profile())["solutionColor"] == "#123456"


def test_rejecting_a_colour_is_recorded_as_an_override_to_its_own_original():
    """How "leave this one alone" survives into the build.

    There is no third state to store: an override to the colour's own value is
    both the record that someone decided, and the value that wins over the
    palette when the preamble is emitted. `blueish` passing contrast is not a
    reason it cannot be rejected -- the palette moves it for consistency, not
    for conformance, and that is a choice a person is allowed to decline.
    """
    chosen = ColorChoice()
    chosen.set("blueish", "#B31AB3")  # the course's own value
    assert chosen.replacements(_profile())["blueish"] == "#B31AB3"
    # Still applied everywhere it was not rejected.
    assert chosen.replacements(_profile())["redish"] == "#D20000"


def test_palette_mode_still_derives_nothing_per_name():
    """`conforming` computed a different value for every colour NAME, which is
    how one page ended up drawing its answer text in #187AC4 and its answer
    vectors in #0000FF. The palette is a lookup, so two names that share a hue
    cannot come out different."""
    replacements = ColorChoice().replacements(_profile())
    assert replacements["solutionColor"] == palette_value("blue")


def test_house_mode_still_changes_nothing():
    """An existing run.yaml carrying `mode: house` must keep meaning that."""
    assert ColorChoice(mode="house").replacements(_profile()) == {}


def test_a_confirmed_choice_wins_over_the_derivation():
    choice = ColorChoice()
    choice.set("solutionColor", "#004400")

    assert choice.replacements(_profile())["solutionColor"] == "#004400"


def test_the_xcolor_existence_guard_uses_the_right_csname():
    r"""The guard is `\string\color@<name>`, not `color@<name>`.

    xcolor builds the control sequence with a literal backslash as the first
    character of its name, so `\@ifundefined{color@foo}` is true for every
    colour that has ever been defined. Written the wrong way it made
    \accessconformingcolors a silent no-op: a document defining solutionColor
    as rgb(0.2,0.6,0.9) still filled `0.2 0.6 0.9` in the PDF while the run
    reported a conforming palette. A grep is a poor test, but the alternative
    is a LaTeX compile, and this is the exact character that was wrong.
    """
    from pathlib import Path

    core = (Path(__file__).resolve().parent.parent / "tex" / "latexally-core.sty").read_text()
    assert r"\@ifundefined { color@" not in core
    assert core.count(r"\@ifundefined { \string \color@") == 2


# ---------------------------------------------------------------------- #
# colours used but never defined: \color{red}
# ---------------------------------------------------------------------- #


def _findings(tmp_path, body: str):
    from latexally.check.rules import check_source

    path = tmp_path / "q.tex"
    path.write_text(body)
    return [f for f in check_source(path, _profile()) if f.rule == "ALLY-SRC-010"]


def test_a_builtin_colour_used_without_being_defined_is_still_judged(tmp_path):
    r"""There is no \definecolor to read, so nothing used to look at this.

    sp26.sty's \edit{} macro and the discussion tally boxes both set body text
    with a bare \color{red} at 4.00:1, and the checker reported nothing.
    """
    found = _findings(tmp_path, "\\newcommand{\\edit}[1]{{\\color{red} #1}}\n")

    assert [f.data["color"] for f in found] == ["red"]
    assert found[0].data["ratio"] == 4.0
    assert "#EE0000" in found[0].hint


def test_a_conforming_builtin_is_not_reported(tmp_path):
    """Plain blue is 8.59:1. The corpus uses it 63 times and it is fine."""
    assert _findings(tmp_path, "\\textcolor{blue}{a link}\n") == []


def test_a_colour_defined_in_the_file_is_judged_once(tmp_path):
    """Its real value wins; the built-in of the same name must not double-report."""
    found = _findings(
        tmp_path,
        "\\definecolor{red}{HTML}{EE0000}\n\\textcolor{red}{text}\n",
    )

    assert found == []  # the local definition conforms


def test_an_xcolor_expression_is_left_alone(tmp_path):
    r"""`red!60` has no single resolvable value, so guessing would be worse."""
    assert _findings(tmp_path, "\\textcolor{red!60}{text}\n") == []


def test_each_name_is_reported_once_however_often_it_is_used(tmp_path):
    """Forty identical findings are forty reasons to ignore the rule."""
    found = _findings(tmp_path, "\\color{red}a\\color{red}b\\color{red}c\n")

    assert len(found) == 1


def test_the_only_installed_profile_is_used_without_being_named(tmp_path, monkeypatch):
    """`-p eecs16a` on every command is noise while there is one course.

    Not cosmetic: the flag was required, and omitting it loaded empty defaults
    -- no corpus, no palette -- rather than failing, so a forgotten flag looked
    like an empty corpus.
    """
    from latexally import config as config_module
    from latexally.config import builtin_profile_dir, load_profile

    # Pinned to a directory holding exactly one profile, because that is the
    # condition the inference is about. Reading the repo's own profiles/ made
    # this test's result depend on how many courses happen to be installed --
    # adding a second one turned a passing assertion into a failure about
    # something else entirely.
    only = tmp_path / "profiles"
    only.mkdir()
    shutil.copy(builtin_profile_dir() / "eecs16a.yaml", only / "eecs16a.yaml")
    monkeypatch.setattr(config_module, "builtin_profile_dir", lambda: only)

    profile = load_profile()

    assert profile.name == "eecs16a"
    assert profile.colors.originals, "the default profile must carry its palette"


# ---------------------------------------------------------------------- #
# one palette, for text and for drawings
# ---------------------------------------------------------------------- #

#: The hue each token is derived from. `purple` is derived from MAGENTA on
#: purpose: it is the token for the 265-345 bin, and the most legible form of
#: xcolor's own `purple` (#BF0040) measures at hue 349 -- inside the red bin,
#: next to the red token. Hue 300 is the representative that stays clear.
XCOLOR_BASE = {
    "blue": "#1754FF",
    "red": "#FF0000",
    "green": "#00FF00",
    "orange": "#FF8000",
    "purple": "#FF00FF",
}

#: WCAG 1.4.3 AA for text on the page, and 1.4.11 for a line beside black axes.
PAGE_FLOOR = 4.5
INK_FLOOR = 3.0


def _coloraide():
    return pytest.importorskip("coloraide", reason="the palette is derived with it")


def test_every_colour_is_legible_on_the_page_and_beside_black_ink():
    r"""One colour has to serve two neighbours, because one page holds both.

    fa26/dis/01A puts a table ruled in the solution colour on one page and a
    plot drawn in blue on another, and a reader sees them as one document. The
    palette used to answer that with two blues -- #0000FF for prose at 8.59:1,
    #4E84FF for drawings at 3.46:1 -- because no single value could clear the
    page floor and still clear 3:1 against black axes after a `!70!black` mix
    darkened it. Snapping removes the mix, so one value does both now.
    """
    _coloraide()
    from coloraide import Color

    from latexally.check.contrast import PALETTE_BINDINGS, palette_value

    for name in ("blue", "red", "green", "orange", "purple"):
        value = palette_value(name)
        page = Color(value).contrast("white", method="wcag21")
        ink = Color(value).contrast("black", method="wcag21")
        assert page >= PAGE_FLOOR, f"{name} is {page:.2f}:1 against the page"
        assert ink >= INK_FLOOR, f"{name} is {ink:.2f}:1 against black ink"
    assert set(PALETTE_BINDINGS.values()) == {
        "allyBlue",
        "allyRed",
        "allyGreen",
        "allyOrange",
        "allyPurple",
    }, "five tokens, one per hue bin"


def test_the_palette_is_the_most_balanced_form_of_each_hue():
    """Re-derived, not trusted: hold the OKLCH hue and chroma, move lightness.

    The objective is ``min(page/4.5, ink/3.0)`` -- the same relative margin over
    each floor rather than the largest number on either one. Maximising page
    contrast alone lands every colour back on the near-black values that fail
    beside black axes; maximising ink contrast alone lands them on the pale ones
    that fail as text.
    """
    _coloraide()
    from coloraide import Color

    from latexally.check.contrast import palette_value

    for name, original in XCOLOR_BASE.items():
        base = Color(original).convert("oklch")
        best, score = None, -1.0
        for step in range(1, 1000):
            candidate = base.clone().set("l", step / 1000).convert("srgb").fit()
            margin = min(
                candidate.contrast("white", method="wcag21") / PAGE_FLOOR,
                candidate.contrast("black", method="wcag21") / INK_FLOOR,
            )
            if margin > score:
                best, score = candidate, margin
        assert best.to_string(hex=True).upper() == palette_value(name), (
            f"{name} should be {best.to_string(hex=True)}, not {palette_value(name)}"
        )
        assert score > 1.2, f"{name} clears its floors by only {score:.2f}x"


def test_the_colours_stay_telling_apart():
    """Five lines on one axis have to be five colours, not four and a pair."""
    _coloraide()
    from itertools import combinations

    from coloraide import Color

    from latexally.check.contrast import PALETTE

    for one, other in combinations(PALETTE, 2):
        distance = Color(PALETTE[one]).delta_e(PALETTE[other], method="2000")
        assert distance >= 10, f"{one} and {other} are {distance:.1f} dE2000 apart"


def test_yellow_is_binned_because_a_legible_yellow_is_not_yellow():
    r"""Yellow is the hue that cannot be fixed by darkening, so it is binned.

    Green darkened is still green -- its nearest CSS name goes from `lime` to
    `forestgreen`. Yellow darkened enough to clear the page is #8F8F00, whose
    nearest CSS name is `olive`: not a darker yellow, a different colour. The
    old palette left it alone for that reason and reported it instead. With five
    bins it lands on orange, the neighbouring hue that does survive darkening --
    a decision, not an accident, so it is asserted here.
    """
    _coloraide()
    from coloraide import Color
    from coloraide.css.color_names import name2val_map

    from latexally.check.contrast import PALETTE_BINDINGS, snap_bin

    assert "yellow" not in PALETTE_BINDINGS
    assert snap_bin((1.0, 1.0, 0.0)) == "orange"

    named = {
        name: Color("srgb", [channel / 255 for channel in value[:3]])
        for name, value in name2val_map.items()
    }

    def reads_as(color):
        return min(named, key=lambda name: Color(color).delta_e(named[name], method="2000"))

    legible_yellow = (
        Color("#FFFF00").convert("oklch").set("l", 0.62).convert("srgb").fit()
    )
    assert legible_yellow.contrast("white", method="wcag21") >= 3.0
    assert reads_as(legible_yellow) == "olive"
    assert reads_as("#007900") == "green", "green survives the same treatment"


# ---------------------------------------------------------------------- #
# snapping: one colour per hue, however the source spelled it
# ---------------------------------------------------------------------- #


def _rgb(spec: str):
    from latexally.check.contrast import parse_color

    return parse_color("HTML", spec.lstrip("#"))


def test_the_spellings_this_corpus_actually_uses_land_on_one_colour():
    r"""The measurement this exists for.

    Counted across questionBank, blue arrives 27 ways and green 14 -- and only
    the bare word `blue` was ever reachable by rebinding a name. Every value
    below is a real spelling from the corpus, resolved the way xcolor resolves
    it: `blue!40!black` through the REBOUND blue, which is what made the two
    disagree in the first place.
    """
    from latexally.check.contrast import snap_bin

    blues = {
        "#1754FF": "blue, already the token",
        "#092266": "blue!40!black, through the rebound blue",
        "#00FFFF": "cyan",
        "#17DAFF": "cyan!70!blue",
        "#1F77B4": "steelblue31119180, 98 matplotlib exports",
        "#80CCFF": "lightblue",
        "#0040FF": "mydarkblue",
        "#56B4E9": "skyblue",
        "#008080": "teal",
        "#D1DDFF": "blue!20, a wash -- snapped to full, not kept pale",
    }
    for value, why in blues.items():
        assert snap_bin(_rgb(value)) == "blue", f"{value} ({why}) is not blue"

    greens = {"#007900": "green", "#004300": "black!45!green", "#228B22": "ForestGreen"}
    for value, why in greens.items():
        assert snap_bin(_rgb(value)) == "green", f"{value} ({why}) is not green"

    assert snap_bin(_rgb("#9400D1")) == "purple", "mauve"
    assert snap_bin(_rgb("#FFD700")) == "orange", "gold"
    assert snap_bin(_rgb("#964B00")) == "orange", "brown"
    assert snap_bin(_rgb("#FF8E85")) == "red", "red!60"


def test_the_faint_and_the_achromatic_are_left_where_they_are():
    """A grid line is meant to be faint, and black ink is meant to be black."""
    from latexally.check.contrast import snap_bin

    for value, why in {
        "#000000": "black",
        "#FFFFFF": "white",
        "#CCCCCC": "gray!40",
        "#333333": "black!80",
        "#B0B0B0": "darkgray176, from the same matplotlib exports",
    }.items():
        assert snap_bin(_rgb(value)) is None, f"{value} ({why}) should be left alone"


def test_snapping_a_snapped_colour_changes_nothing():
    """Every token has to sit inside its own bin, or the hook oscillates.

    This is what rules out a purple token derived from xcolor's own `purple`:
    its most legible form measures at hue 349, so a second pass over a drawing
    would move it to red.
    """
    from latexally.check.contrast import PALETTE, PALETTE_BINDINGS, snap_bin

    for name, token in PALETTE_BINDINGS.items():
        if name in ("blue", "red", "green", "orange", "purple"):
            assert snap_bin(_rgb(PALETTE[token])) == name, f"{token} leaves its own bin"
    assert snap_bin(_rgb("#F54C6A")) == "red", "the reason the purple token is magenta's"

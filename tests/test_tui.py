"""The interactive runner, driven by Textual's pilot.

The old suite drove a scripted terminal: the wizard was a function from
``(state, answers)`` to a ``RunConfig``, and that property was the argument for
building it on Rich rather than on a widget toolkit. The argument lost. Rich's
``Live`` clips at the terminal height and cannot scroll, so a real scope hid the
footer explaining how to tick a box and put every row past the fold out of
reach. The two tests at the bottom of this file are that failure, named.

What is asserted has not changed: the ``RunConfig`` a user ends up with, and the
text they were actually shown. ``visible`` reads the composited screen, so an
assertion about a phrase is an assertion that the phrase was *on screen* --
which is exactly what the previous front-end could not guarantee.

The sixteen tests that covered ``tui.prompt`` -- raw key decoding, cursor
wrapping, the typed-line fallback -- are gone with the module they tested.
Textual owns that layer now.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import (
    Button,
    DataTable,
    Input,
    RadioSet,
    SelectionList,
    Static,
)
from textual.worker import WorkerCancelled

from latexally.config import ColorPolicy, CorpusScope, Profile
from latexally.errors import LatexAllyError
from latexally.run import RunConfig
from latexally.tui.app import (
    AltScreen,
    Choice,
    ColorsScreen,
    DocumentsScreen,
    LatexAllyApp,
    OutputScreen,
    ModeScreen,
    ProfileScreen,
    ReviewScreen,
    RevertScreen,
    ScopeScreen,
    StandardsScreen,
)


@pytest.fixture
def two_scopes(profile: Profile) -> Profile:
    """A profile with two named scopes, so ← → have somewhere to go."""
    return replace(
        profile,
        corpus=replace(
            profile.corpus,
            named={"sem": ("sem/**/*.tex",), "homeworks": ("sem/hw/**/*.tex",)},
        ),
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    for name in ("1", "2", "3"):
        directory = tmp_path / "sem" / "hw" / name
        directory.mkdir(parents=True)
        (directory / f"sol{name}.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\\end{document}\n"
        )
    for name in ("01A", "01B"):
        directory = tmp_path / "sem" / "dis" / name
        directory.mkdir(parents=True)
        (directory / f"sol{name}.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\\end{document}\n"
        )
    return tmp_path


@pytest.fixture
def profile(corpus: Path) -> Profile:
    return Profile(
        name="test",
        corpus=CorpusScope(
            root=corpus,
            include=("**/*.tex",),
            named={"sem": ("sem/**/*.tex",)},
            kinds={"hw": "homework", "dis": "discussion"},
        ),
        # A real palette, so the colour screen's table, swatches and contrast
        # arithmetic are exercised rather than skipped.
        colors=ColorPolicy(
            originals={
                "solutionColor": "#3399E6",  # 3.07:1, fails
                "redish": "#FF0000",  # 4.00:1, fails
                "blueish": "#B31AB3",  # 5.65:1, passes and must be left alone
            },
        ),
    )


# ---------------------------------------------------------------------- #
# driving
# ---------------------------------------------------------------------- #

#: Wide and tall enough that a step's own content is not the thing under test.
#: The deliberately short terminal lives in the two scrolling tests below.
SIZE = (110, 44)

_BOX = "│╭╮╰╯─┏┓┗┛┃━┡┩╇╈┳┻╋▔▁▊▎▐▌⭘▏▂▅▇█▄"


def visible(app: LatexAllyApp) -> str:
    """What is on the screen right now, whitespace collapsed, boxes stripped.

    Composited rather than reconstructed from the widget tree, so anything
    clipped, scrolled away or covered is absent here too -- which is the point.
    """
    text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
    for char in _BOX:
        text = text.replace(char, " ")
    return " ".join(text.split())



def _out(profile: Profile) -> Path:
    """Where a defaulted output root now points: `<corpus>/ally-out`.

    Anchored to the corpus rather than the working directory, so a run started
    from the tool's own checkout does not write its output there.
    """
    return profile.corpus.root.resolve() / "ally-out"


async def settle(pilot) -> None:
    """Let the scope worker finish. Scanning runs off the message loop.

    The scan worker is ``exclusive``, so moving along the scope row cancels the
    one before it; a cancelled worker is the normal case here, not a failure.
    """
    try:
        await pilot.app.workers.wait_for_complete()
    except WorkerCancelled:
        pass
    await pilot.pause()


async def press(pilot, *keys: str) -> None:
    for key in keys:
        await pilot.press(key)
        await settle(pilot)


async def advance(pilot, times: int = 1) -> None:
    await press(pilot, *["n"] * times)


async def retreat(pilot, times: int = 1) -> None:
    await press(pilot, *["backslash"] * times)


async def walk_to(pilot, screen) -> None:
    """Step forward until the named screen is showing."""
    while not isinstance(pilot.app.screen, screen):
        await advance(pilot)



async def open_scope(pilot) -> None:
    """Settle, then step past the local-or-choose question onto the picker.

    That question is step 1 now, so a test that settles and then reads the
    scope list is reading the wrong screen.
    """
    await settle(pilot)
    if isinstance(pilot.app.screen, ModeScreen):
        await advance(pilot)
    await settle(pilot)


async def set_scope(pilot, path: str) -> None:
    # The scope picker is step 2 now: step 1 asks local-or-choose. Advance only
    # from that first screen -- `walk_to` can only go forwards, so calling it
    # from anywhere later spins pressing Next against a screen it will never
    # leave.
    if isinstance(pilot.app.screen, ModeScreen):
        await advance(pilot)
    field = pilot.app.screen.query_one("#scope-path", Input)
    field.value = path
    await field.action_submit()
    await settle(pilot)


def highlighted(app: LatexAllyApp) -> list[str]:
    """Every run of reverse-video text on screen, in order.

    The cursor is reverse video rather than a glyph, and ``visible`` throws
    styling away -- so "which row am I on" has to be asked of the styles.
    """
    return [
        segment.text.strip()
        for strip in app.screen._compositor.render_strips()
        for segment in strip
        if segment.style and segment.style.reverse and segment.text.strip()
    ]


async def tick(pilot, *values: str, list_id: str = "assignments") -> None:
    choices = pilot.app.screen.query_one(f"#{list_id}", SelectionList)
    for value in values:
        choices.select(value)
    await pilot.pause()


async def untick(pilot, *values: str, list_id: str = "assignments") -> None:
    choices = pilot.app.screen.query_one(f"#{list_id}", SelectionList)
    for value in values:
        choices.deselect(value)
    await pilot.pause()


async def scope_all_homework(pilot) -> None:
    """The scope almost every test starts from: the three homeworks."""
    await set_scope(pilot, "sem/hw")
    await press(pilot, "a")


# ---------------------------------------------------------------------- #
# scope: asked first, without having to be found
# ---------------------------------------------------------------------- #


async def test_the_first_question_is_asked_at_startup_with_no_navigation(
    profile: Profile,
):
    """The first thing on screen is the one question the tool cannot answer.

    It used to be the scope picker itself. That put a list of sixty-two
    directories above the question of whether you wanted a list at all, and
    took the focus with it -- so the arrows meant for the question went to the
    list instead.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, ModeScreen)
        assert "Which material?" in visible(app)
        # The arrows answer the question, because it holds the focus.
        assert isinstance(app.focused, Choice)
        assert app.scope_mode == "choose"

async def test_selecting_a_kind_selects_its_assignments(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        assert set(app.config.assignments) == {"sem/hw/1", "sem/hw/2", "sem/hw/3"}


async def test_a_narrower_scope_selects_only_that(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/dis")
        await press(pilot, "a")
        assert set(app.config.assignments) == {"sem/dis/01A", "sem/dis/01B"}


async def test_a_whole_scope_can_be_taken_at_once(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await press(pilot, "a")
        assert len(app.config.assignments) == 5


async def test_a_subset_is_picked_by_ticking_only_those(profile: Profile):
    """Nothing starts ticked, so narrowing is picking, not deselection."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await tick(pilot, "sem/hw/1", "sem/hw/3")
        assert set(app.config.assignments) == {"sem/hw/1", "sem/hw/3"}


async def test_an_explicit_path_can_be_used(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/hw")
        await press(pilot, "a")
        assert set(app.config.assignments) == {"sem/hw/1", "sem/hw/2", "sem/hw/3"}


async def test_a_scope_says_what_it_holds_rather_than_filtering_again(
    profile: Profile,
):
    """Kind was a second filter over the same axis as scope, so it is a
    sentence now: the profile declares both a `homeworks` scope and a
    `homework` kind, and only one of them can be the control."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        assert "5 directories — 2 discussion, 3 homework" in visible(app)
        await set_scope(pilot, "sem/hw")
        assert "3 directories — 3 homework" in visible(app)


async def test_a_single_assignment_scope_does_not_ask_which_assignment(
    profile: Profile,
):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/hw/1")
        assert app.config.assignments == ("sem/hw/1",)
        assert "the only assignment here" in visible(app)


async def test_a_bad_path_reports_and_selects_nothing(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/nope")
        assert app.config.assignments == ()
        shown = visible(app)
        assert "No buildable assignments" in shown or "unknown scope" in shown


async def test_a_fragments_only_scope_explains_itself(profile: Profile, corpus: Path):
    """The shared question bank is the easiest scope to pick by accident.

    It sorts first and its files are \\input fragments, not documents. "Nothing
    here" reads as a broken tool; the message has to say why and what to do.
    """
    bank = corpus / "sem" / "bank"
    bank.mkdir(parents=True)
    (bank / "q_one.tex").write_text("\\qns{A question}\nNo document here.\n")
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/bank")
        shown = visible(app)
        assert "No buildable assignments" in shown
        assert "begin{document}" in shown
        assert "input fragments" in shown


async def test_a_replayed_config_opens_on_what_it_saved(profile: Profile):
    """`latexally run --config run.yaml` must not clear the scope it was given."""
    app = LatexAllyApp(profile, RunConfig().with_assignments(["sem/hw/1"]))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert app.config.assignments == ("sem/hw/1",)
        assert app.screen._can_next


async def test_a_mixed_selection_is_one_plain_list(profile: Profile):
    """Two discussions and one homework, with no view to assemble it across.

    This used to need a kind filter and a selection that survived switching
    between its tabs. One list holding the whole scope makes it a selection.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await tick(pilot, "sem/dis/01A", "sem/dis/01B", "sem/hw/1")
        assert app.config.assignments == (
            "sem/dis/01A",
            "sem/dis/01B",
            "sem/hw/1",
        )
        assert "3 of 5 selected" in visible(app)


async def test_the_count_of_selected_directories_is_shown(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        assert "0 of 5 selected" in visible(app)
        await tick(pilot, "sem/hw/1", "sem/hw/2")
        assert "2 of 5 selected" in visible(app)


async def test_arrows_walk_scopes_and_rows_and_space_ticks(profile: Profile):
    """← → for scopes, ↑ ↓ for rows, space to tick, Enter for next.

    Ticking used to do nothing after a rescan: rebuilding the options leaves
    nothing highlighted, and the tick key toggles the highlighted row.

    Enter is Next now, so space is the tick -- which is the key SelectionList
    binds for it anyway.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await press(pilot, "space")
        await press(pilot, "down", "space")
        await press(pilot, "down", "space")
        assert app.config.assignments == (
            "sem/dis/01A",
            "sem/dis/01B",
            "sem/hw/1",
        )
        await press(pilot, "n")
        assert isinstance(app.screen, DocumentsScreen)


async def test_the_current_scope_stays_marked(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await advance(pilot)  # past the local-or-choose question
        await settle(pilot)  # the opening scan lands on the first named scope
        assert "sem" in highlighted(app), "the scope you are on has to be marked"


async def test_a_tick_is_a_character_not_a_shade(profile: Profile):
    """Textual draws both states as the same X and colours them differently."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/hw")
        await tick(pilot, "sem/hw/1")
        shown = visible(app)
        assert "[x] sem/hw/1" in shown
        assert "[ ] sem/hw/2" in shown


# ---------------------------------------------------------------------- #
# standards
# ---------------------------------------------------------------------- #


async def test_a_standard_can_be_toggled_off_and_on(profile: Profile):
    assert RunConfig().standards.question_tags is True
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, StandardsScreen)
        await untick(pilot, "question_tags", list_id="standards")
        assert app.config.standards.question_tags is False
        await tick(pilot, "question_tags", list_id="standards")
        assert app.config.standards.question_tags is True


async def test_the_detail_pane_explains_without_changing_anything(profile: Profile):
    """The evidence for a default belongs next to the toggle, not in a commit."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, StandardsScreen)
        choices = app.screen.query_one("#standards", SelectionList)
        choices.highlighted = 3  # Question headings
        await pilot.pause()
        assert app.config.standards.question_tags is True
        shown = visible(app)
        assert "74 of 362" in shown  # the count the old default was inferred from
        assert "0.42%" in shown      # and what it actually measured


async def test_the_screen_does_not_predict_a_cost_it_will_measure(profile: Profile):
    """No "~2.6% of pixels move" badge: that is an average of other documents.

    The build measures the real thing against the untouched originals and
    reports it per document, which is the number worth showing.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, StandardsScreen)
        shown = visible(app)
        assert "Question headings as real H2 tags" in shown
        assert "~2.6% of pixels move" not in shown
        assert "0.00–0.79% (measured)" not in shown


async def test_backing_out_of_the_standards_screen_keeps_the_change(profile: Profile):
    """Back never discards: the config is mutated as controls change."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, StandardsScreen)
        await untick(pilot, "question_tags", list_id="standards")
        await retreat(pilot)
        assert isinstance(app.screen, DocumentsScreen)
        assert app.config.standards.question_tags is False


# ---------------------------------------------------------------------- #
# colours
# ---------------------------------------------------------------------- #


async def colors(pilot, row: int) -> None:
    await scope_all_homework(pilot)
    await walk_to(pilot, ColorsScreen)
    pilot.app.screen.query_one("#colors", DataTable).move_cursor(row=row)
    await pilot.pause()


async def set_hex(pilot, value: str) -> None:
    field = pilot.app.screen.query_one("#hex", Input)
    field.focus()
    field.value = value
    await field.action_submit()
    await settle(pilot)


#: Row order follows ``ColorPolicy.originals``: solutionColor, redish, blueish.
REDISH, BLUEISH = 1, 2


async def test_the_proposal_is_the_palette_token(profile: Profile):
    """The point of the whole screen, under the default mode.

    #FF0000 fails at 4.00:1 and the palette answers it with allyRed, #CC0000 at
    5.89:1 -- the darkest red that is still recognisably red. Pure red cannot
    stay: it is 4.00:1 and AA wants 4.5:1.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "u")
        assert app.config.colors.replacements(profile)["redish"] == "#D20000"
        assert "#D20000" in visible(app)


async def test_conforming_mode_still_proposes_the_smallest_change(profile: Profile):
    """The narrower mode survives, and it still derives rather than looks up.

    A fixed palette is what once answered a course blue of #3399E6 with
    #0645AD -- 8.53:1 where 4.5:1 was asked for, and reported as harder to read
    than the colour it replaced. `conforming` is for a document whose figures
    must keep the exact hues they were drawn in; it lands on the floor and
    stops, so #FF0000 goes to #EE0000 at 4.53:1 rather than to the palette's
    #CC0000.
    """
    from latexally.run import ColorChoice

    app = LatexAllyApp(profile)
    app.config.colors = ColorChoice(mode="conforming")
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "u")
        assert app.config.colors.replacements(profile)["redish"] == "#EE0000"
        assert "#EE0000" in visible(app)


async def test_a_colour_that_already_conforms_is_still_unified(profile: Profile):
    """blueish is #B31AB3 at 5.65:1 -- it passes, and it still moves.

    This is the difference between the two modes stated as one colour. Contrast
    is not the only reason to change a colour: `blueish` passes AA, is named for
    a colour it is not (it is magenta), and is one of three unrelated purples
    and blues the corpus uses for the same purpose. `conforming` leaves it
    exactly as it is, because it only ever asked "does this pass?". `palette`
    binds it to allyPurple, because it asks "is this the same colour as the one
    beside it?".
    """
    from latexally.run import ColorChoice

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, BLUEISH)
        # Nothing pressed. The palette is applied on arrival, so the screen has
        # nothing to approve -- `u` is the undo, and there is no decision yet to
        # undo, so it is greyed out.
        assert app.config.colors.replacements(profile)["blueish"] == "#B800B8"
        assert app.screen.check_action("use", ()) is None
        assert app.screen.check_action("keep", ()) is True

    conforming = LatexAllyApp(profile)
    conforming.config.colors = ColorChoice(mode="conforming")
    async with conforming.run_test(size=SIZE) as pilot:
        await colors(pilot, BLUEISH)
        assert "blueish" not in conforming.config.colors.replacements(profile)
        assert "already meets the" in visible(conforming)
        # Offered nowhere: the footer greys u and k out on a conforming colour.
        assert conforming.screen.check_action("use", ()) is None
        assert conforming.screen.check_action("keep", ()) is None


async def test_keeping_the_original_is_allowed_and_flagged(profile: Profile):
    """Keeping a failing colour is a supported answer, never a silent one."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "k")
        # Recorded as an override to itself, so "kept on purpose" and "not yet
        # looked at" stay distinguishable.
        assert app.config.colors.overrides == {"redish": "#FF0000"}
        assert app.config.colors.replacements(profile)["redish"] == "#FF0000"
        assert "will report it" in visible(app)


async def test_an_individual_colour_can_be_overridden(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "#004400")
        assert app.config.colors.overrides == {"redish": "#004400"}


async def test_a_low_contrast_override_is_accepted_but_flagged(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "#DDDDDD")
        assert app.config.colors.overrides == {"redish": "#DDDDDD"}
        assert "below the 4.5:1 floor" in visible(app)


async def test_undo_clears_a_decision_rather_than_recording_another(profile: Profile):
    """`u` after a hand-typed hex means "never mind", and leaves no trace.

    Agreeing with what the run already does is not a decision. Recording it as
    one would put a `*` beside a row nobody argued with, and an
    `\\accessrecolor` line in the preamble that changes nothing.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "#004400")
        assert app.config.colors.overrides == {"redish": "#004400"}
        await press(pilot, "u")
        assert app.config.colors.overrides == {}
        assert app.config.colors.replacements(profile)["redish"] == "#D20000"


async def test_rejecting_is_the_only_decision_the_screen_asks_for(profile: Profile):
    """The screen's whole purpose, after the palette started applying itself.

    `k` is a rejection: the colour stays as the course wrote it, recorded as an
    override to its own original so that "left alone on purpose" and "never
    looked at" remain different things. Everything not rejected is applied,
    including for someone who skips this step entirely.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "k")
        assert app.config.colors.overrides == {"redish": "#FF0000"}
        assert app.config.colors.replacements(profile)["redish"] == "#FF0000"
        # Rejecting one does not reject the rest.
        assert app.config.colors.replacements(profile)["solutionColor"] == "#1754FF"
        # And the row now offers the undo instead of another rejection.
        assert app.screen.check_action("keep", ()) is None
        assert app.screen.check_action("use", ()) is True


async def test_conforming_mode_clears_the_override_instead(profile: Profile):
    from latexally.run import ColorChoice

    app = LatexAllyApp(profile)
    app.config.colors = ColorChoice(mode="conforming")
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "#004400")
        await press(pilot, "u")
        assert app.config.colors.overrides == {}
        assert app.config.colors.replacements(profile)["redish"] == "#EE0000"


async def test_e_opens_the_colour_under_the_cursor_for_editing(profile: Profile):
    """`e`, not Enter, rather than hunting for a field.

    Enter is Next on every other step. A screen where it silently meant "edit
    this row" instead was a screen you got stuck on: press the key that has
    moved you forward four times and the cursor drops into a text field.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "e")
        field = app.screen.query_one("#hex", Input)
        assert field.value == "#D20000"
        assert field.has_focus


async def test_enter_leaves_the_colour_screen_rather_than_editing(profile: Profile):
    """The collision this replaced: Enter has to move on here like everywhere."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        assert "e Edit hex" in visible(app)
        await press(pilot, "enter")
        await settle(pilot)
        assert isinstance(app.screen, AltScreen)


async def test_enter_in_the_hex_field_submits_it(profile: Profile):
    """And inside the field Enter is still the field's own, or a typed hex
    could never be committed."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        name = app.screen.current
        await press(pilot, "e")
        app.screen.query_one("#hex", Input).value = "#123456"
        await press(pilot, "enter")
        assert isinstance(app.screen, ColorsScreen), "must not have moved on"
        assert app.config.colors.overrides[name] == "#123456"


async def test_a_typed_hex_previews_its_ink_and_ratio(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        app.screen.query_one("#hex", Input).value = "#004400"
        await pilot.pause()
        # Shown before it is applied, so the swatch answers "what will this
        # look like" rather than "what did I just do".
        assert "#004400" in visible(app)
        assert app.config.colors.overrides == {}


async def test_the_colour_cursor_never_covers_a_swatch(profile: Profile):
    """A cursor across the row hid the cells that have to be seen, not read.

    So the highlight is confined to the colour's name, which is pure text.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        assert highlighted(app) == ["redish"]


async def test_an_unusable_hex_is_reported_not_ignored(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "zzz")
        assert app.config.colors.overrides == {}
        assert "is not a hex colour" in visible(app)


# ---------------------------------------------------------------------- #
# alt text
# ---------------------------------------------------------------------- #


async def choose_alt(pilot, name: str) -> None:
    radio = pilot.app.screen.query_one("#alt-mode", RadioSet)
    next(button for button in radio.query("RadioButton") if button.name == name).value = True
    await pilot.pause()


async def test_the_alt_template_marks_figures_without_gating_the_build(
    profile: Profile,
):
    """The refusal is gone, and with it the warning that announced it.

    An unfilled marker used to be a hard LaTeX error, on the argument that a
    placeholder reaching a PDF is a silent false claim of conformance. It is
    still reported by `check` and still named in the build log, so it is not
    silent -- and a build that refuses is a build nobody can look at.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "on")
        assert app.config.alt.mode == "placeholders"
        assert app.config.alt.injects is True
        assert app.config.alt.strict is False
        shown = visible(app)
        assert "FAILS TO BUILD" not in shown
        assert "NOT conformant" not in shown
        # The explanation is in the box, and it says what actually happens.
        assert "TODO:figure-id" in shown


async def test_captions_are_the_option_above_the_marker(profile: Profile):
    """A marker on the page beats a marker only a screen reader would hear."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "caption")
        assert app.config.alt.mode == "caption"
        assert app.config.alt.captions is True
        # Caption-only. It used to be the marker tier PLUS a caption, which
        # meant asking for captions also produced a worklog, <<TODO>> in /Alt
        # and a report of every undescribed figure -- none of it asked for.
        # Alt text is reached with `latexally scan` and `latexally check`.
        assert app.config.alt.injects is False
        assert app.config.alt.scans is False
        assert app.config.alt.touches_sources is True
        shown = visible(app)
        assert "caption" in shown
        # The one thing a caption cannot do, said where the choice is made.
        assert "figure or table" in shown


async def test_descriptions_can_be_switched_off(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "off")
        assert app.config.alt.mode == "off"
        assert app.config.alt.scans is False
        assert app.config.alt.captions is False


async def test_every_step_keeps_its_description_in_the_same_place(
    profile: Profile,
):
    """Docked bottom, so it does not move as the body above it changes height.

    A note that lands on a different row on each screen is one the eye has to
    find again each time.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "on")
        box = app.screen.query_one("#detail-box", Static)
        assert box.display
        # Titled with the option it explains, and the title is the bold part.
        assert box.border_title == "on"
        assert box.styles.border_title_style.bold
        # Bottom aligned, and whole: the box used to be docked, which put its
        # last row -- the bottom border -- underneath the Footer.
        rendered = [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]
        rendered = [line for line in rendered if line.strip()]
        assert rendered[-2].lstrip().startswith("\u2570"), rendered[-3:]


# ---------------------------------------------------------------------- #
# output
# ---------------------------------------------------------------------- #


async def output(pilot) -> None:
    await scope_all_homework(pilot)
    await walk_to(pilot, OutputScreen)


async def type_into(pilot, selector: str, value: str) -> None:
    pilot.app.screen.query_one(selector, Input).value = value
    await pilot.pause()


async def test_output_root_can_be_set(profile: Profile, tmp_path: Path):
    target = tmp_path / "elsewhere"
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#root", str(target))
        assert app.config.output.root == target
        # Everything hangs off the root unless separately overridden.
        assert app.config.output.pdf_dir() == target / "pdf"
        assert app.config.output.worklog_dir() == target / "descriptions"


async def test_each_artifact_path_can_be_moved_independently(
    profile: Profile, tmp_path: Path
):
    """Staff asked for the alt-text log somewhere they share; PDFs elsewhere."""
    shared = tmp_path / "shared" / "alt"
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#path-descriptions", str(shared))
        assert app.config.output.worklog_dir() == shared
        assert app.config.output.pdf_dir() == _out(profile) / "pdf"


async def test_a_relative_artifact_path_hangs_off_the_root(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#path-pdf", "final")
        assert app.config.output.pdf_dir() == _out(profile) / "final"
        # Stored as typed, so run.yaml stays portable between machines.
        assert app.config.output.as_dict()["paths"]["pdf"] == "final"


async def test_an_artifact_path_can_be_restored_to_its_default(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#path-pdf", "final")
        await type_into(pilot, "#path-pdf", "")
        assert app.config.output.paths == {}
        assert app.config.output.pdf_dir() == _out(profile) / "pdf"


async def test_a_path_field_survives_an_absolute_default(profile: Profile, tmp_path: Path):
    """Regression: `[/Users/...]` in a prompt was parsed as Rich markup.

    Absolute paths appear in every path field, and an unescaped one crashed the
    runner with MarkupError before it could ask the question.
    """
    absolute = tmp_path / "somewhere"
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#root", str(absolute))
        assert app.config.output.root == absolute
        # The screen survived the absolute path -- which is the regression --
        # and shows each artifact as the part that differs from the root it
        # hangs off, not with the root repeated on all six rows.
        assert app.screen.query_one("#path-pdf", Input).placeholder == "pdf"


async def test_write_mode_can_be_set(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        radio = app.screen.query_one("#write-mode", RadioSet)
        next(b for b in radio.query("RadioButton") if b.name == "in-place").value = True
        await pilot.pause()
        assert app.config.output.in_place is True
        # The cost of the choice is stated where the choice is made, not after.
        assert "clean git worktree" in visible(app)


async def test_backing_out_keeps_the_current_output_settings(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await retreat(pilot)
        assert app.config.output.root == _out(profile)
        assert app.config.output.write_mode == "in-place"


async def test_the_worklog_path_is_named_so_staff_can_find_it(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        shown = visible(app)
        assert "descriptions" in shown
        assert "Alt-text log" in shown


# ---------------------------------------------------------------------- #
# documents (variants)
# ---------------------------------------------------------------------- #


async def test_every_version_is_built_by_default(profile: Profile):
    """The blank handout is what students receive; it must not be optional."""
    assert RunConfig().variants == ()
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        assert "every version each assignment has" in visible(app)


async def test_answers_only_is_not_built_unless_asked_for(profile: Profile):
    """It is an answers-only extract, produced for staff marking rather than
    handed out, and converting it doubles what a discussion costs in alt text
    for a document nobody reads with a screen reader."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        shown = visible(app)
        assert "[x] solution" in shown
        assert "[x] problem" in shown
        assert "[ ] answer" in shown, "answers-only starts unticked"


async def test_a_version_can_be_deselected(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        await untick(pilot, "solution", list_id="variants")
        assert app.config.variants == ("problem",)


async def test_returning_to_the_default_set_is_stored_as_no_filter(profile: Profile):
    """Off then on again must return to the default, not freeze a list."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        await untick(pilot, "solution", list_id="variants")
        await tick(pilot, "solution", list_id="variants")
        assert app.config.variants == ()


async def test_the_last_version_cannot_be_removed(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        await press(pilot, "c")
        assert app.config.variants == (), "the runner must never build nothing"
        assert not app.screen._can_next
        assert "would build nothing" in visible(app)
        # And it stays put rather than silently backing out.
        await advance(pilot)
        assert isinstance(app.screen, DocumentsScreen)


async def test_the_screen_counts_how_many_assignments_have_each_version(
    profile: Profile,
):
    config = RunConfig().with_assignments(["sem/hw/1", "sem/hw/2"])
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, DocumentsScreen)
        shown = visible(app)
        assert "blank, as students receive it" in shown
        assert "2 in scope" in shown


# ---------------------------------------------------------------------- #
# review and running
# ---------------------------------------------------------------------- #


async def test_the_review_lists_every_document_that_will_be_built(profile: Profile):
    config = RunConfig().with_assignments(["sem/hw/1"])
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        assert "sem-hw-1-solution-accessible.pdf" in visible(app)


async def test_the_review_names_the_preamble_it_will_inject(profile: Profile):
    """It is the last section, and reachable by scrolling to it.

    Thirty lines of LaTeX above the document table pushed the one thing people
    actually check -- which documents get built -- off the bottom of the
    screen. It is still shown in full; it is just no longer shown first.
    """
    config = RunConfig().with_assignments(["sem/hw/1"])
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        # What matters at a glance is above the fold.
        assert "sem-hw-1-solution-accessible.pdf" in visible(app)

        app.screen.query_one("#body").scroll_end(animate=False)
        await pilot.pause()
        assert "DocumentMetadata" in visible(app)


async def test_quitting_never_sets_write(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await pilot.press("q")
        await pilot.pause()
    assert app.should_run is False
    assert app.config.write is False


async def test_backing_out_of_the_review_leaves_it_a_dry_run(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, ReviewScreen)
        await retreat(pilot)
        assert app.should_run is False
        assert app.config.write is False


async def test_the_review_states_whether_anything_will_be_written(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, ReviewScreen)
        # in-place is the default now: the PDF goes beside the document, and
        # the review has to say so rather than promise an untouched corpus.
        shown = visible(app)
        assert "beside the document it came from" in shown
        assert app.config.output.edits_sources is False


async def test_the_review_names_build_as_the_key_that_writes(profile: Profile):
    """Nothing writes until a key that says "build" is pressed."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, ReviewScreen)
        shown = visible(app)
        assert "Build" in shown, "the footer has to name the key that writes"
        assert "\\ Back" in shown


async def test_in_place_review_names_the_corpus_not_a_directory(profile: Profile):
    """The prompt must say what is about to be edited, in plain words."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        radio = app.screen.query_one("#write-mode", RadioSet)
        next(b for b in radio.query("RadioButton") if b.name == "in-place").value = True
        await pilot.pause()
        await walk_to(pilot, ReviewScreen)
        shown = visible(app)
        assert "beside the document" in shown
        # It must not imply the sources are edited -- they are not, any more.
        assert "EDIT YOUR COURSE SOURCES" not in shown


async def test_running_with_nothing_selected_is_refused(profile: Profile):
    """A disabled Next with the reason beside it, not a silent exit."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        assert not app.screen._can_next
        assert "Tick at least one directory" in visible(app)
        await advance(pilot)
        assert isinstance(app.screen, ScopeScreen), "it must not leave the screen"
        assert app.should_run is False


async def test_back_from_the_first_step_exits(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await retreat(pilot)
        await pilot.pause()
    assert app.should_run is False


async def test_a_saved_config_replays_identically(profile: Profile, tmp_path: Path):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, StandardsScreen)
        await untick(pilot, "question_tags", list_id="standards")
        config = app.config
    path = tmp_path / "run.yaml"
    path.write_text(config.to_yaml())
    assert RunConfig.load(path).as_dict() == config.as_dict()


async def test_the_app_can_save_its_config(profile: Profile, tmp_path: Path):
    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        path = app.action_save()
    assert path.is_file()
    assert RunConfig.load(path).assignments == ("sem/hw/1",)


# ---------------------------------------------------------------------- #
# the two failures that prompted the rewrite
# ---------------------------------------------------------------------- #


@pytest.fixture
def big_corpus(tmp_path: Path) -> Path:
    """Forty assignments: more than any terminal shows at once."""
    for index in range(1, 41):
        directory = tmp_path / "sem" / "hw" / f"{index:02d}"
        directory.mkdir(parents=True)
        (directory / f"sol{index}.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\\end{document}\n"
        )
    return tmp_path


@pytest.fixture
def big_profile(big_corpus: Path) -> Profile:
    return Profile(
        name="test",
        corpus=CorpusScope(
            root=big_corpus, include=("**/*.tex",), kinds={"hw": "homework"}
        ),
    )


async def test_a_scope_taller_than_the_terminal_scrolls_to_its_last_row(
    big_profile: Profile,
):
    """The failure that prompted all of this.

    Rich's ``Live`` clipped at the terminal height with no way to scroll, so
    with a scope this size the rows past the fold could not be reached at all.
    """
    app = LatexAllyApp(big_profile)
    async with app.run_test(size=(80, 12)) as pilot:
        await set_scope(pilot, "sem/hw")
        choices = app.screen.query_one("#assignments", SelectionList)
        assert choices.option_count == 40
        choices.focus()
        await pilot.press("end")
        await pilot.pause()
        assert "sem/hw/40" in visible(app), "the last row has to be reachable"


async def test_the_footer_survives_a_scope_taller_than_the_terminal(
    big_profile: Profile,
):
    """The footer said how to tick a box, and was the first thing clipped off."""
    app = LatexAllyApp(big_profile)
    async with app.run_test(size=(80, 12)) as pilot:
        await set_scope(pilot, "sem/hw")
        choices = app.screen.query_one("#assignments", SelectionList)
        choices.focus()
        await pilot.press("end")
        await open_scope(pilot)
        shown = visible(app)
        assert "a All" in shown, "the footer has to keep naming the keys"
        assert "Next" in shown, "the key that moves on has to stay named"


async def test_select_all_is_a_visible_control_and_a_binding(big_profile: Profile):
    """Not a single undocumented key at the bottom of a screen that clips."""
    app = LatexAllyApp(big_profile)
    async with app.run_test(size=(80, 12)) as pilot:
        await set_scope(pilot, "sem/hw")
        assert "a All" in visible(app)
        await pilot.press("a")
        await pilot.pause()
        assert len(app.config.assignments) == 40
        await pilot.press("c")
        await pilot.pause()
        assert app.config.assignments == ()


# ---------------------------------------------------------------------- #
# the build screen
# ---------------------------------------------------------------------- #


async def test_the_build_screen_reports_every_document_and_its_failures(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """``build_run``'s own hooks drive the table; the engine stays terminal-blind.

    ``build_run`` is replaced here because the assertion is about what the
    screen does with a report, not about whether pdflatex is installed.
    """
    import latexally.build as build

    from latexally.build import BuildReport
    from latexally.discover import iter_selected

    def fake_build_run(config, prof, *, on_start=None, on_finish=None):
        reports = []
        for assignment in iter_selected(prof, config):
            if on_start:
                on_start(assignment, "solution")
            failed = assignment.path == "sem/hw/2"
            report = BuildReport(
                assignment=assignment.path,
                variant="solution",
                ok=not failed,
                pdf=None if failed else tmp_path / f"{assignment.name}.pdf",
                pages=3,
                bookmarks=7,
                figures=2,
                pixel_diff=0.015,
                errors=["! Undefined control sequence."],
                note="tagging refused this construct",
            )
            reports.append(report)
            if on_finish:
                on_finish(report)
        return reports

    monkeypatch.setattr(build, "build_run", fake_build_run)
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    config = RunConfig().with_assignments(["sem/hw/1", "sem/hw/2"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        # `b`, because reaching this screen no longer starts anything.
        await pilot.press("b")
        for _ in range(20):
            await pilot.pause()
            if app.reports:
                break
        assert app.should_run is True
        assert app.config.write is True
        assert [report.assignment for report in app.reports] == [
            "sem/hw/1",
            "sem/hw/2",
        ]
        shown = visible(app)
        assert "1 of 2 built clean" in shown
        assert "1 produced nothing" in shown
        assert "failed — no PDF" in shown
        assert "tagging refused this construct" in shown
        # The measured cost, per document -- what the standards screen no
        # longer predicts.
        assert "1.50%" in shown
        # A DataTable sizes a column to whatever was in it when the rows were
        # added -- "queued" -- and then crops every longer state to six.
        assert "FAILED" in shown


async def test_enter_is_the_way_out_once_the_build_is_over(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """One key, said in words. `q Quit` is the App's and shows on every screen;
    a screen-level `q` for the same word drew it twice and neither said the run
    had finished."""
    import latexally.build as build

    monkeypatch.setattr(
        build, "build_run", lambda config, prof, *, on_start=None, on_finish=None: []
    )
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        screen = app.screen
        # Nothing has run: Enter is greyed, not live.
        assert screen.check_action("finish", ()) is None
        await pilot.press("b")
        for _ in range(20):
            await pilot.pause()
            if screen._build_done:
                break
        assert screen.check_action("finish", ()) is True
        assert "press Enter to exit" in visible(app)
        await pilot.press("enter")
        await pilot.pause()
        assert app._exit is True


async def test_reaching_the_build_screen_builds_nothing(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """Arriving is not consent.

    Enter from Review used to compile immediately, and compiling is the one
    thing in the runner that writes PDFs, takes minutes, and cannot be undone
    with Escape. The queue is what you came here to read -- which documents,
    which variants -- and it is worth being able to read it before anything
    runs.
    """
    import latexally.build as build

    called: list[str] = []

    def fake_build_run(config, prof, *, on_start=None, on_finish=None):
        called.append("built")
        return []

    monkeypatch.setattr(build, "build_run", fake_build_run)
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        for _ in range(10):
            await pilot.pause()
        assert called == [], "the build started without being asked"
        shown = visible(app)
        assert "Press b to build" in shown
        assert "Nothing has been written yet" in shown

        await pilot.press("b")
        for _ in range(20):
            await pilot.pause()
            if called:
                break
        assert called == ["built"]


async def test_edit_mode_builds_on_arrival(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """Edit mode was already consented to twice -- on Output, and on Review."""
    import latexally.build as build

    called: list[str] = []
    monkeypatch.setattr(
        build,
        "build_run",
        lambda config, prof, *, on_start=None, on_finish=None: called.append("built")
        or [],
    )
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    config.output.write_mode = "edit"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        for _ in range(20):
            await pilot.pause()
            if called:
                break
        assert called == ["built"]
        assert "Press b to build" not in visible(app)


async def test_a_second_b_does_not_start_a_second_build(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """The key stays pressable, and the worker is `exclusive`, but relying on
    that to swallow a double-press is relying on a scheduling detail."""
    import latexally.build as build

    called: list[str] = []
    monkeypatch.setattr(
        build,
        "build_run",
        lambda config, prof, **_: called.append("built") or [],
    )
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        await pilot.press("b")
        await pilot.press("b")
        for _ in range(20):
            await pilot.pause()
        assert called == ["built"]


async def test_back_on_the_first_step_does_not_quit(profile: Profile):
    """Back used to fall through to app.exit(), so Back here killed the app."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("backslash")
        await pilot.pause()
        assert app.is_running
        assert isinstance(app.screen, ModeScreen)
        assert app.screen.first


async def test_the_arrows_belong_to_the_caret_while_typing_a_path(
    profile: Profile,
):
    """In the path field ← → are the text caret, which outranks the scope row."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        assert app.screen.check_action("scope", (-1,)) is True
        app.screen.query_one("#scope-path", Input).focus()
        await pilot.pause()
        assert app.screen.check_action("scope", (-1,)) is False


async def test_the_arrows_reach_the_screen_past_the_lists_own_bindings(
    two_scopes: Profile,
):
    """A SelectionList is a ScrollView and binds left/right to scrolling.

    Those bindings swallowed the scope keys before the screen ever saw them.
    """
    app = LatexAllyApp(two_scopes)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        assert app.focused is app.screen.query_one("#assignments")
        await press(pilot, "right")
        assert "homeworks" in highlighted(app)
        await press(pilot, "left")
        assert "sem" in highlighted(app)


async def test_saving_writes_a_replayable_run_yaml(profile: Profile, tmp_path: Path):
    """What `s` does, and the only way to keep a selection without building."""
    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await pilot.pause()
    saved = tmp_path / "out" / "run.yaml"
    assert saved.is_file()
    assert RunConfig.load(saved).assignments == ("sem/hw/1",)


async def test_rescanning_leaves_the_screen_usable(profile: Profile):
    """The crash that once made the arrows look dead.

    A row of mounted widgets went stale across a rescan -- ``remove_children()``
    is deferred -- and reading it raised out of every later keypress. The filter
    rows are rendered text now, so there is nothing left to go stale.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await press(pilot, "space")
        assert app.config.assignments == ("sem/dis/01A",)
        await set_scope(pilot, "sem/hw")
        await press(pilot, "down", "space")
        assert app.is_running
        assert app.config.assignments == ("sem/dis/01A", "sem/hw/2")


async def test_ticks_survive_a_change_of_scope(profile: Profile):
    """A scope is a place to look, not a new question.

    Two discussions and one homework is a legitimate run even when they are
    reached through different scopes, so changing scope keeps what is ticked.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem/hw")
        await tick(pilot, "sem/hw/1")
        await set_scope(pilot, "sem/dis")
        await tick(pilot, "sem/dis/01A")
        assert app.config.assignments == ("sem/dis/01A", "sem/hw/1")
        # The tick you cannot see is named, not just counted: it is still
        # going to be built.
        shown = visible(app)
        assert "1 of 2 selected, 1 in another scope" in shown
        assert "also ticked: sem/hw/1" in shown
        # ...and it is still ticked when you go back to where it came from.
        await set_scope(pilot, "sem/hw")
        assert set(app.screen.query_one("#assignments", SelectionList).selected) == {
            "sem/hw/1"
        }


# ---------------------------------------------------------------------- #
# keyboard only
# ---------------------------------------------------------------------- #


async def test_it_opens_on_a_scope_with_the_list_already_populated(
    profile: Profile,
):
    """No keystroke should be needed before the arrows do something."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        choices = app.screen.query_one("#assignments", SelectionList)
        assert choices.option_count == 5, "the opening scan has to have landed"
        assert choices.highlighted == 0
        assert app.focused is choices, "arrows must work without focusing first"
        assert "sem" in highlighted(app)
        await press(pilot, "down", "space")
        assert app.config.assignments == ("sem/dis/01B",)


async def test_the_opening_scope_follows_the_profile_not_the_alphabet(
    profile: Profile, corpus: Path
):
    """Sorting put the fragments-only question bank first, so 'the first one'
    opened on an error. The profile already declares a usable order."""
    bank = corpus / "sem" / "bank"
    bank.mkdir(parents=True)
    (bank / "q_one.tex").write_text("\\qns{A question}\nNo document here.\n")
    ordered = replace(
        profile,
        corpus=replace(
            profile.corpus,
            named={"sem": ("sem/**/*.tex",), "bank": ("sem/bank/**/*.tex",)},
        ),
    )
    app = LatexAllyApp(ordered)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        assert "sem" in highlighted(app)
        assert "No buildable assignments" not in visible(app)
        # …and the alphabetically-first scope is still one keystroke away.
        await press(pilot, "right")
        assert "bank" in highlighted(app)


async def test_a_replayed_config_is_not_wiped_by_the_opening_scan(
    profile: Profile,
):
    """Scanning clears the selection, so a saved one has to skip the scan."""
    app = LatexAllyApp(profile, RunConfig().with_assignments(["sem/hw/1"]))
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        assert app.config.assignments == ("sem/hw/1",)
        assert app.screen._can_next
        # And the keys still act, rather than typing into the path field.
        assert not isinstance(app.focused, Input)
        await press(pilot, "n")
        assert isinstance(app.screen, DocumentsScreen)


async def test_left_and_right_walk_the_named_scopes(two_scopes: Profile):
    app = LatexAllyApp(two_scopes)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        await press(pilot, "space")
        assert app.config.assignments == ("sem/dis/01A",)
        await press(pilot, "right")
        assert app.screen.query_one("#scope-path", Input).value == "homeworks"
        assert app.config.assignments == ("sem/dis/01A",), "the tick has to stand"


async def test_nothing_in_the_app_is_a_button(profile: Profile):
    """The standing guard against a mouse-only control coming back.

    The runner runs with `mouse=False`, so a Button would be reachable only by
    tabbing to it — which is a worse key hint than the footer already gives.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        # `a` is a scope-picker key. Pressed on the first screen it does
        # nothing, and the walk below then spins forever against a Next that
        # stays disabled because nothing is ticked.
        await walk_to(pilot, ScopeScreen)
        await press(pilot, "a")
        for screen in (
            ScopeScreen,
            DocumentsScreen,
            StandardsScreen,
            ColorsScreen,
            AltScreen,
            OutputScreen,
            ReviewScreen,
        ):
            await walk_to(pilot, screen)
            assert not app.screen.query(Button), f"{screen.__name__} grew a button"


async def test_arrows_choose_between_options_rather_than_shopping(
    profile: Profile,
):
    """↑ ↓ move the value, not a second cursor over it.

    Textual's RadioSet leaves Enter to commit, so `(•)` marks the value and
    something else marks where you are — two cursors for one decision.
    """
    app = LatexAllyApp(profile, RunConfig().with_assignments(["sem/hw/1"]))
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, AltScreen)
        assert app.focused is app.screen.query_one("#alt-mode")
        # Captions are the default, so the screen opens on the first row.
        assert app.config.alt.mode == "caption"
        # Three options: caption, on, off. Each arrow moves the value by one.
        await press(pilot, "down")
        assert app.config.alt.mode == "placeholders"
        await press(pilot, "down")
        assert app.config.alt.mode == "off"
        await press(pilot, "up")
        assert app.config.alt.mode == "placeholders"


async def test_the_write_mode_is_where_the_output_cursor_starts(
    profile: Profile,
):
    """Focus used to land on the scrolling container, so ↑ ↓ scrolled."""
    app = LatexAllyApp(profile, RunConfig().with_assignments(["sem/hw/1"]))
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, OutputScreen)
        assert app.focused is app.screen.query_one("#write-mode")
        assert app.config.output.write_mode == "in-place"
        await press(pilot, "down")
        assert app.config.output.in_place is True
        await press(pilot, "up")
        assert app.config.output.write_mode == "in-place"


async def test_scanning_clears_the_list_and_says_so(profile: Profile, monkeypatch):
    """The previous scope's directories must not sit there during a scan.

    A scan is seconds on a real corpus, and leaving the old list up invites
    ticking a directory that is about to disappear.
    """
    import threading

    import latexally.tui.app as appmod

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        await tick(pilot, "sem/dis/01A")

        # Hold the worker open; this corpus scans faster than one frame.
        release = threading.Event()
        real = appmod.discover_assignments
        monkeypatch.setattr(
            appmod,
            "discover_assignments",
            lambda prof, scope: (release.wait(5), real(prof, scope))[1],
        )
        screen = app.screen
        screen.scan("sem/hw")
        await pilot.pause()

        assert screen.query_one("#assignments", SelectionList).option_count == 0
        assert screen.query_one("#scanning").display
        shown = visible(app)
        assert "scanning sem/hw…" in shown
        assert "sem/dis/01A   (" not in shown, "the old list has to be gone"
        # ...and the keys keep working. `widget.loading` broke that by taking
        # focus off the list and handing it to the path field, where every
        # letter is text -- the footer emptied for the length of the scan.
        assert app.focused is screen.query_one("#assignments")
        assert "q Quit" in shown

        release.set()
        await settle(pilot)
        assert not screen.query_one("#scanning").display
        assert screen.query_one("#assignments", SelectionList).option_count == 3
        # The tick from the other scope stands.
        assert app.config.assignments == ("sem/dis/01A",)


# ---------------------------------------------------------------------- #
# revert
# ---------------------------------------------------------------------- #


async def test_r_opens_revert_from_any_step(profile: Profile):
    """`r` is app-level on purpose: undoing is not a step in converting.

    Reachable from the first screen without walking the wizard, and pushed
    rather than switched to, so escape returns to exactly where it was called.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        opened_from = type(app.screen)
        await press(pilot, "r")
        assert isinstance(app.screen, RevertScreen)
        await press(pilot, "backslash")
        assert isinstance(app.screen, opened_from)


async def test_revert_says_what_it_would_do_before_doing_it(profile: Profile):
    """The screen opens on the plan. Nothing is written until `y`."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        shown = visible(app)
        assert "Revert" in shown
        # A profile whose corpus is a bare tmp_path is not a git repository, so
        # the screen must say that rather than offer a key that cannot work.
        assert "git" in shown.lower()


async def test_the_revert_heading_names_the_key(profile: Profile, monkeypatch):
    """`y Yes, revert` in the footer is one line of small print among five, and
    this is the one screen where a keypress rewrites course material."""
    import latexally.revert as revert_mod

    class Plan:
        empty = False
        root = Path("/corpus")
        restore = [Path("/corpus/a.tex")]
        remove: list = []
        outputs: list = []

    monkeypatch.setattr(revert_mod, "plan_revert", lambda config, prof: Plan())
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        for _ in range(20):
            await pilot.pause()
            if "Confirm revert (y)" in visible(app):
                break
        assert "Confirm revert (y)" in visible(app)


async def test_revert_cannot_be_confirmed_without_a_plan(profile: Profile):
    """`y` is greyed, not missing: the footer still answers "why not".

    A key listed but inert sends someone looking for a result they were
    promised; a key that has vanished gives them no way to ask what happened
    to it.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        assert app.screen.check_action("confirm", ()) is None


async def test_enter_on_revert_refuses_out_loud(profile: Profile):
    """Enter is Next on every other screen, so it arrives here out of habit.

    It was unbound, which means a keypress with no reaction whatsoever --
    indistinguishable from the app having hung, and the thing behind the key
    rewrites course material. It now says why it did nothing.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        await press(pilot, "enter")
        shown = visible(app)
        assert "Enter does not revert" in shown or "Nothing to revert" in shown
        # And it is still the revert screen: Enter must not advance anything.
        assert isinstance(app.screen, RevertScreen)


async def test_the_footer_does_not_offer_two_keys_called_revert(profile: Profile):
    """`r` opens this screen and `y` performs it, and both were labelled Revert.

    Worse than a cosmetic clash: `action_revert` returns early when the screen
    is already showing, so the `r` being advertised did nothing at all, right
    beside the `y` that does everything.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        # The App owns the binding, so the App is who decides. False, not None:
        # hidden outright rather than shown greyed out.
        assert app.check_action("revert", ()) is False
        shown = visible(app)
        assert "Yes, revert" in shown
        # `r Revert` is gone from the footer; the screen heading still says
        # "Revert", which is what the screen is called and is not a key.
        assert "r Revert" not in shown, shown


async def test_a_revert_in_flight_blocks_a_second_one(profile: Profile, monkeypatch):
    """`y` twice must not start two git checkouts over the same tree."""
    import latexally.revert as revert_mod

    calls: list[int] = []
    monkeypatch.setattr(revert_mod, "do_revert", lambda plan: calls.append(1))

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "r")
        screen = app.screen
        # Force a non-empty plan; the fixture corpus is not a git repo.
        screen._plan = SimpleNamespace(empty=False, restore=[], remove=[], outputs=[])
        screen._reverting = True
        assert screen.check_action("confirm", ()) is None
        screen.action_confirm()
        assert calls == []


# ---------------------------------------------------------------------- #
# starting where you are standing
# ---------------------------------------------------------------------- #


async def test_the_runner_opens_on_the_folder_it_was_started_from(
    profile: Profile, corpus: Path, monkeypatch
):
    """The first question, and for most runs the only one.

    `latexally scan` already means "this folder"; the runner meaning "the whole
    corpus, nothing ticked, off you go" made the two disagree about the same
    words.
    """
    monkeypatch.chdir(corpus / "sem" / "hw" / "1")
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.here_scope == "sem/hw/1"
        assert app.scope_mode == "local"
        assert "local — sem/hw/1" in visible(app)
        # Nothing is listed yet: the question comes before the list, and the
        # arrows answer it because it holds the focus.
        assert isinstance(app.focused, Choice)

        await advance(pilot)
        await settle(pilot)
        # Everything the folder holds, ticked -- shown rather than implied, so
        # any of it can still be unticked.
        assert app.config.assignments == ("sem/hw/1",)
        assert "[x] sem/hw/1" in visible(app)


async def test_choosing_instead_reveals_the_browsing_controls(
    profile: Profile, corpus: Path, monkeypatch
):
    """The scope row and the path field are for browsing, so they appear only
    when the previous step said browsing is what was wanted."""
    monkeypatch.chdir(corpus / "sem" / "hw" / "1")
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "down")  # the arrows answer it
        assert app.scope_mode == "choose"

        await advance(pilot)
        await settle(pilot)
        assert app.screen.query_one("#scope-path-line").display
        assert app.screen.query_one("#scope-line").display


async def test_local_is_not_offered_outside_the_corpus(
    profile: Profile, monkeypatch
):
    """Started from somewhere else -- this repository, or CI -- there is no
    "local" to offer, so the option says which and disables itself.

    The tests directory, not a tmp_path child: the corpus fixture's root IS
    tmp_path, so anything under it is inside the corpus by definition.
    """
    monkeypatch.chdir(Path(__file__).resolve().parent)
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.here_scope is None
        assert app.scope_mode == "choose"
        assert "not inside the corpus" in visible(app)


async def test_enter_moves_on_and_space_ticks(profile: Profile, corpus: Path, monkeypatch):
    """Enter is the key people press to go on.

    It is a priority binding, because the focused OptionList binds Enter to
    "tick this row" and would otherwise swallow it. Ticking is `space`, which
    SelectionList binds for that anyway.
    """
    monkeypatch.chdir(corpus / "sem" / "hw" / "1")
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "enter")
        await settle(pilot)
        assert isinstance(app.screen, ScopeScreen)

        await press(pilot, "space")
        assert app.config.assignments == (), "space must untick the ticked row"
        await press(pilot, "space")
        assert app.config.assignments == ("sem/hw/1",)

        await press(pilot, "enter")
        await settle(pilot)
        assert isinstance(app.screen, DocumentsScreen)


async def test_a_local_folder_with_nothing_buildable_is_not_a_dead_end(
    profile: Profile, corpus: Path, monkeypatch
):
    r"""Reported as "TUI stuck and not responding on 2/8".

    Starting in a folder of `\input` fragments -- the shared question bank,
    where most figures live -- the scan finds no document. The list is then
    empty, Next is disabled, `a` and `c` have nothing to act on, and in local
    mode the scope row and the path field are hidden. Nothing on screen could
    widen the scope and nothing said `esc` was the way out, so it read exactly
    like a hung program.

    An empty local scan now hands the browsing controls back and says so.
    """
    fragments = corpus / "sem" / "bank"
    fragments.mkdir(parents=True)
    (fragments / "q_only.tex").write_text("\\begin{tikzpicture}\\end{tikzpicture}\n")
    monkeypatch.chdir(fragments)

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.scope_mode == "local"
        await advance(pilot)
        await settle(pilot)

        assert app.screen.query_one("#assignments", SelectionList).option_count == 0
        assert not app.screen._can_next
        # Both ways out are on screen, and named.
        assert app.screen.query_one("#scope-path-line").display
        assert "esc to go back" in visible(app)
        # And the key that walks the scopes comes back with the row.
        assert app.screen.check_action("scope", ()) is True


async def test_the_reason_next_is_disabled_says_which_situation_it_is(
    profile: Profile,
):
    """"Tick at least one" is useless advice when there is nothing to tick."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await open_scope(pilot)
        await set_scope(pilot, "sem/hw")
        assert "Tick at least one directory" in visible(app)

        await set_scope(pilot, "nowhere-at-all")
        assert "Nothing here to convert" in visible(app)


async def test_review_separates_its_sections(profile: Profile):
    """Six unrelated answers in one column need more than bold text.

    The preamble is thirty lines of LaTeX and the document table has a header
    row of its own, so "Colours" read as one more line of whatever was above
    it. Every section carries a rule now.
    """
    from textual.widgets import Rule

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, ReviewScreen)
        # Order matters: the preamble is long and rarely read, so it goes last
        # rather than pushing the document table below the fold.
        titles = [
            "Selected directories",
            "What gets built",
            "Figure descriptions",
            "Colours",
            "Output",
            "Injected into each driver",
        ]
        headings = [
            widget.render().plain for widget in app.screen.query(".section")
        ]
        assert headings == titles
        assert len(app.screen.query(Rule)) == len(titles)


async def test_review_shows_the_placeholder_markup_that_will_be_written(
    profile: Profile,
):
    r"""The last screen before a marker lands in somebody's course file.

    The alt-text step warns that placeholders are written; it does not show
    what they look like. A `<<TODO:...>>` appearing in a .tex should not be the
    first time anyone sees one.
    """
    from latexally.apply import PLACEHOLDER

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        app.config.alt = replace(app.config.alt, mode="placeholders")
        await walk_to(pilot, ReviewScreen)

        markup = app.screen.query_one("#alt-markup").render().plain
        assert PLACEHOLDER.format(id="fig-1a2b3c4d") in markup
        assert "\\begin{Described}" in markup
        assert "\\described" in markup, "the inline form is written too"
        assert "reported by `latexally check`" in markup, (
            "a marker no longer fails the build, so the review has to say what "
            "does happen to it instead"
        )


async def test_review_says_nothing_about_markup_when_none_is_written(
    profile: Profile,
):
    """`worklog` mode edits no .tex, so there is no markup to warn about and a
    warning would be a lie about what the run does."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        app.config.alt = replace(app.config.alt, mode="worklog")
        await walk_to(pilot, ReviewScreen)

        assert app.screen.query_one("#alt-markup").render().plain == ""
        assert "your .tex files are not edited" in visible(app)


async def test_off_still_means_no_figures_at_all(profile: Profile):
    """Three options now, and the third must not have drifted."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        radio = app.screen.query_one("#alt-mode", RadioSet)
        next(b for b in radio.query("RadioButton") if b.name == "off").value = True
        await pilot.pause()
        assert app.config.alt.mode == "off"
        assert app.config.alt.scans is False


# ---------------------------------------------------------------------- #
# which course
# ---------------------------------------------------------------------- #
#
# `latexally run` used to inherit the refusal every other command makes when
# two courses are installed and neither was named. Right for `build --write`,
# where guessing converts the wrong corpus in silence; wrong for the runner,
# whose whole premise is asking. These pin that it asks, that it asks only
# when there is something to ask, and that switching re-derives what was read
# off the profile it replaced.


@pytest.fixture
def installed(tmp_path: Path, corpus: Path, monkeypatch) -> Path:
    """A profiles directory with two courses, one of them declaring itself."""
    from latexally import config as config_module

    directory = tmp_path / "installed"
    directory.mkdir()
    (directory / "current.yaml").write_text(
        "name: current\n"
        "default: true\n"
        "course:\n"
        '  number: "EE 66"\n'
        '  name: "Signals, Dynamics, and Information"\n'
        "corpus:\n"
        f'  root: "{corpus}"\n'
        '  include: ["**/*.tex"]\n'
    )
    (directory / "archive.yaml").write_text(
        "name: archive\n"
        "course:\n"
        '  number: "EECS 16A"\n'
        '  name: "Designing Information Devices and Systems I"\n'
        "corpus:\n"
        f'  root: "{corpus}"\n'
        '  include: ["**/*.tex"]\n'
    )
    monkeypatch.setattr(config_module, "builtin_profile_dir", lambda: directory)
    return directory


def test_the_default_course_is_declared_in_the_profile_not_in_the_code(installed):
    """Which course is current is course data, like every other field.

    A name compiled into the tool would have to be edited to onboard a course,
    or to hand this corpus to next term's staff -- who would then be editing
    Python to say what term it is.
    """
    from latexally.config import builtin_profile_names, default_builtin_profile

    assert builtin_profile_names() == ["archive", "current"]
    assert default_builtin_profile() == "current"


def test_two_courses_claiming_to_be_current_is_not_a_default(installed):
    """A contradiction in the data, and taking the first would hide it."""
    from latexally.config import default_builtin_profile

    (installed / "archive.yaml").write_text(
        (installed / "archive.yaml").read_text().replace(
            "name: archive\n", "name: archive\ndefault: true\n"
        )
    )
    assert default_builtin_profile() is None


def test_one_course_installed_is_the_default_whether_or_not_it_says_so(installed):
    from latexally.config import default_builtin_profile

    (installed / "current.yaml").unlink()
    assert default_builtin_profile() == "archive"


async def test_the_course_is_asked_before_anything_is_read_off_it(
    profile: Profile, installed
):
    """Step one, because `here_scope` and the output root are both its output."""
    app = LatexAllyApp(profile, ask_profile=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, ProfileScreen)
        shown = visible(app)
        assert "Which course?" in shown
        # The rows are read from the directory, so both courses are offered and
        # each is named the way a person would recognise it.
        assert "EE 66 - Signals, Dynamics, and Information" in shown
        assert "EECS 16A - Designing Information Devices and Systems I" in shown
        assert isinstance(app.focused, Choice)


async def test_one_course_installed_is_not_a_question(
    profile: Profile, installed
):
    """Rule 3: a step with one possible answer is not a step."""
    (installed / "archive.yaml").unlink()
    app = LatexAllyApp(profile, ask_profile=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.ask_profile is False
        assert isinstance(app.screen, ModeScreen)


async def test_the_runner_asks_only_when_the_command_line_left_it_open(
    profile: Profile, installed
):
    """`-p` is an answer, so the screen it would answer is not shown."""
    app = LatexAllyApp(profile, ask_profile=False)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, ModeScreen)


async def test_switching_course_re_derives_what_was_read_off_the_old_one(
    profile: Profile, installed, monkeypatch
):
    """The failure this guards: step two listing the previous course's corpus.

    `here_scope` and the anchored output root are both functions of the corpus,
    so a switch that left them behind would point the scope picker at the
    profile it just replaced.
    """
    from latexally.config import load_profile

    app = LatexAllyApp(load_profile("current"), ask_profile=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.profile.name == "current"
        app.use_profile("archive")
        await settle(pilot)
        assert app.profile.name == "archive"
        # The config carries the choice into run.yaml, so a saved run replays
        # against the course it was actually made for.
        assert app.config.profile == "archive"
        assert app.profile.corpus.root.resolve() == app.profile.corpus.root.resolve()


async def test_switching_to_a_course_that_is_not_installed_is_refused(
    profile: Profile, installed
):
    app = LatexAllyApp(profile, ask_profile=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        with pytest.raises(LatexAllyError):
            app.use_profile("no-such-course")


async def test_a_dirty_worktree_is_waited_out_not_a_dead_end(
    profile: Profile, monkeypatch, tmp_path: Path
):
    """`--edit` refuses on a dirty corpus, and that refusal used to end the run:
    `b` was spent, Enter exited, and the only way back was to walk the seven
    screens again. It watches instead, and starts itself when git goes quiet."""
    import latexally.build as build

    calls: list[str] = []

    def fake_build_run(config, prof, *, on_start=None, on_finish=None):
        calls.append("run")
        if len(calls) == 1:
            raise LatexAllyError("the corpus has 1 uncommitted change(s):\n    M x.tex")
        return []

    monkeypatch.setattr(build, "build_run", fake_build_run)
    monkeypatch.setattr(build, "describe_run", lambda config, prof: {})

    clean = {"yet": False}

    def fake_guard(root, ignore=None):
        if not clean["yet"]:
            raise LatexAllyError("the corpus has 1 uncommitted change(s)")

    monkeypatch.setattr(build, "require_clean_worktree", fake_guard)

    config = RunConfig().with_assignments(["sem/hw/1"])
    config.output.root = tmp_path / "out"
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
        await advance(pilot)
        screen = app.screen
        await pilot.press("b")
        for _ in range(20):
            await pilot.pause()
            if calls:
                break
        assert "uncommitted change" in visible(app)
        # `b` is live again, and Enter is not offered: the run is not over.
        assert screen.check_action("start", ()) is True
        assert screen.check_action("finish", ()) is None

        clean["yet"] = True
        screen._check_worktree()
        for _ in range(40):
            await pilot.pause()
            if len(calls) == 2:
                break
        assert len(calls) == 2, "the build did not restart itself"

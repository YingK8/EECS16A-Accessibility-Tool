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

import pytest
from textual.widgets import Button, DataTable, Input, RadioSet, SelectionList
from textual.worker import WorkerCancelled

from latexally.config import ColorPolicy, CorpusScope, Profile
from latexally.run import RunConfig
from latexally.tui.app import (
    AltScreen,
    ColorsScreen,
    DocumentsScreen,
    LatexAllyApp,
    OutputScreen,
    ReviewScreen,
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
    await press(pilot, *["escape"] * times)


async def walk_to(pilot, screen) -> None:
    """Step forward until the named screen is showing."""
    while not isinstance(pilot.app.screen, screen):
        await advance(pilot)


async def set_scope(pilot, path: str) -> None:
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


async def test_scope_is_asked_at_startup_with_no_navigation(profile: Profile):
    """The first thing on screen is the only question the tool cannot answer."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        assert isinstance(app.screen, ScopeScreen)
        assert "What do you want to convert?" in visible(app)
        await scope_all_homework(pilot)
        assert set(app.config.assignments) == {"sem/hw/1", "sem/hw/2", "sem/hw/3"}


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


async def test_arrows_walk_scopes_and_rows_and_enter_ticks(profile: Profile):
    """← → for scopes, ↑ ↓ for rows, Enter to tick, n for next.

    Enter used to do nothing after a rescan: rebuilding the options leaves
    nothing highlighted, and Enter toggles the highlighted row.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await set_scope(pilot, "sem")
        await press(pilot, "enter")
        await press(pilot, "down", "enter")
        await press(pilot, "down", "enter")
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


async def test_the_proposal_is_the_smallest_change_not_a_palette_colour(
    profile: Profile,
):
    """The point of the whole screen.

    #FF0000 fails at 4.00:1. The old fixed palette answered it with #C00000 --
    6.48:1, far past what was asked for. The proposal now is #EE0000 at 4.53:1.
    """
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await press(pilot, "u")
        assert app.config.colors.replacements(profile)["redish"] == "#EE0000"
        shown = visible(app)
        assert "#EE0000" in shown
        assert "#C00000" not in shown


async def test_a_colour_that_already_conforms_is_never_offered(profile: Profile):
    """blueish is #B31AB3 at 5.65:1. Nothing to fix, so nothing to ask about."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, BLUEISH)
        assert "blueish" not in app.config.colors.replacements(profile)
        assert "already meets the" in visible(app)
        # Offered nowhere: the footer greys u and k out on a conforming colour.
        assert app.screen.check_action("use", ()) is None
        assert app.screen.check_action("keep", ()) is None


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


async def test_accepting_the_proposal_clears_an_earlier_override(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        await set_hex(pilot, "#004400")
        await press(pilot, "u")
        assert app.config.colors.overrides == {}
        assert app.config.colors.replacements(profile)["redish"] == "#EE0000"


async def test_selecting_a_colour_row_opens_it_for_editing(profile: Profile):
    """Click the colour (or press Enter on it) rather than hunting for a field."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await colors(pilot, REDISH)
        table = app.screen.query_one("#colors", DataTable)
        table.post_message(
            DataTable.RowSelected(table, table.cursor_row, table.coordinate_to_cell_key(
                table.cursor_coordinate).row_key)
        )
        await pilot.pause()
        field = app.screen.query_one("#hex", Input)
        assert field.value == "#EE0000"
        assert field.has_focus


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


async def test_the_alt_template_warns_that_it_will_fail_the_build(profile: Profile):
    """Strict is not a question any more, so the consequence has to be stated."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "on")
        assert app.config.alt.mode == "placeholders"
        assert app.config.alt.injects is True
        # Never offered as a toggle: turning it off is what lets an unfilled
        # marker reach a reader as if it were a description.
        assert app.config.alt.strict is True
        shown = visible(app)
        assert "FAILS TO BUILD" in shown
        assert "TODO:figure-id" in shown


async def test_descriptions_can_be_switched_off(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, AltScreen)
        await choose_alt(pilot, "off")
        assert app.config.alt.mode == "off"
        assert app.config.alt.scans is False
        assert not app.screen.query_one("#alt-warning").display


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
        assert app.config.output.pdf_dir() == (Path("ally-out") / "pdf").absolute()


async def test_a_relative_artifact_path_hangs_off_the_root(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#path-pdf", "final")
        assert app.config.output.pdf_dir() == (Path("ally-out") / "final").absolute()
        # Stored as typed, so run.yaml stays portable between machines.
        assert app.config.output.as_dict()["paths"]["pdf"] == "final"


async def test_an_artifact_path_can_be_restored_to_its_default(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await output(pilot)
        await type_into(pilot, "#path-pdf", "final")
        await type_into(pilot, "#path-pdf", "")
        assert app.config.output.paths == {}
        assert app.config.output.pdf_dir() == (Path("ally-out") / "pdf").absolute()


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
        assert app.screen.query_one("#path-pdf", Input).placeholder.endswith(
            "somewhere/pdf"
        )


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
        assert app.config.output.root == Path("ally-out")
        assert app.config.output.write_mode == "mirror"


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


async def test_a_version_can_be_deselected(profile: Profile):
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        await untick(pilot, "answer", list_id="variants")
        assert app.config.variants == ("solution", "problem")


async def test_selecting_everything_is_stored_as_no_filter(profile: Profile):
    """Off then on again must return to the default, not freeze a list."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, DocumentsScreen)
        await untick(pilot, "answer", list_id="variants")
        await tick(pilot, "answer", list_id="variants")
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
    config = RunConfig().with_assignments(["sem/hw/1"])
    app = LatexAllyApp(profile, config)
    async with app.run_test(size=SIZE) as pilot:
        await walk_to(pilot, ReviewScreen)
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
        assert "your corpus is not modified" in visible(app)


async def test_the_review_names_build_as_the_key_that_writes(profile: Profile):
    """Nothing writes until a key that says "build" is pressed."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await scope_all_homework(pilot)
        await walk_to(pilot, ReviewScreen)
        shown = visible(app)
        assert "n Build" in shown, "the footer has to name what n does here"
        assert "esc Back" in shown


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
        await pilot.pause()
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
        await pilot.pause()
        shown = visible(app)
        assert "a All" in shown, "the footer has to keep naming the keys"
        assert "n Next" in shown


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


async def test_escape_on_the_first_step_does_not_quit(profile: Profile):
    """Back used to fall through to app.exit(), so Escape here killed the app."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert app.is_running
        assert isinstance(app.screen, ScopeScreen)
        assert app.screen.first


async def test_the_arrows_belong_to_the_caret_while_typing_a_path(
    profile: Profile,
):
    """In the path field ← → are the text caret, which outranks the scope row."""
    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
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
        await settle(pilot)
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
        await press(pilot, "enter")
        assert app.config.assignments == ("sem/dis/01A",)
        await set_scope(pilot, "sem/hw")
        await press(pilot, "down", "enter")
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
        await settle(pilot)
        choices = app.screen.query_one("#assignments", SelectionList)
        assert choices.option_count == 5, "the opening scan has to have landed"
        assert choices.highlighted == 0
        assert app.focused is choices, "arrows must work without focusing first"
        assert "sem" in highlighted(app)
        await press(pilot, "down", "enter")
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
        await settle(pilot)
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
        await settle(pilot)
        assert app.config.assignments == ("sem/hw/1",)
        assert app.screen._can_next
        # And the keys still act, rather than typing into the path field.
        assert not isinstance(app.focused, Input)
        await press(pilot, "n")
        assert isinstance(app.screen, DocumentsScreen)


async def test_left_and_right_walk_the_named_scopes(two_scopes: Profile):
    app = LatexAllyApp(two_scopes)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await press(pilot, "enter")
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
        assert app.config.output.write_mode == "mirror"
        await press(pilot, "down")
        assert app.config.output.in_place is True
        await press(pilot, "up")
        assert app.config.output.write_mode == "mirror"


async def test_scanning_clears_the_list_and_says_so(profile: Profile, monkeypatch):
    """The previous scope's directories must not sit there during a scan.

    A scan is seconds on a real corpus, and leaving the old list up invites
    ticking a directory that is about to disappear.
    """
    import threading

    import latexally.tui.app as appmod

    app = LatexAllyApp(profile)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
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

"""The interactive runner, driven by scripted keystrokes.

The wizard is a function from (state, answers) to a ``RunConfig``, so every
screen is testable by handing it a list of strings -- no terminal, no pilot
harness, no timing. That property is the reason the runner is built on Rich
rather than on a full-screen widget toolkit.

Output goes to a throwaway Console so the assertions are about the config the
user ended up with, never about how it was drawn.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from latexa11y.config import CorpusScope, Profile
from latexa11y.run import RunConfig
from latexa11y.tui import Wizard, _parse_selection, run_wizard


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
    )


def drive(profile: Profile, answers: list[str], config: RunConfig | None = None):
    """Run the wizard against a script and return (config, should_run, output).

    The output has its whitespace collapsed and Rich's box drawing stripped.
    Without that, an assertion about a phrase fails whenever the phrase happens
    to straddle a line break inside a panel -- which tests the terminal width,
    not the wizard.
    """
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, no_color=True, highlight=False)
    result, should_run = run_wizard(profile, config, console=console, answers=answers)
    text = buffer.getvalue()
    for char in "│╭╮╰╯─┏┓┗┛┃━┡┩╇╈┳┻╋":
        text = text.replace(char, " ")
    return result, should_run, " ".join(text.split())


# ---------------------------------------------------------------------- #
# scope
# ---------------------------------------------------------------------- #


def test_selecting_a_kind_selects_its_assignments(profile: Profile):
    # 1 → scope; 1 → the "sem" named scope; 2 → the "homework" group
    # (kinds are listed alphabetically: discussion, homework); blank → all of them.
    config, _, _ = drive(profile, ["1", "1", "2", "", "q"])
    assert set(config.assignments) == {"sem/hw/1", "sem/hw/2", "sem/hw/3"}


def test_selecting_the_other_kind_selects_only_that(profile: Profile):
    config, _, _ = drive(profile, ["1", "1", "1", "", "q"])
    assert set(config.assignments) == {"sem/dis/01A", "sem/dis/01B"}


def test_all_selects_every_kind(profile: Profile):
    config, _, _ = drive(profile, ["1", "1", "a", "", "q"])
    assert len(config.assignments) == 5


def test_a_subset_can_be_picked_by_number(profile: Profile):
    config, _, _ = drive(profile, ["1", "1", "2", "1,3", "q"])
    assert set(config.assignments) == {"sem/hw/1", "sem/hw/3"}


def test_an_explicit_path_can_be_used(profile: Profile):
    config, _, _ = drive(profile, ["1", "p", "sem/hw", "a", "", "q"])
    assert set(config.assignments) == {"sem/hw/1", "sem/hw/2", "sem/hw/3"}


def test_a_bad_path_reports_and_leaves_the_config_alone(profile: Profile):
    config, _, output = drive(profile, ["1", "p", "sem/nope", "q"])
    assert config.assignments == ()
    assert "No buildable assignments" in output or "unknown scope" in output


def test_a_fragments_only_scope_explains_itself(profile: Profile, corpus: Path):
    """The shared question bank is the easiest scope to pick by accident.

    It sorts first and its files are \\input fragments, not documents. "Nothing
    here" reads as a broken tool; the message has to say why and what to do.
    """
    bank = corpus / "sem" / "bank"
    bank.mkdir(parents=True)
    (bank / "q_one.tex").write_text("\\qns{A question}\nNo document here.\n")
    _, _, output = drive(profile, ["1", "p", "sem/bank", "q"])
    assert "No buildable assignments" in output
    assert "begin{document}" in output
    assert "input fragments" in output


# ---------------------------------------------------------------------- #
# standards
# ---------------------------------------------------------------------- #


def test_a_standard_can_be_toggled_off_and_on(profile: Profile):
    assert RunConfig().standards.question_tags is False
    config, _, _ = drive(profile, ["2", "4", "", "q"])
    assert config.standards.question_tags is True
    config, _, _ = drive(profile, ["2", "4", "4", "", "q"])
    assert config.standards.question_tags is False


def test_asking_why_explains_without_changing_anything(profile: Profile):
    """'?N' must be inspection, not a toggle with extra steps."""
    config, _, output = drive(profile, ["2", "?4", "", "q"])
    assert config.standards.question_tags is False
    assert "74 of 362" in output  # the measured reflow cost


def test_the_summary_states_each_toggle_cost(profile: Profile):
    _, _, output = drive(profile, ["2", "", "q"])
    assert "reflows 1 question in 5" in output
    assert "none measurable" in output


# ---------------------------------------------------------------------- #
# colours and descriptions
# ---------------------------------------------------------------------- #


def test_house_colours_can_be_chosen_and_are_flagged(profile: Profile):
    config, _, output = drive(profile, ["3", "2", "q"])
    assert config.colors.mode == "house"
    assert "1.4.3" in output  # the run is told this may leave it non-conforming


def test_placeholders_require_confirming_strict_mode(profile: Profile):
    config, _, output = drive(profile, ["4", "2", "y", "q"])
    assert config.alt.mode == "placeholders"
    assert config.alt.strict is True
    assert "cannot build" in output


def test_strict_can_be_declined_for_a_draft(profile: Profile):
    config, _, _ = drive(profile, ["4", "2", "n", "q"])
    assert config.alt.mode == "placeholders"
    assert config.alt.strict is False


def test_descriptions_can_be_switched_off(profile: Profile):
    config, _, _ = drive(profile, ["4", "3", "q"])
    assert config.alt.mode == "off"
    assert config.alt.scans is False


# ---------------------------------------------------------------------- #
# output
# ---------------------------------------------------------------------- #


def test_output_root_and_write_mode_can_be_set(profile: Profile, tmp_path: Path):
    target = tmp_path / "elsewhere"
    config, _, output = drive(profile, ["5", str(target), "2", "q"])
    assert config.output.root == target
    assert config.output.in_place is True
    assert "clean git worktree" in output


def test_blank_keeps_the_current_output_settings(profile: Profile):
    config, _, _ = drive(profile, ["5", "", "", "q"])
    assert config.output.root == Path("a11y-out")
    assert config.output.write_mode == "mirror"


def test_the_worklog_path_is_named_so_staff_can_find_it(profile: Profile):
    _, _, output = drive(profile, ["5", "", "", "q"])
    assert "descriptions" in output
    assert "fill these in" in output


# ---------------------------------------------------------------------- #
# running
# ---------------------------------------------------------------------- #


def test_quitting_never_sets_write(profile: Profile):
    config, should_run, _ = drive(profile, ["1", "1", "a", "", "q"])
    assert should_run is False
    assert config.write is False


def test_declining_the_confirmation_leaves_it_a_dry_run(profile: Profile):
    config, should_run, output = drive(profile, ["1", "1", "a", "", "r", "n", "q"])
    assert should_run is False
    assert config.write is False
    assert "nothing written" in output


def test_confirming_arms_the_run(profile: Profile):
    config, should_run, _ = drive(profile, ["1", "1", "a", "", "r", "y"])
    assert should_run is True
    assert config.write is True


def test_running_with_nothing_selected_is_refused(profile: Profile):
    _, should_run, output = drive(profile, ["r", "q"])
    assert should_run is False
    assert "nothing selected" in output


def test_in_place_confirmation_names_the_corpus_not_a_directory(profile: Profile):
    """The prompt must say what is about to be edited, in plain words."""
    _, _, output = drive(
        profile, ["1", "1", "a", "", "5", "", "2", "r", "n", "q"]
    )
    assert "COURSE SOURCES" in output


def test_a_saved_config_replays_identically(profile: Profile, tmp_path: Path):
    config, _, _ = drive(profile, ["1", "1", "2", "", "2", "4", "", "q"])
    path = tmp_path / "run.yaml"
    path.write_text(config.to_yaml())
    assert RunConfig.load(path).as_dict() == config.as_dict()


def test_an_unknown_command_is_reported_not_ignored(profile: Profile):
    _, _, output = drive(profile, ["zzz", "q"])
    assert "unknown command" in output


def test_a_short_script_ends_rather_than_hanging(profile: Profile):
    """Under-supplying answers must terminate: a hung test tells you nothing."""
    config, should_run, _ = drive(profile, ["1"])
    assert should_run is False


# ---------------------------------------------------------------------- #
# selection parsing
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,total,expected",
    [
        ("", 3, [1, 2, 3]),
        ("2", 3, [2]),
        ("1,3", 3, [1, 3]),
        ("1-3", 5, [1, 2, 3]),
        ("3-1", 5, []),          # backwards range yields nothing, not everything
        ("1, 2 ,3", 3, [1, 2, 3]),
        ("2,2,2", 3, [2]),       # duplicates collapse
        ("9", 3, []),            # out of range is dropped
        ("1,nonsense,3", 3, [1, 3]),
    ],
)
def test_selection_parsing(text, total, expected):
    assert _parse_selection(text, total) == expected


def test_wizard_can_save_its_config(profile: Profile, tmp_path: Path):
    buffer = io.StringIO()
    wizard = Wizard(
        profile,
        RunConfig().with_assignments(["sem/hw/1"]),
        console=Console(file=buffer, width=100, no_color=True),
    )
    path = wizard.save(tmp_path / "run.yaml")
    assert path.is_file()
    assert RunConfig.load(path).assignments == ("sem/hw/1",)
    assert "--config" in buffer.getvalue()

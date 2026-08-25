"""The run configuration and what it decides conversion means.

These tests guard the seam that the whole redesign rests on: the TUI produces a
``RunConfig`` and the engine consumes one, so if the mapping from toggles to
injected LaTeX is wrong, both paths are wrong together and silently.
"""

from __future__ import annotations

import pytest

from latexally.build import preamble_for, split_preamble
from latexally.config import Profile, load_profile
from latexally.errors import ConfigError, LatexAllyError, ToolchainError
from latexally.run import (
    STANDARD_TOGGLES,
    AltChoice,
    ColorChoice,
    Output,
    RunConfig,
    Standards,
)
from latexally.toolchain import TaggingMode


@pytest.fixture
def profile() -> Profile:
    return load_profile()


# ---------------------------------------------------------------------- #
# serialisation
# ---------------------------------------------------------------------- #


def test_yaml_round_trip_is_lossless():
    config = RunConfig(
        assignments=("sp26/hw/9", "sp26/dis/09A"),
        standards=Standards(question_tags=True, unicode_map=False),
        colors=ColorChoice(mode="house", overrides={"solutionColor": "#123456"}),
        alt=AltChoice(mode="placeholders", strict=False),
        output=Output(root="somewhere", write_mode="in-place", keep_logs=False),
    )
    restored = RunConfig.from_yaml(config.to_yaml())
    assert restored.as_dict() == config.as_dict()


def test_write_is_never_persisted():
    """Committing to a corpus is decided at run time, never inherited from a file.

    A run.yaml that could carry ``write: true`` would mean
    ``latexally build --config shared.yaml`` writes to somebody's sources because
    of a line they did not read.
    """
    config = RunConfig(assignments=("sp26/hw/9",), write=True)
    assert "write" not in config.as_dict()
    assert RunConfig.from_yaml(config.to_yaml()).write is False


def test_unknown_standard_is_rejected_not_ignored():
    # A typo'd key that silently defaults would produce a build that quietly
    # does not do what the file says.
    with pytest.raises(ConfigError) as exc:
        Standards.from_dict({"taging": True})
    assert "taging" in str(exc.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "conforming-ish"},
    ],
)
def test_bad_colour_mode_is_rejected(kwargs):
    with pytest.raises(ConfigError):
        ColorChoice(**kwargs)


def test_bad_write_mode_is_rejected():
    with pytest.raises(ConfigError):
        Output(write_mode="overwrite-everything")


def test_every_toggle_has_a_stated_cost():
    """The TUI shows `cost` verbatim; an empty one would be a silent surprise."""
    for toggle in STANDARD_TOGGLES:
        assert toggle.cost.strip()
        assert toggle.detail.strip()
        assert hasattr(Standards.defaults(), toggle.key)


# ---------------------------------------------------------------------- #
# what the toggles actually emit
# ---------------------------------------------------------------------- #


def test_defaults_emit_metadata_retrofit_and_palette(profile):
    lines = preamble_for(RunConfig(), profile, TaggingMode.LEGACY_TESTPHASE)
    assert lines[0].startswith("\\DocumentMetadata{")
    assert "testphase=" in lines[0]
    assert "\\usepackage{latexally-ee16}" in lines
    assert "\\accesssetup{conforming-colors}" in lines
    assert "\\accessquestiontags" in lines


def test_question_tags_are_on_by_default():
    """They were off, on an inferred cost that measurement did not support.

    A heading may not sit inside a paragraph, so real H2 question titles force a
    \\par, and 74 of 362 \\qns calls are followed immediately by text rather
    than a blank line. A visual cost was inferred from that count and never
    rendered. Measured across six assignments -- including all three in sp26
    that exhibit the pattern -- the difference is 0.00%, 0.00%, 0.00%, 0.00%,
    0.42%, 0.79%, with page counts identical throughout: the \\par collapses
    into the list item's existing \\parskip instead of adding to it.

    What it buys is not cosmetic. A screen reader's heading key walks the
    structure tree; the bookmark outline is a separate object graph and does not
    answer it. Without this a reader gets an H1 and then nothing.
    """
    assert Standards.defaults().question_tags is True
    assert RunConfig().standards.question_tags is True


def test_modern_toolchain_declares_conformance(profile):
    lines = preamble_for(RunConfig(), profile, TaggingMode.MODERN)
    assert "tagging=on" in lines[0]
    assert "pdfstandard=" in lines[0]
    # The legacy fallback must NOT appear as well; both would be contradictory.
    assert "testphase=" not in lines[0]


def test_unavailable_toolchain_refuses_rather_than_emitting_untagged(profile):
    """The single most important refusal in the package.

    A missing testphase module is a *silent* no-op: the build succeeds, the log
    is clean, and the PDF has no tags. Producing that quietly, for material under
    a legal accessibility obligation, is worse than any error.
    """
    with pytest.raises(ToolchainError):
        preamble_for(RunConfig(), profile, TaggingMode.UNAVAILABLE)


def test_house_colors_emits_no_palette_line(profile):
    config = RunConfig(colors=ColorChoice(mode="house"))
    lines = preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)
    assert not any("conforming-colors" in line for line in lines)


def test_question_tags_emit_the_opt_in_macro(profile):
    config = RunConfig(standards=Standards(question_tags=True))
    lines = preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)
    assert "\\accessquestiontags" in lines


def test_question_tags_without_retrofit_is_refused(profile):
    """\\accessquestiontags is defined by the retrofit, not by the core package.

    Emitting it regardless produces an undefined control sequence, which
    pdflatex in nonstop mode swallows -- still writing a PDF, so the toggle
    appears to have worked.
    """
    config = RunConfig(
        standards=Standards(retrofit=False, question_tags=True, bookmarks=True)
    )
    with pytest.raises(LatexAllyError, match="retrofit"):
        preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)


def test_no_accesssetup_without_a_package_to_define_it(profile):
    """Regression: \\accesssetup was emitted with neither package loaded."""
    config = RunConfig(
        standards=Standards(
            tagging=True,
            retrofit=False,
            bookmarks=False,
            question_tags=False,
            math_speech=False,
            unicode_map=False,
        ),
        colors=ColorChoice(mode="house"),
    )
    lines = preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)
    assert not any("accesssetup" in line for line in lines)
    # Only latexally-core and latexally-ee16 define \accesssetup; the check is
    # that neither is missing, not that no package of ours is loaded at all.
    assert not any("latexally-core" in line or "latexally-ee16" in line for line in lines)


def test_math_speech_loads_its_package_independently(profile):
    """latexally-math needs neither the retrofit nor core: it patches latex-lab."""
    config = RunConfig(
        standards=Standards(retrofit=False, bookmarks=False, question_tags=False),
    )
    lines = preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)
    assert "\\usepackage{latexally-math}" in lines


def test_draft_mode_downgrades_placeholder_errors(profile):
    config = RunConfig(alt=AltChoice(mode="placeholders", strict=False))
    lines = preamble_for(config, profile, TaggingMode.LEGACY_TESTPHASE)
    assert "\\accesssetup{strict=false}" in lines


def test_tagging_off_emits_no_metadata(profile):
    config = RunConfig(
        standards=Standards(
            tagging=False,
            retrofit=False,
            bookmarks=False,
            question_tags=False,
            unicode_map=False,
        )
    )
    lines = preamble_for(config, profile, TaggingMode.UNAVAILABLE)
    assert not any(line.startswith("\\DocumentMetadata") for line in lines)


def test_document_metadata_is_separated_to_lead_the_file(profile):
    """It is only honoured as the FIRST line; elsewhere the build is untagged.

    First, not alone: `\\AddToHook{file/.../before}` also has to sit above
    `\\documentclass`, so the lead block holds more than one line now. What
    still has to hold is the ordering.
    """
    lines = preamble_for(RunConfig(), profile, TaggingMode.LEGACY_TESTPHASE)
    lead, rest = split_preamble(lines)
    assert lead and lead[0].startswith("\\DocumentMetadata")
    assert not any(line.startswith("\\DocumentMetadata") for line in rest)
    # Everything else in the lead must be a hook, not a stray package line that
    # would be read before \documentclass.
    assert all(line.startswith("\\AddToHook") for line in lead[1:])
    # And nothing that must lead may end up in `rest`, where it would be read
    # after \documentclass and be useless.
    assert not any(
        line.startswith(("\\DocumentMetadata", "\\AddToHook")) for line in rest
    )


# ---------------------------------------------------------------------- #
# output layout
# ---------------------------------------------------------------------- #


def test_output_directories_are_distinct_and_under_the_root():
    output = Output(root="/tmp/out")
    directories = [
        output.pdf_dir(),
        output.log_dir(),
        output.tex_dir(),
        output.worklog_dir(),
    ]
    assert len(set(directories)) == 4
    for directory in directories:
        assert directory.is_relative_to(output.root)


def test_mirror_is_the_default_write_mode():
    """The safe default: the corpus is read-only unless asked otherwise."""
    assert Output().write_mode == "mirror"
    assert Output().in_place is False
    assert RunConfig().write is False


def test_a_clashing_environment_is_saved_and_restored_not_merely_freed(profile):
    r"""Freeing the name is not enough, and the first version did only that.

    ee16.sty defines `proof` and then does `\let\proof\relax` itself, expecting
    amsthm to supply one afterwards. Under `\DocumentMetadata` amsthm does not:
    the kernel has already declared a tagged `proof`, so amsthm's own
    `\newenvironment{proof}` is suppressed and the name is left `\relax`. A
    document that merely LOADS ee16 then looks fixed, while every document that
    actually writes `\begin{proof}` fails with "Environment proof undefined".
    """
    lines = preamble_for(RunConfig(), profile, TaggingMode.MODERN)
    lead, _ = split_preamble(lines)
    hooks = [line for line in lead if line.startswith("\\AddToHook")]

    before = [h for h in hooks if "/before}" in h]
    after = [h for h in hooks if "/after}" in h]
    assert before and after, "both halves are needed; freeing alone is a silent gap"
    assert len(before) == len(after) == 1, "a merge duplicated the hook"
    # The before hook saves what the kernel defined before freeing it...
    assert "\\let\\proof\\relax" in before[0]
    assert "latexallykept" in before[0]
    # ...and the after hook puts it back, undoing the package's own \let.
    assert "latexallykept" in after[0]
    assert "endproof" in after[0], "\\newenvironment defines both halves"

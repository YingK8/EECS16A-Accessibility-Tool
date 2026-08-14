"""Finding assignments, their drivers, and the files they actually use.

Built against a synthetic tree shaped like the real corpus rather than against
the corpus itself, so the suite runs anywhere and a course-content edit cannot
turn a test red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latexa11y.build import relative_dependencies
from latexa11y.config import CorpusScope, Profile
from latexa11y.errors import ConfigError
from latexa11y.run import (
    RunConfig,
    discover_assignments,
    find_driver,
    group_by_kind,
    iter_selected,
)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A miniature of the real thing, including the parts that broke.

    Mirrors three real shapes: a driver that reaches shared packages with ``../``
    hops, a body that pulls questions from a shared bank outside its own folder,
    and a preamble one level up whose own relative paths resolve against the
    *build* directory rather than against itself.
    """
    (tmp_path / "shared.sty").write_text("% shared package\n")
    (tmp_path / "sem").mkdir()
    (tmp_path / "sem" / "term.sty").write_text("% term package\n")

    bank = tmp_path / "bank" / "q"
    bank.mkdir(parents=True)
    (bank / "q_plot.tex").write_text(
        "\\qns{Plotting}\nHere is a picture.\n"
        "\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}\n"
    )

    hw = tmp_path / "sem" / "hw" / "3"
    hw.mkdir(parents=True)
    (hw / "sol3.tex").write_text(
        "\\documentclass{article}\n"
        "\\usepackage{../../../shared}\n"
        "\\usepackage{../../term}\n"
        "\\input{body}\n"
    )
    (hw / "body.tex").write_text(
        "\\begin{document}\n"
        "\\input{../../../bank/q/q_plot.tex}\n"
        "\\end{document}\n"
    )
    (hw / "prob3.tex").write_text("\\documentclass{article}\n\\input{body}\n")

    dis = tmp_path / "sem" / "dis" / "01A"
    dis.mkdir(parents=True)
    (dis / "sol01A.tex").write_text("\\input{../preamble}\n\\input{body01A}\n")
    (dis / "body01A.tex").write_text("\\begin{document}\nHello.\n\\end{document}\n")
    # One level above the assignment, and its own \usepackage is written relative
    # to the ASSIGNMENT directory, not to itself. Copied from the real shape:
    # sp26/dis/preambleFa23.tex says \usepackage{../../fa23}, and two hops from
    # sp26/dis/09A lands on sp26/fa23.sty.
    (tmp_path / "sem" / "dis" / "preamble.tex").write_text(
        "\\documentclass{article}\n\\usepackage{../../term}\n"
    )

    # A directory with no \begin{document} anywhere: a shared includes folder.
    (tmp_path / "sem" / "figures").mkdir()
    (tmp_path / "sem" / "figures" / "common.tex").write_text("% fragments only\n")
    return tmp_path


@pytest.fixture
def profile(corpus: Path) -> Profile:
    return Profile(
        name="test",
        corpus=CorpusScope(
            root=corpus,
            include=("**/*.tex",),
            kinds={"hw": "homework", "dis": "discussion", "bank": "bank"},
            named={"sem": ("sem/**/*.tex",)},
        ),
    )


# ---------------------------------------------------------------------- #
# driver detection
# ---------------------------------------------------------------------- #


def test_driver_prefers_the_naming_convention(corpus: Path):
    # sol<name>.tex wins over prob<name>.tex, which also opens a document.
    assert find_driver(corpus / "sem" / "hw" / "3") == "sol3.tex"


def test_driver_falls_back_to_content(corpus: Path, tmp_path: Path):
    odd = corpus / "sem" / "hw" / "9"
    odd.mkdir(parents=True)
    (odd / "zzz.tex").write_text("fragment\n")
    (odd / "main-document.tex").write_text("\\begin{document}\\end{document}\n")
    assert find_driver(odd) == "main-document.tex"


def test_directory_without_a_document_has_no_driver(corpus: Path):
    assert find_driver(corpus / "sem" / "figures") is None


# ---------------------------------------------------------------------- #
# discovery
# ---------------------------------------------------------------------- #


def test_discovery_classifies_by_profile_kinds(profile: Profile):
    found = {item.path: item for item in discover_assignments(profile, "sem")}
    assert found["sem/hw/3"].kind == "homework"
    assert found["sem/dis/01A"].kind == "discussion"


def test_unclassified_directories_are_other_not_dropped(profile: Profile):
    kinds = {item.kind for item in discover_assignments(profile, "sem")}
    assert "other" in kinds


def test_non_buildable_directories_are_reported_not_hidden(profile: Profile):
    """A folder with no driver may be a shared includes dir -- or a broken one.

    Either way it appears in the listing with ``buildable`` False, so the runner
    can say how many it skipped instead of quietly narrowing the job.
    """
    found = {item.path: item for item in discover_assignments(profile, "sem")}
    assert found["sem/figures"].buildable is False
    assert found["sem/hw/3"].buildable is True


def test_group_by_kind_is_ordered_and_total(profile: Profile):
    found = discover_assignments(profile, "sem")
    grouped = group_by_kind(found)
    assert list(grouped) == sorted(grouped)
    assert sum(len(items) for items in grouped.values()) == len(found)


def test_selecting_a_missing_assignment_raises(profile: Profile):
    """Converting four of the five things asked for is worse than stopping."""
    config = RunConfig().with_assignments(["sem/hw/3", "sem/hw/404"])
    with pytest.raises(ConfigError, match="404"):
        list(iter_selected(profile, config))


# ---------------------------------------------------------------------- #
# the include graph
# ---------------------------------------------------------------------- #


def test_dependencies_follow_relative_package_paths(corpus: Path):
    found = relative_dependencies(corpus / "sem" / "hw" / "3" / "sol3.tex")
    assert corpus / "shared.sty" in found
    assert corpus / "sem" / "term.sty" in found


def test_dependencies_reach_the_shared_question_bank(corpus: Path):
    """The finding that motivated all of this.

    An assignment's questions -- and so most of its figures -- live outside its
    own directory. Measured on the real sp26 corpus, 76.5% of graphics are
    reached this way, so a directory-scoped scan reports a clean sweep having
    looked at a quarter of the material.
    """
    found = relative_dependencies(corpus / "sem" / "hw" / "3" / "sol3.tex")
    assert corpus / "bank" / "q" / "q_plot.tex" in found


def test_dependencies_resolve_against_the_build_directory(corpus: Path):
    """TeX's rule, and not an approximation of it.

    ``sem/dis/preamble.tex`` says ``\\usepackage{../../term}``. Resolved against
    the preamble's own directory (``sem/dis``) that is ``<root>/term.sty``, which
    does not exist. Resolved against the assignment being built (``sem/dis/01A``)
    it is ``sem/term.sty``, which does -- and which is what TeX looks for.
    """
    found = relative_dependencies(corpus / "sem" / "dis" / "01A" / "sol01A.tex")
    assert corpus / "sem" / "term.sty" in found
    assert corpus / "sem" / "dis" / "preamble.tex" in found
    assert not (corpus / "term.sty").exists(), (
        "fixture must not also satisfy the wrong resolution, or it proves nothing"
    )


def test_tex_tree_packages_are_never_treated_as_local_files(corpus: Path, tmp_path: Path):
    driver = corpus / "sem" / "hw" / "3" / "sol3.tex"
    driver.write_text(driver.read_text() + "\\usepackage{tikz}\n\\usepackage{amsmath}\n")
    found = relative_dependencies(driver)
    assert not any(path.name in ("tikz.sty", "amsmath.sty") for path in found)


def test_dependency_walk_terminates_on_a_cycle(corpus: Path):
    a = corpus / "sem" / "hw" / "3" / "cycle_a.tex"
    b = corpus / "sem" / "hw" / "3" / "cycle_b.tex"
    a.write_text("\\input{cycle_b}\n")
    b.write_text("\\input{cycle_a}\n")
    assert relative_dependencies(a) == {a, b}

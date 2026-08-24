"""Choosing the stand-in for a question the live bank no longer has.

The policy is the whole point. 875 of this corpus's missing includes exist in
several snapshots that hold *different* versions of the question, so picking
carelessly puts the wrong question into a document that claims to be a faithful
conversion -- a worse outcome than the build failing. These tests pin the order
and the flag; the build tests cover the copying.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latexally.repair import bank_search_order, find_replacements


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    for name in (
        "questionBank",
        "fa18_questionBank",
        "sp21_questionBank",
        "fa21_questionBank",
        "sp24_questionBank",
        "notes",
    ):
        (tmp_path / name).mkdir()
    return tmp_path


def bank(corpus: Path, name: str, tail: str, text: str) -> Path:
    path = corpus / name / tail
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------- #
# the order
# ---------------------------------------------------------------------- #


def test_the_assignments_own_semester_is_searched_first(corpus: Path):
    order = [path.name for path in bank_search_order(corpus, "sp21")]
    assert order[0] == "sp21_questionBank"


def test_earlier_semesters_come_before_later_ones(corpus: Path):
    """The question as it stood when the assignment was set.

    A question is likelier to have been edited after an assignment used it than
    before, so walking backwards is the closer guess.
    """
    order = [path.name for path in bank_search_order(corpus, "fa21")]
    assert order == [
        "fa21_questionBank",
        "sp21_questionBank",
        "fa18_questionBank",
        "sp24_questionBank",
        "questionBank",
    ]


def test_the_live_bank_is_the_last_resort(corpus: Path):
    """It is today's wording, which is the furthest thing from "back then"."""
    for semester in ("fa18", "sp24", "fa99"):
        assert bank_search_order(corpus, semester)[-1].name == "questionBank"


def test_an_assignment_older_than_every_snapshot_walks_forward(corpus: Path):
    """fa17 predates the oldest snapshot here, and still has to resolve."""
    order = [path.name for path in bank_search_order(corpus, "fa17")]
    assert order[0] == "fa18_questionBank"


def test_a_directory_that_is_not_a_semester_still_gets_an_order(corpus: Path):
    order = [path.name for path in bank_search_order(corpus, "exams")]
    assert order and order[-1].name if False else order[-1] == "questionBank"


# ---------------------------------------------------------------------- #
# the choice
# ---------------------------------------------------------------------- #


def request(corpus: Path, target: str, *, referenced_by: str, semester: str):
    source = corpus / referenced_by
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"\\input{{{target}}}\n")
    return find_replacements(
        [(source, target)],
        corpus_root=corpus,
        mirror_root=corpus / "out" / "tex",
        semester=semester,
    )


def test_the_own_semester_copy_wins(corpus: Path):
    bank(corpus, "sp21_questionBank", "hw/9/q_x.tex", "mine")
    bank(corpus, "fa18_questionBank", "hw/9/q_x.tex", "older")
    found = request(
        corpus, "../../../questionBank/hw/9/q_x", referenced_by="sp21/hw/9/body.tex",
        semester="sp21",
    )
    assert [item.used.read_text() for item in found] == ["mine"]
    assert found[0].ambiguous is False


def test_identical_copies_are_not_flagged_however_many_there_are(corpus: Path):
    """No choice was made, so there is nothing to warn about."""
    for name in ("fa18_questionBank", "sp21_questionBank", "questionBank"):
        bank(corpus, name, "hw/9/q_x.tex", "same everywhere")
    found = request(
        corpus, "../../../questionBank/hw/9/q_x", referenced_by="fa21/hw/9/body.tex",
        semester="fa21",
    )
    assert found[0].ambiguous is False
    assert len(found[0].candidates) == 3


def test_differing_copies_are_flagged(corpus: Path):
    """The 875. A stand-in here may be a different question entirely."""
    bank(corpus, "fa18_questionBank", "hw/9/q_x.tex", "one wording")
    bank(corpus, "sp24_questionBank", "hw/9/q_x.tex", "another wording")
    found = request(
        corpus, "../../../questionBank/hw/9/q_x", referenced_by="fa21/hw/9/body.tex",
        semester="fa21",
    )
    assert found[0].ambiguous is True
    assert found[0].used.read_text() == "one wording", "nearest earlier"
    assert "fix" and found[0].fix.startswith("copy ")


def test_the_stand_in_lands_where_the_source_looks_for_it(corpus: Path):
    """Placed in the mirror at the offset the source wrote, so nothing is edited."""
    bank(corpus, "fa18_questionBank", "hw/9/q_x.tex", "text")
    found = request(
        corpus, "../../../questionBank/hw/9/q_x", referenced_by="fa17/hw/9/body.tex",
        semester="fa17",
    )
    assert found[0].destination == corpus / "out" / "tex" / "questionBank/hw/9/q_x.tex"


def test_a_target_climbing_out_of_the_mirror_is_refused(corpus: Path):
    """Otherwise this tool scatters copies into whatever sits above the output."""
    bank(corpus, "fa18_questionBank", "hw/9/q_x.tex", "text")
    found = request(
        corpus, "../../../../../../questionBank/hw/9/q_x",
        referenced_by="fa17/hw/9/body.tex", semester="fa17",
    )
    assert found == []


def test_a_question_nowhere_in_the_corpus_is_left_alone(corpus: Path):
    found = request(
        corpus, "../../../questionBank/hw/9/q_missing",
        referenced_by="fa21/hw/9/body.tex", semester="fa21",
    )
    assert found == []


def test_a_bank_name_that_was_never_a_real_directory_still_resolves(corpus: Path):
    """`fall19_questionBank` is referenced by fa19 assignments and has never
    existed; only the tail after the bank component is meaningful."""
    bank(corpus, "fa18_questionBank", "hw/7/q_x.tex", "found anyway")
    found = request(
        corpus, "../../../fall19_questionBank/hw/7/q_x",
        referenced_by="fa19/hw/7/body.tex", semester="fa19",
    )
    assert found[0].used.read_text() == "found anyway"


def test_a_space_in_the_path_is_written_to_both_names(corpus: Path):
    r"""``\usepackage`` squeezes the space out; ``\input`` keeps it.

    ``fa19/hw/7`` writes ``../../../fall19_questionBank /hw/6/q_x`` -- a space
    the author typed. LaTeX's package loader looks for the bare path and
    ``\input`` looks for the spaced one, so writing only one of them fixes one
    caller and breaks the other. It cost a build each way round before the
    stand-in was placed at both.
    """
    bank(corpus, "fa18_questionBank", "hw/6/q_x.tex", "text")
    found = request(
        corpus, "../../../fall19_questionBank /hw/6/q_x",
        referenced_by="fa19/hw/7/body.tex", semester="fa19",
    )
    tex = corpus / "out" / "tex"
    assert found[0].destination == tex / "fall19_questionBank /hw/6/q_x.tex"
    assert found[0].alias == tex / "fall19_questionBank/hw/6/q_x.tex"


def test_a_clean_path_needs_no_second_name(corpus: Path):
    bank(corpus, "fa18_questionBank", "hw/6/q_x.tex", "text")
    found = request(
        corpus, "../../../questionBank/hw/6/q_x",
        referenced_by="fa19/hw/6/body.tex", semester="fa19",
    )
    assert found[0].alias is None


def test_the_suffix_comes_from_the_file_that_was_found(corpus: Path):
    """Forcing .tex put a .sty at a name TeX would never load."""
    bank(corpus, "fa18_questionBank", "hw/7/kbordermatrix.sty", "package")
    found = request(
        corpus, "../../../questionBank/hw/7/kbordermatrix",
        referenced_by="fa19/hw/7/sol7.tex", semester="fa19",
    )
    assert found[0].used.suffix == ".sty"
    assert found[0].destination.suffix == ".sty"

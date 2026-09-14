"""The burn-down `latexally figures` reports.

The one thing worth pinning here is that the per-scope rows are a breakdown of
one set and not a partition of it. The profile's scopes overlap by design --
`live` contains `bank` -- and a figure is content-addressed, so the same
drawing reached by two scopes is one description to write. Summing the rows
overcounts, and a reader who trusts the sum is told the corpus is bigger than
it is.
"""

from pathlib import Path

import pytest

from latexally.catalog import coverage
from latexally.config import CorpusScope, Profile


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    (tmp_path / "bank").mkdir()
    (tmp_path / "sp26").mkdir()
    # The same drawing, byte for byte, in two scopes: one figure, two sites.
    shared = "\\begin{tikzpicture}\n    \\node {A};\n\\end{tikzpicture}\n"
    (tmp_path / "bank" / "q.tex").write_text(shared)
    (tmp_path / "sp26" / "q.tex").write_text(shared)
    (tmp_path / "sp26" / "other.tex").write_text(
        "\\begin{tikzpicture}\n    \\node {B};\n\\end{tikzpicture}\n"
    )
    return tmp_path


@pytest.fixture
def profile(corpus: Path) -> Profile:
    return Profile(
        name="test",
        corpus=CorpusScope(
            root=corpus,
            include=("**/*.tex",),
            named={"bank": ("bank/**/*.tex",), "sp26": ("sp26/**/*.tex",)},
        ),
    )


def test_one_drawing_in_two_scopes_is_counted_once(profile: Profile):
    report = coverage(profile)

    assert report.total == 2, "the shared drawing is one figure, not two"
    assert report.call_sites == 3


def test_the_scope_rows_overlap_and_do_not_sum_to_the_total(profile: Profile):
    report = coverage(profile)
    rows = {row["name"]: row["total"] for row in report.as_dict()["by_scope"]}

    assert rows == {"bank": 1, "sp26": 2}
    assert sum(rows.values()) > report.total


def test_a_description_in_any_worklog_counts_everywhere(profile: Profile, corpus: Path):
    """Content addressing is the payoff: describe it once, it is done in both."""
    from latexally.catalog import build_catalog, worklog_dir

    catalog = build_catalog(profile, write=False)
    shared = next(
        identity for identity, entry in catalog.entries.items() if len(entry.sites) == 2
    )
    directory = worklog_dir(profile)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bank.yaml").write_text(
        f"{shared}:\n  at: bank/q.tex:1\n  alt_text: A single node labelled A.\n"
    )

    report = coverage(profile)

    assert report.done == 1
    assert report.outstanding == 1
    by_scope = {row["name"]: row["done"] for row in report.as_dict()["by_scope"]}
    assert by_scope == {"bank": 1, "sp26": 1}


def test_coverage_writes_nothing(profile: Profile, corpus: Path):
    """It is the command you run between authoring sessions. It must be inert."""
    before = {p: p.stat().st_mtime_ns for p in corpus.rglob("*") if p.is_file()}

    coverage(profile)

    after = {p: p.stat().st_mtime_ns for p in corpus.rglob("*") if p.is_file()}
    assert after == before

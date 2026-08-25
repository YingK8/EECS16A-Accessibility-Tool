r"""The whole questionBank as a test.

Three claims, in cost order.

**Every document finds its files** (fast, always runs). Parametrising 3,000
documents into 3,000 test cases would make a failure readable and the suite
unusable, so the sweep runs once and the assertion is a diff against
``corpus_baseline.json``. The baseline is checked in: a fix that unblocks
documents changes it in the same commit as the code, and so does a regression.
Neither can happen quietly.

**The mirror is byte-for-byte the source plus insertions** (fast). The one
promise this tool makes about the material it is handed: nothing is edited, and
the converted copy differs from the original only by lines that were added.

**A tagged PDF actually comes out** (slow, ``-m corpus``). One pdflatex run per
document is a minute each, so this is a deliberate act, not something that runs
on every edit.

All three read the live corpus and write only into ``tmp_path``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from corpus_sweep import compare, read_baseline, sweep
from latexally.build import materialise, preamble_for
from latexally.config import load_profile
from latexally.discover import discover_assignments
from latexally.run import Output, RunConfig

#: Skip rather than fail when the corpus is not beside the checkout. These tests
#: are about *this* corpus; on a machine without it there is nothing to say.
_profile = None
try:  # pragma: no cover - depends on the machine, not on the code
    _profile = load_profile("eecs16a")
    _has_corpus = _profile.corpus.root.is_dir()
except Exception:
    _has_corpus = False

needs_corpus = pytest.mark.skipif(not _has_corpus, reason="questionBank not present")


@pytest.fixture(scope="module")
def results():
    return sweep(_profile)


@needs_corpus
def test_no_document_loses_a_reference_it_used_to_resolve(results):
    """The sweep matches the recorded baseline, reference for reference.

    Counting would not do. Swapping one broken include for another leaves the
    total unchanged while quietly moving the damage, so the comparison is over
    the set of unresolved targets per document.
    """
    baseline = read_baseline()
    if not baseline:
        pytest.skip("no baseline recorded; run `python -m tools.repair_lab.sweep --write`")

    report = compare(results, baseline)
    assert not report["regressed"], (
        f"{len(report['regressed'])} documents lost references they used to "
        f"resolve, e.g. {report['regressed'][:5]}"
    )
    assert not report["vanished"], (
        f"{len(report['vanished'])} documents are no longer discovered at all, "
        f"e.g. {report['vanished'][:5]}"
    )


@needs_corpus
def test_the_sweep_is_deterministic():
    """Two runs of discovery agree exactly.

    The cheap way to get this wrong is to let a `glob()` or a `set` decide which
    of several equal candidates wins, which produces a tool that repairs a
    document differently on someone else's machine.
    """
    first = [(item.path, tuple(sorted(item.drivers.items()))) for item in discover_assignments(_profile)]
    second = [(item.path, tuple(sorted(item.drivers.items()))) for item in discover_assignments(_profile)]
    assert first == second


@needs_corpus
@pytest.mark.parametrize(
    "assignment",
    [
        # One per era and per shape: the fa15 exam whose driver used to be
        # mistaken for its body, a discussion whose class comes from a preamble
        # one directory up, a note, and a current homework.
        "exams/fa15/mt1",
        "sp26/dis/06B",
        "notes/note0",
        "sp26/hw/9",
    ],
)
def test_the_mirror_is_the_source_plus_insertions(assignment: str, tmp_path: Path):
    """Nothing in the mirror differs from the corpus except by added lines.

    Checked by reconstruction rather than by inspection: strip the inserted
    lines back out of the converted driver and the bytes must be the original's,
    exactly -- same encoding, same line endings, same trailing whitespace. Every
    other mirrored file must be an unmodified copy of a corpus file.
    """
    root = _profile.corpus.root.resolve()
    found = {item.path: item for item in discover_assignments(_profile)}
    if assignment not in found:
        pytest.skip(f"{assignment} is not in this corpus")

    item = found[assignment]
    config = RunConfig(
        assignments=[assignment], write=True, output=Output(root=tmp_path, write_mode="mirror")
    )
    lines = preamble_for(config, _profile)
    prepared = materialise(item, config, _profile, lines=lines, driver=item.driver)

    original = (root / assignment / item.driver).read_bytes()
    converted = prepared.driver.read_bytes()
    inserted = b"".join(f"{line}\n".encode() for line in lines)
    stripped = converted
    for chunk in inserted.splitlines(keepends=True):
        stripped = stripped.replace(chunk, b"", 1)
    assert stripped == original, "the converted driver is not the original plus insertions"

    # And every other file in the assignment's mirrored folder is a copy.
    for path in prepared.work_dir.iterdir():
        if not path.is_file() or path.name in (item.driver, f"{Path(item.driver).stem}-original.tex"):
            continue
        source = root / assignment / path.name
        if source.is_file():
            assert path.read_bytes() == source.read_bytes(), f"{path.name} was modified in the mirror"


@needs_corpus
def test_the_corpus_is_never_written_to(tmp_path: Path):
    """A whole conversion leaves the corpus with the same mtimes it started with.

    The guarantee everything else rests on. Content hashes would miss a file
    rewritten with identical bytes; mtimes catch the write itself.
    """
    root = _profile.corpus.root.resolve()
    watched = sorted((root / "sp26" / "hw" / "9").rglob("*"))
    before = {path: path.stat().st_mtime_ns for path in watched if path.is_file()}

    item = next(i for i in discover_assignments(_profile) if i.path == "sp26/hw/9")
    config = RunConfig(
        assignments=["sp26/hw/9"], write=True, output=Output(root=tmp_path, write_mode="mirror")
    )
    materialise(item, config, _profile, driver=item.driver)

    after = {path: path.stat().st_mtime_ns for path in before}
    assert after == before


# ---------------------------------------------------------------------- #
# the slow tier
# ---------------------------------------------------------------------- #


@needs_corpus
@pytest.mark.corpus
def test_a_sample_of_the_corpus_still_compiles(tmp_path: Path):
    """Build a seeded sample and allow no regressions.

    A *regression* is a document whose unconverted source compiles and whose
    converted copy does not. Documents that were already broken before this tool
    touched them are counted and reported, never asserted on -- there is nothing
    a conversion pipeline can do about a 2015 exam that calls a macro its
    era's style file no longer defines.

    Sample size comes from ``LATEXALLY_CORPUS_SAMPLE`` so CI can run 10 and a
    release can run 200 without editing the test.
    """
    from corpus_compile import compile_sample

    count = int(os.environ.get("LATEXALLY_CORPUS_SAMPLE", "8"))
    results = compile_sample(count=count, seed=16, out=tmp_path, profile=_profile)
    regressions = [item for item in results if item.verdict == "regression"]
    inherited = [item for item in results if item.verdict == "inherited"]

    print(
        f"\n{len(results)} documents: "
        f"{sum(1 for i in results if i.verdict == 'ok')} ok, "
        f"{len(inherited)} already broken before conversion, "
        f"{len(regressions)} regressions"
    )
    assert not regressions, "\n".join(
        f"{item.assignment} {item.variant}: {(item.errors or [''])[0]}" for item in regressions
    )

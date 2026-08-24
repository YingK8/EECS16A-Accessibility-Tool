"""Math alt text: parsing latex-lab's hand-off, and putting speech back.

The fixture is real output from `\tagpdfsetup{math/mathml/write-dummy}` on
TeX Live 2025, not a hand-written approximation. Two of the bugs these tests
pin were only visible against the real thing: latex-lab writes its MD5 in
UPPERCASE hex, and it writes `\frac {x}` with a space after the control
sequence.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from latexally.mathspeech import (
    _ALIGNAT,
    Formula,
    _escape_tex_string,
    _unwrap,
    convert,
    read_dummy,
    write_sources,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mathml_dummy.html"


def by_source(formulas, needle: str):
    """Pick a fixture formula by what it contains, not by position.

    The fixture is regenerated from real `write-dummy` output whenever the
    sample document changes, and the order is latex-lab's, not ours.
    """
    return next(f for f in formulas if needle in f.tex)


def test_dummy_file_yields_inline_and_display_formulas():
    formulas = read_dummy(FIXTURE)

    assert all(f.hash == f.hash.upper() for f in formulas), (
        "hashes are uppercase hex; a lowercase-only pattern silently matches nothing"
    )
    assert by_source(formulas, r"\frac").display is False
    assert by_source(formulas, "align*").display is True
    assert by_source(formulas, "array").display is True
    # Prefixed so a math id can never collide with a figure's `fig-`/`img-`.
    assert by_source(formulas, r"\frac").id == "math-2BB2E069FD45"


def test_repeated_formulas_collapse_to_one_entry(tmp_path: Path):
    """523,828 occurrences over 35,504 distinct strings: dedup is the pipeline."""
    doubled = tmp_path / "dup.html"
    text = FIXTURE.read_text()
    doubled.write_text(text.replace("</html>", "") + text.split("<html>", 1)[1])

    formulas = read_dummy(doubled)

    assert len(formulas) == 4
    assert {f.count for f in formulas} == {2}


def test_structured_environments_are_handed_over_intact():
    """`align` is what tells the converter `&` is an alignment tab.

    Stripping the wrapper -- which an earlier version did, on the assumption
    that a delimiter is a delimiter -- turns "Line 1: y equals m x plus b" into
    "y ampersand equals m x plus b". Measured in the live corpus, `align` and
    `align*` occur 29,820 times against 7,357 for `equation`/`equation*`, so
    this is the common case, not an edge one.
    """
    tex = r"\begin {align*} y &= mx + b \\ z &= 2y \end {align*}"

    body, display = _unwrap(tex)

    assert body == tex, "the environment must survive to the converter"
    assert display is True


def test_the_authors_latex_is_never_reformatted():
    r"""latex-lab writes `\begin {align*}` -- with a space. It stays that way.

    The pipeline reads what the engine reports and writes generated files
    beside the PDF; no `.tex` is touched, and nothing normalises the author's
    spelling of an environment on the way to the converter.
    """
    original = r"\begin {align*}y &= mx + b \\ z &= 2y\end {align*}"

    body, _ = _unwrap(original)

    assert body == original


def test_only_alignat_is_rewritten_and_only_in_memory():
    r"""The single exception, and it never reaches disk.

    `latex2mathml` yields zero rows for `\begin{alignat}{2}` and reads every
    `&` aloud as "ampersand"; `align` renders the same content correctly and
    the column count is presentational. The substitution happens on the string
    handed to the converter, so the author's source keeps its `alignat`.
    """
    rewritten = _ALIGNAT.sub(r"\1{align\2}", r"\begin {alignat}{2} a &= b \end {alignat}")

    assert rewritten == r"\begin {align} a &= b \end {align}"
    assert _ALIGNAT.sub(r"\1{align\2}", r"\begin {align} a &= b \end {align}") == (
        r"\begin {align} a &= b \end {align}"
    ), "the rewrite must be a no-op on anything that is not alignat"


def test_delimiters_around_a_structured_environment_still_come_off():
    r"""`\[ ... \begin{cases} ... \end{cases} ... \]` -- strip the outside only."""
    body, display = _unwrap(r"\[ f(x) = \begin{cases} 1 & x>0 \end{cases} \]")

    assert body.startswith("f(x)")
    assert r"\end{cases}" in body
    assert display is True


def test_an_augmented_matrix_keeps_its_array():
    r"""`\end{array}` must survive whatever unwrapping happens around it.

    The augmented matrix is the construct this corpus most needs: 13,130
    `array` and 90,258 `bmatrix` uses in the live tree.
    """
    tex = (
        r"\begin {equation}\left [\begin {array}{cc|c}1 & 2 & 3\\4 & 5 & 6"
        r"\end {array}\right ]\end {equation}"
    )

    body, display = _unwrap(tex)

    assert r"\end {array}" in body
    assert display is True


@pytest.mark.parametrize(
    "tex,expected_display",
    [(r"$x+1$", False), (r"\(x+1\)", False), (r"$$x+1$$", True), (r"\[x+1\]", True)],
)
def test_unwrap_strips_every_bare_delimiter_style(tex: str, expected_display: bool):
    """Left on, SRE reads the delimiter aloud: "dollar sign x plus 1"."""
    body, display = _unwrap(tex)

    assert body == "x+1"
    assert display is expected_display


def test_speech_is_written_with_expl3_space_tokens():
    """A literal space is discarded under \\ExplSyntaxOn.

    The resulting /Alt is well-formed, present, passes veraPDF, and reads
    "thefractionwithnumerator..." aloud.
    """
    assert _escape_tex_string("the fraction with x") == "the~fraction~with~x"
    assert "\\" not in _escape_tex_string(r"a \frac b")


def test_generated_files_stay_out_of_the_source_tree(tmp_path: Path):
    """Everything generated lands beside the PDF, never in the corpus."""
    formulas = read_dummy(FIXTURE)
    outdir = tmp_path / "out"

    write_sources({formulas[0].hash: ("<math></math>", "x")}, formulas, "job", outdir)

    assert {path.name for path in outdir.iterdir()} == {
        "job-mathml.html",
        "job-mathspeech.ltx",
    }
    assert not list(tmp_path.glob("*.tex"))


def test_write_sources_emits_both_files_keyed_by_the_latex_lab_hash(tmp_path: Path):
    formulas = read_dummy(FIXTURE)
    first, second = formulas[0], formulas[1]
    results = {first.hash: ("<math></math>", "x plus one")}

    mathml_path, speech_path = write_sources(results, formulas, "job", tmp_path)

    assert mathml_path.name == "job-mathml.html", "the name latex-lab looks for"
    speech = speech_path.read_text()
    assert f"g__latexally_speech_{first.hash} _tl" in speech
    assert "x~plus~one" in speech
    # A formula with no speech is omitted rather than given an empty entry, so
    # ALLY-PDF-040 reports it instead of it reading as a pass.
    assert second.hash not in speech


@pytest.mark.skipif(shutil.which("node") is None, reason="speech needs Node")
def test_conversion_produces_speech_and_keeps_augmented_matrix_columns(tmp_path: Path):
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    formulas = read_dummy(FIXTURE)

    results = convert(formulas, cache=tmp_path / "cache.json")

    _, inline_speech = results[by_source(formulas, r"\frac").hash]
    assert inline_speech == (
        "the fraction with numerator x squared minus 1 and denominator x plus 1"
    )
    assert "$" not in inline_speech and "\\" not in inline_speech
    matrix_mathml, matrix_speech = results[by_source(formulas, "array").hash]
    assert "columnlines" in matrix_mathml, "the augmented-matrix divider must survive"
    assert "matrix" in matrix_speech.lower()
    _, align_speech = results[by_source(formulas, "align*").hash]
    assert align_speech.startswith("2 lines"), align_speech
    assert "ampersand" not in align_speech

    # ponytail: clearspeak says "the 2 by 3 matrix" and does not announce the
    # augmented divider. The MathML /AF carries it; upgrade to MathCAT if a
    # student reports the gap.

    assert (tmp_path / "cache.json").is_file()
    assert convert(formulas, cache=tmp_path / "cache.json") == results


@pytest.mark.skipif(shutil.which("node") is None, reason="speech needs Node")
@pytest.mark.parametrize(
    "name,tex",
    [
        ("align*", r"\begin {align*} y &= mx + b \\ z &= 2y \end {align*}"),
        ("align", r"\begin{align} y &= mx + b \end{align}"),
        ("bmatrix", r"$\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}$"),
        ("cases", r"\[ f(x) = \begin{cases} 1 & x>0 \\ 0 & x\le 0 \end{cases} \]"),
        ("alignat", r"\begin{alignat}{2} a &= b & c &= d \end{alignat}"),
        ("array", r"$\left[\begin{array}{cc|c}1&2&3\\4&5&6\end{array}\right]$"),
    ],
)
def test_every_environment_the_corpus_uses_speaks_without_markup(name: str, tex: str):
    """Counted in the live tree, most-used first. No alignment tab may be heard.

    `alignat` is here because the converter yields *zero* rows for it and reads
    every `&` aloud unless it is rewritten to `align` first.
    """
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    digest = name.upper().ljust(32, "0")

    results = convert([Formula(digest, tex)], cache=None)

    assert digest in results, f"{name} produced no speech at all"
    speech = results[digest][1]
    assert "ampersand" not in speech, f"{name} read an alignment tab aloud: {speech!r}"
    assert "dollar" not in speech
    assert "\\" not in speech and "$" not in speech

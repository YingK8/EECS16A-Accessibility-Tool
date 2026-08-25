"""Math alt text: parsing latex-lab's hand-off, and putting speech back.

The fixture is real output from `\tagpdfsetup{math/mathml/write-dummy}` on
TeX Live 2025, not a hand-written approximation. Two of the bugs these tests
pin were only visible against the real thing: latex-lab writes its MD5 in
UPPERCASE hex, and it writes `\frac {x}` with a space after the control
sequence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latexally.mathspeech import (
    DRIVER,
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


@pytest.mark.skipif(not DRIVER.is_file(), reason="speech needs the MathCAT driver built")
def test_conversion_produces_speech_and_keeps_augmented_matrix_columns(tmp_path: Path):
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    formulas = read_dummy(FIXTURE)

    results = convert(formulas, cache=tmp_path / "cache.json")

    _, inline_speech = results[by_source(formulas, r"\frac").hash]
    assert inline_speech == (
        "the fraction with numerator; x squared minus 1; and denominator x plus 1"
    )
    # The semicolons are MathCAT's prosody, not markup: with `TTS=None` it emits
    # pauses as punctuation rather than as SSML a reader would spell out.
    assert "$" not in inline_speech and "\\" not in inline_speech
    matrix_mathml, matrix_speech = results[by_source(formulas, "array").hash]
    assert "columnlines" in matrix_mathml, "the augmented-matrix divider must survive"
    # The divider is *spoken*, not merely carried in the MathML /AF. Upstream
    # MathCAT reads no mtable line attribute, so this asserts the one rule the
    # `vendor/MathCAT` fork adds -- and fails loudly if a rebase drops it.
    assert matrix_speech.startswith("the 2 by 3 augmented matrix"), matrix_speech
    _, align_speech = results[by_source(formulas, "align*").hash]
    assert align_speech.startswith("2 equations"), align_speech
    assert "ampersand" not in align_speech

    assert (tmp_path / "cache.json").is_file()
    assert convert(formulas, cache=tmp_path / "cache.json") == results


@pytest.mark.skipif(not DRIVER.is_file(), reason="speech needs the MathCAT driver built")
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


# ---------------------------------------------------------------------- #
# course macros the converter has never heard of
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source, expected",
    [
        # 591 of the 700 macro occurrences measured in real write-dummy output.
        (r"\mat{A}", r"\mathbf{A}"),
        # A prefix rewrite, so a nested argument cannot mis-nest.
        (r"\mat{\vec{x}}", r"\mathbf{\vec{x}}"),
        (r"\wt{H}", r"\tilde{H}"),
        # Both ends change, so these are brace-scanned rather than matched.
        (r"\norm{\vec{v}}", r"\lVert \vec{v} \rVert"),
        (r"\bmqty{\mat{S}}", r"\begin{bmatrix}\mathbf{S}\end{bmatrix}"),
        (r"\SI{1}{\kohm}", r"1 \mathrm{k\Omega}"),
        (r"\R", r"\mathbb{R}"),
        # Sizing is presentational; left in, the reader hears "backslash brace".
        (r"\big\{x\big\}", r"\{x\}"),
        # `aligned` yields invalid MathML; `align*` says what the author meant.
        (r"\begin{aligned}y&=1\end{aligned}", r"\begin{align*}y&=1\end{align*}"),
    ],
)
def test_expand_macros(source: str, expected: str):
    from latexally.mathspeech import expand_macros

    assert expand_macros(source) == expected


def test_a_longer_macro_is_not_mistaken_for_a_shorter_one():
    r"""`\normalsize` is not `\norm`, and `\bigcup` is not `\big`."""
    from latexally.mathspeech import expand_macros

    assert expand_macros(r"\normalsize x") == r"\normalsize x"
    assert expand_macros(r"\bigcup A") == r"\bigcup A"


def test_a_superscripted_matrix_is_wrapped_before_conversion():
    r"""The single most expensive converter bug on this corpus.

    `latex2mathml` emits an `<msup>` with the wrong number of children for
    `\begin{bmatrix}...\end{bmatrix}^{\top}`, MathCAT refuses it -- "msup should
    have 2 children" -- and a transposed matrix ships with no `/Alt` at all. On
    a linear-algebra course that is not an edge case. A brace group is enough.
    """
    from latexally.mathspeech import expand_macros

    wrapped = expand_macros(r"\begin{bmatrix}1 & 2\end{bmatrix}^{\top}")

    # `^{\top}` is normalised to `^{T}` on the way past -- see
    # test_transpose_is_spelled_the_way_the_engine_recognises_it.
    assert wrapped == r"{\begin{bmatrix}1 & 2\end{bmatrix}}^{T}"
    # Unscripted, it must be left exactly as it was.
    assert expand_macros(r"\begin{bmatrix}1 & 2\end{bmatrix}") == (
        r"\begin{bmatrix}1 & 2\end{bmatrix}"
    )


@pytest.mark.skipif(not DRIVER.is_file(), reason="speech needs the MathCAT driver built")
def test_a_transposed_matrix_actually_speaks():
    """The end of that chain, through the real converter and real engine."""
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    formula = Formula("H", r"$\begin{bmatrix}1 & 2\end{bmatrix}^{\top}$")

    results = convert([formula])

    assert formula.hash in results, "a transposed matrix produced no speech at all"
    speech = results[formula.hash][1]
    assert "matrix" in speech and "transpose" in speech, speech


def test_the_cache_is_discarded_when_the_conversion_recipe_changes(tmp_path: Path):
    r"""The cache key cannot see the thing most likely to change.

    It is latex-lab's hash of the *source*, which is what makes it survive
    edits, renames and semester rollovers. It also means a change to the
    conversion -- expanding `\mat` to `\mathbf` altered a third of this
    corpus's speech -- moves no hash at all, so a rerun would serve the old
    strings forever.
    """
    import json

    from latexally.mathspeech import RECIPE

    formulas = read_dummy(FIXTURE)
    cache = tmp_path / "cache.json"
    first = convert(formulas, cache=cache)
    assert json.loads(cache.read_text())["#recipe"] == RECIPE

    stored = json.loads(cache.read_text())
    stored["#recipe"] = "some-older-pipeline"
    stored[next(iter(first))] = ["<math/>", "stale speech nobody should hear"]
    cache.write_text(json.dumps(stored))

    assert convert(formulas, cache=cache) == first


def test_transpose_is_spelled_the_way_the_engine_recognises_it():
    r"""`^{\top}` and `^T` are the same operator and were not spoken the same.

    MathCAT matches `^T` to its transpose intent and says "transpose"; nothing
    matches `^{\top}`, which comes out as the literal "superscript top". This
    corpus writes it the second way 11,345 times, and `\top` never appears
    outside a superscript in it -- so there it always means transpose.
    """
    from latexally.mathspeech import expand_macros

    assert expand_macros(r"\vec{x}^\top") == r"\vec{x}^{T}"
    assert expand_macros(r"\mathbf{A}^{\top}") == r"\mathbf{A}^{T}"


@pytest.mark.skipif(not DRIVER.is_file(), reason="speech needs the MathCAT driver built")
@pytest.mark.parametrize(
    "tex, expected",
    [
        # The fork's TEMP_NAME rule. Without it MathCAT's own internal wrapper
        # reaches the catch-all and a reader hears the words "TEMP NAME of".
        # 315 matrix inverses in this corpus.
        (r"$\begin{bmatrix}1 & 2\\3 & 4\end{bmatrix}^{-1}$", "matrix"),
        (r"$\begin{bmatrix}1 & 2\end{bmatrix}^{\top}$", "transpose"),
    ],
)
def test_a_scripted_matrix_says_nothing_internal(tex: str, expected: str):
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    formula = Formula("H", tex)

    results = convert([formula])

    assert formula.hash in results, f"no speech at all for {tex}"
    speech = results[formula.hash][1]
    assert "TEMP" not in speech.upper(), speech
    assert expected in speech, speech


@pytest.mark.skipif(not DRIVER.is_file(), reason="speech needs the MathCAT driver built")
def test_a_formula_that_says_nothing_is_not_recorded_as_spoken():
    r"""`$\\$` is a line break that ended up inside maths.

    It converts to a lone `<mspace linebreak="newline"/>`, and MathCAT is right
    to return "" for it. Recording that as a success writes an empty token
    list, `\tl_if_exist` then succeeds, and the document ships `/Alt ()` --
    which satisfies a naive "every Formula has /Alt" check and tells a reader
    nothing. Found on sp26/hw/3, where it was the single ALLY-PDF-040 in 179
    formulas.
    """
    pytest.importorskip("latex2mathml", reason="math descriptions need the [math] extra")
    empty = Formula("E", "$\\\\$")
    real = Formula("R", "$x + 1$")

    results = convert([empty, real])

    assert real.hash in results
    assert empty.hash not in results, "an empty /Alt must not be written"


@pytest.mark.parametrize(
    "source, expected",
    [
        # `aligned`, `flalign` and `eqnarray` all yield invalid MathML, and the
        # formula then ships with no /Alt at all. Each was found by a real
        # build, not by reading.
        (r"\begin{flalign*}y &= 1&\end{flalign*}", "align"),
        (r"\begin{eqnarray*}y &=& 1\end{eqnarray*}", "align"),
    ],
)
def test_align_lookalikes_become_align(source: str, expected: str):
    from latexally.mathspeech import expand_macros

    assert expected in expand_macros(source)


def test_an_eqnarray_row_loses_only_its_middle_separator():
    r"""eqnarray is `lhs & rel & rhs`; align is `lhs & rel-and-rhs`."""
    from latexally.mathspeech import expand_macros

    out = expand_macros(r"\begin{eqnarray*} y &=& mx+b \\ z &=& 2y \end{eqnarray*}")

    assert "&=  mx+b" in out.replace("& = ", "&= ")
    assert out.count("&") == 2, out


def test_speech_is_folded_to_characters_pdftex_can_write():
    r"""MathCAT renders mathvariant="bold" as the Unicode bold letters.

    `$\textbf{vector projection}$` came back as U+1D42F U+1D41E ... -- useless
    as speech, and four UTF-8 octets each, which pdfTeX cannot encode. The
    generated token list then leaked LaTeX's own error text onto the page:
    "known as \UTFvi ii@four@octets vector projection", on page 1 of
    sp26/hw/2, in a build reported as clean with zero errors.
    """
    from latexally.mathspeech import normalise_speech

    assert normalise_speech("\U0001D42F\U0001D41E\U0001D41C\U0001D42D\U0001D428\U0001D42B") == "vector"
    # Inside the BMP is left alone: pdfTeX handles those and they carry meaning.
    assert normalise_speech("alpha β and x") == "alpha β and x"
    assert all(ord(c) <= 0xFFFF for c in normalise_speech("\U0001F600 x"))


def test_the_escaped_string_never_carries_a_four_octet_character():
    from latexally.mathspeech import _escape_tex_string

    out = _escape_tex_string("\U0001D42F\U0001D41E\U0001D41C x")

    assert out == "vec~x"
    assert all(ord(c) <= 0xFFFF for c in out)


@pytest.mark.parametrize(
    "source, expected",
    [
        # A matrix NAME: 11,279 of the corpus's 12,478 \mat arguments.
        (r"\mat{A}", r"\mathbf{A}"),
        (r"\mat{\vec{x}}", r"\mathbf{\vec{x}}"),
        # A matrix BODY: the other 1,199. \mathbf{| & | \\ ...} is invalid
        # MathML, MathCAT refuses it, and the formula ships with no /Alt --
        # three of them in notes_sp21/note23 alone.
        (r"\mat{1 & 2 \\ 3 & 4}", r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"),
        (r"\mat{a \\ b}", r"\begin{bmatrix}a \\ b\end{bmatrix}"),
    ],
)
def test_mat_expands_by_what_its_argument_holds(source: str, expected: str):
    r"""The macro means two things and the semesters disagree about which.

    39 of its 47 live definitions are `\mathbf{#1}` and five are
    `\begin{bmatrix}#1\end{bmatrix}`. An `&` or a `\\` inside the argument can
    only be a body, so the content decides rather than a guess.
    """
    from latexally.mathspeech import expand_macros

    assert expand_macros(source) == expected


def test_latex_lab_write_dummy_artifacts_are_not_treated_as_formulas(tmp_path: Path):
    r"""`write-dummy` sometimes captures hyperref's machinery, not the maths.

    For a numbered `eqnarray` the rendered source template is
    `\if@eqnstar \else \ifx \\\@currentHref ... \hyper@makecurrent`, which is
    not an equation. Seven of this corpus's entries are that.

    They used to be rejected downstream as invalid MathML, which looked like an
    engine limitation. Once the `eqnarray` rewrite made them convertible they
    started producing fluent nonsense instead -- "table with 4 rows and 2
    columns; row 1; column 1; backslash if at sign e q n s t a r" -- which a
    reader hears as if it were the formula. Dropping them restores no `/Alt`
    and an ALLY-PDF-040, which is the honest outcome.
    """
    dummy = tmp_path / "d-mathml-dummy.html"
    dummy.write_text(
        "<html>\n<div>\n<h2>\\mml 1</h2>\n<p>$x + 1$</p>\n<p>AAAA1111BBBB2222</p>\n</div>\n"
        "<div>\n<h2>\\mml 2</h2>\n"
        "<p>\\begin {eqnarray}\\if@eqnstar \\else \\ifx \\\\\\@currentHref</p>\n"
        "<p>CCCC3333DDDD4444</p>\n</div>\n</html>\n",
        encoding="utf-8",
    )

    formulas = read_dummy(dummy)

    assert [f.tex for f in formulas] == ["$x + 1$"]


def test_a_label_is_not_spelled_out_mid_equation():
    r"""`\label{eq:vref}` is heard as "label e q colon v r e f" if left in.

    It places a `\ref` target, typesets nothing, and means nothing aloud. The
    converter has no reason to know that, so it reads the key as content and
    spells it letter by letter in the middle of the equation. Measured on a
    109-document sample: 25 formulas across 10 documents.
    """
    from latexally.mathspeech import expand_macros

    assert "label" not in expand_macros(r"V_{ref} = \frac{R_2}{R_1} \label{eq:vref}")
    assert "notag" not in expand_macros(r"a = b \notag")
    assert "nonumber" not in expand_macros(r"a = b \nonumber")


def test_stripping_bookkeeping_leaves_real_content_alone():
    r"""Only the bookkeeping goes. `\text{...}` saying "label" is content."""
    from latexally.mathspeech import expand_macros

    kept = expand_macros(r"x = 1 \text{ the label reads } y")
    assert "label" in kept and "\\text" in kept


def test_prose_between_equations_is_spoken_not_spelled():
    r"""`\shortintertext` carries the author's reasoning and was lost twice over.

    latex-lab tags a whole align as one Formula, so its /Alt replaces the
    subtree and the interleaved sentences are never announced. The converter
    meanwhile read the macro name as content: "backslash shortintertext,
    Subtract colon t w o", spelling the words one letter at a time.

    299 occurrences across 39 files. Measured through the real converter,
    `\text{}` says the same prose properly.
    """
    from latexally.mathspeech import Formula, convert, expand_macros

    assert expand_macros(r"a \shortintertext{Subtract: two} b") == r"a \text{Subtract: two} b"
    assert expand_macros(r"a \intertext{Now plug in} b") == r"a \text{Now plug in} b"

    spoken = convert(
        [
            Formula(
                hash="test-intertext",
                tex=r"\begin{align} a &= b \\ \shortintertext{Subtract: two} \\ c &= d \end{align}",
            )
        ]
    ).get("test-intertext")

    assert spoken, "the align must convert at all"
    assert "Subtract: two" in spoken[1]
    assert "intertext" not in spoken[1]


def test_a_cross_reference_is_said_not_spelled():
    r"""`\eqref{eqn:one}` spelled its key AND broke the prose around it.

    The number it typesets is assigned by LaTeX during the build, so it is not
    knowable here. Left in, the converter read the macro and key as content --
    "eqref, e q n, colon o n e" -- and its presence inside a `\text` broke the
    text handling, so the surrounding sentence was spelled out too. This is a
    real /Alt from fa20/hw/1:

        w e g e t t h e u n i q u e s o l u t i o n

    "the referenced equation" does not say which one, and is a phrase rather
    than a spelled-out key.
    """
    from latexally.mathspeech import Formula, convert, expand_macros

    assert expand_macros(r"\eqref{eqn:one}") == "the referenced equation"
    assert expand_macros(r"\ref{fig:a}") == "the referenced equation"

    tex = (
        r"\begin{align} a &= b \\ "
        r"\shortintertext{Subtract: \eqref{eqn:one} - 2*\eqref{eqn:two}} \\ "
        r"c &= d \end{align}"
    )
    spoken = convert([Formula(hash="test-eqref", tex=tex)]).get("test-eqref")

    assert spoken, "the align must still convert"
    assert "Subtract: the referenced equation" in spoken[1]
    assert "eqref" not in spoken[1]

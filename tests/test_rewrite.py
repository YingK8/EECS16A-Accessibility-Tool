r"""Rewriting the constructs LaTeX's tagging cannot compile.

Every case here was measured against the real corpus first, and two of them
contradict the hint the detecting rule used to print. The docstrings say which,
because that is the part a future reader will not believe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latexally.rewrite import plan_rewrites


def plan_for(tmp_path: Path, text: str):
    source = tmp_path / "q.tex"
    source.write_text(text, encoding="utf-8")
    return plan_rewrites(source)


# ---------------------------------------------------------------------- #
# ALLY-SRC-042
# ---------------------------------------------------------------------- #


def test_a_break_after_display_math_gets_a_line_to_end(tmp_path):
    r"""`\mbox{}`, not deletion.

    Measured on sp26/hw/3: `\mbox{}` moves 0.002% of pixels; deleting the break
    removes a blank line and repaginates 0.42% of them.
    """
    plan = plan_for(tmp_path, "\\[\nx\n\\] \\\\\nafter\n")

    assert plan.result() == "\\[\nx\n\\] \\mbox{}\\\\\nafter\n"
    assert plan.counts() == {"ALLY-SRC-042": 1}


def test_every_spelling_of_display_math_is_covered(tmp_path):
    r"""`$$ ... $$\\` is what actually broke sp26/hw/3, not `\end{align}`."""
    plan = plan_for(tmp_path, "$$\nx\n$$\n\\\\\ny\n\\end{align*}\n\\newline\n")

    assert plan.counts() == {"ALLY-SRC-042": 2}
    assert plan.result().count("\\mbox{}") == 2


# ---------------------------------------------------------------------- #
# ALLY-SRC-043
# ---------------------------------------------------------------------- #


def test_inline_math_is_closed_with_the_delimiter_it_opened_with(tmp_path):
    plan = plan_for(tmp_path, "products of \\(a_i\\) and \\(b_j$ where\n")

    assert plan.result() == "products of \\(a_i\\) and \\(b_j\\) where\n"
    assert plan.counts() == {"ALLY-SRC-043": 1}


def test_prose_after_an_open_paren_is_reported_and_never_reclosed(tmp_path):
    r"""28 of the corpus's 30 sites, and the case the rule's hint got wrong.

    `\(1) put 4 resistors in series, and let it be $R$` is not a mismatched
    formula. It is a literal `(` written as `\(`, and closing it with `\)`
    would set the rest of the sentence in math mode.
    """
    plan = plan_for(tmp_path, "\\(1) put 4 resistors in series, and let it be $R$\n")

    assert not plan.changed
    assert [item.rule for item in plan.skipped] == ["ALLY-SRC-043"]
    assert "prose" in plan.skipped[0].reason


def test_ordinary_and_escaped_dollars_are_left_alone(tmp_path):
    plan = plan_for(tmp_path, "a price of \\(\\frac{\\$5}{2}\\) is fine, and so is $x$\n")

    assert not plan.changed and not plan.skipped


# ---------------------------------------------------------------------- #
# ALLY-SRC-041
# ---------------------------------------------------------------------- #

AUGMENTED = (
    "\\[\n\\begin{bmatrix}\n\\begin{array}{cc|c}\n"
    "1 & 2 & 3\\\\\n4 & 5 & 6\n"
    "\\end{array}\n\\end{bmatrix}\n\\]\n"
)


def test_an_augmented_matrix_keeps_its_divider(tmp_path):
    r"""The rule's own hint said to delete the array. 257 of the corpus's 357
    sites carry a `|` in the column spec, so that would delete the augmentation
    bar from every one of them. The nesting is inverted instead.
    """
    plan = plan_for(tmp_path, AUGMENTED)

    result = plan.result()
    assert "\\left[" in result and "\\right]" in result
    assert "{cc|c}" in result, "the divider lives in the array's column spec"
    assert "bmatrix" not in result
    assert plan.counts() == {"ALLY-SRC-041": 1}


def test_the_rows_never_pass_through_python(tmp_path):
    """Both ends are spliced separately, so the body is copied, not rebuilt."""
    plan = plan_for(tmp_path, AUGMENTED)

    assert "1 & 2 & 3\\\\\n4 & 5 & 6" in plan.result()


@pytest.mark.parametrize(
    "environment, opener, closer",
    [("pmatrix", "\\left(", "\\right)"), ("vmatrix", "\\left|", "\\right|")],
)
def test_every_matrix_flavour_keeps_its_own_delimiters(tmp_path, environment, opener, closer):
    plan = plan_for(tmp_path, AUGMENTED.replace("bmatrix", environment))

    assert opener in plan.result() and closer in plan.result()


def test_a_matrix_holding_more_than_the_array_is_reported_not_rewritten(tmp_path):
    plan = plan_for(
        tmp_path,
        "\\[\n\\begin{bmatrix}\nx &\n\\begin{array}{c}1\\end{array}\n\\end{bmatrix}\n\\]\n",
    )

    assert not plan.changed
    assert [item.rule for item in plan.skipped] == ["ALLY-SRC-041"]


# ---------------------------------------------------------------------- #
# ALLY-SRC-040
# ---------------------------------------------------------------------- #


def test_a_list_label_does_not_assume_it_is_at_depth_one(tmp_path):
    r"""`\labelenumi`, which the rule's hint named, is wrong for most sites.

    483 of the corpus's 667 are two enumerates deep or more *within their own
    file*, and that is a lower bound: every question file is `\input` inside
    the driver's own `\begin{enumerate}[series=qn]`. `\AllyEnumLabel` asks
    LaTeX for the depth at the moment the list opens.
    """
    plan = plan_for(tmp_path, "\\begin{enumerate}[label=(\\roman*)]\n\\item a\n\\end{enumerate}\n")

    result = plan.result()
    assert "\\AllyEnumLabel{(\\roman{\\allyenum})}%" in result
    assert "label=" not in result
    assert "\\begin{enumerate}\n" in result
    assert plan.counts() == {"ALLY-SRC-040": 1}


def test_the_other_options_survive(tmp_path):
    """Only the `label` key is deleted, not the option group."""
    plan = plan_for(
        tmp_path,
        "\\begin{enumerate}[label=\\roman*., itemsep=10pt, leftmargin=2pt]\n"
        "\\item a\n\\end{enumerate}\n",
    )

    result = plan.result()
    assert "itemsep=10pt" in result and "leftmargin=2pt" in result
    assert "label=" not in result


def test_a_braced_label_keeps_its_text(tmp_path):
    r"""`label={Step \arabic*.}` -- enumitem strips one level of braces."""
    plan = plan_for(
        tmp_path,
        "\\begin{enumerate}[label={Step \\arabic*.}]\n\\item a\n\\end{enumerate}\n",
    )

    assert "\\AllyEnumLabel{Step \\arabic{\\allyenum}.}" in plan.result()


def test_the_indent_of_a_nested_list_is_preserved(tmp_path):
    """Otherwise a reviewer reads a whitespace change instead of the fix."""
    plan = plan_for(
        tmp_path,
        "text\n      \\begin{enumerate}[label=(\\roman*)]\n\\item a\n\\end{enumerate}\n",
    )

    assert "\n      \\begin{enumerate}\n" in plan.result()


def test_a_starred_length_is_reported_not_rewritten(tmp_path):
    r"""`leftmargin=*` means "as wide as the widest label".

    No static length reproduces it, and substituting one moves the text -- the
    one thing this tool promises not to do.
    """
    plan = plan_for(tmp_path, "\\begin{enumerate}[(A), leftmargin=*]\n\\item a\n\\end{enumerate}\n")

    assert not plan.changed
    assert [item.rule for item in plan.skipped] == ["ALLY-SRC-040"]
    assert "starred length" in plan.skipped[0].reason


# ---------------------------------------------------------------------- #
# the invariant that holds across all four
# ---------------------------------------------------------------------- #


def test_a_comment_is_never_rewritten(tmp_path):
    """Fails the moment anyone reads `.text` where `.masked` was meant."""
    plan = plan_for(
        tmp_path,
        "% \\begin{align}x\\end{align}\\\\\n"
        "% \\begin{enumerate}[label=\\roman*]\n"
        "% \\(b_j$\n",
    )

    assert not plan.changed
    assert not plan.skipped


def test_a_file_with_nothing_to_fix_is_left_byte_identical(tmp_path):
    text = "\\begin{enumerate}\n\\item plain\n\\end{enumerate}\n"
    plan = plan_for(tmp_path, text)

    assert not plan.changed
    assert plan.result() == text


# ---------------------------------------------------------------------- #
# ALLY-SRC-045
# ---------------------------------------------------------------------- #


def test_a_blank_line_inside_display_math_is_removed(tmp_path):
    r"""Under tagging `\[` becomes an `equation*` whose argument a `\par` ends.

    "Paragraph ended before \environment equation* was complete", and no PDF.
    Untagged it is only "Missing $ inserted", recovered from, which is why the
    corpus carried 59 of these unnoticed.
    """
    plan = plan_for(tmp_path, "\\[\n\\begin{bmatrix}\n1 & 2\n\n3 & 4\n\\end{bmatrix}\n\\]\n")

    assert plan.counts() == {"ALLY-SRC-045": 1}
    assert "1 & 2\n3 & 4" in plan.result()


def test_a_blank_line_inside_inline_math_is_removed(tmp_path):
    plan = plan_for(tmp_path, "Let $\\begin{bmatrix}a\\\\b\n\n\\end{bmatrix}$ be.\n")

    assert plan.counts() == {"ALLY-SRC-045": 1}


def test_a_paragraph_break_between_two_formulas_is_not_touched(tmp_path):
    r"""The bug this test exists for, which was measured before it was caught.

    A regex that scans from one `$` to the next cannot tell which `$` OPENS
    math. On `$x$ prose\n\nmore prose $y$` it matched the *closing* dollar of
    the first formula through the *opening* dollar of the second and deleted a
    real paragraph break. One document lost a page; another stopped building.
    Inline math is a toggle, so it has to be counted from the top of the file.
    """
    text = "Text $x$ and prose.\n\nA new paragraph with $y$ in it.\n\nAnd $z$ ends.\n"
    plan = plan_for(tmp_path, text)

    assert not plan.changed
    assert plan.result() == text


def test_an_odd_number_of_dollars_does_not_run_away(tmp_path):
    """Unbalanced source must not make the scanner treat the rest as maths."""
    plan = plan_for(tmp_path, "One $x lonely.\n\nA paragraph after it.\n")

    assert not plan.changed


def test_display_dollars_are_not_read_as_two_inline_delimiters(tmp_path):
    plan = plan_for(tmp_path, "$$\nx\n$$\n\nprose after\n\n$$y$$\n")

    assert not plan.changed


def test_an_escaped_dollar_does_not_open_maths(tmp_path):
    plan = plan_for(tmp_path, "A price of \\$5 today.\n\nAnd \\$6 tomorrow.\n")

    assert not plan.changed


# ---------------------------------------------------------------------- #
# ALLY-SRC-046
# ---------------------------------------------------------------------- #


def test_maths_holding_only_spacing_is_unwrapped(tmp_path):
    r"""`$\\$` is a line break someone put dollars around.

    It still becomes a tagged Formula element, and no alt text can describe a
    line break -- so ALLY-PDF-040 reports it forever and nothing can ever
    satisfy it. A permanent false positive is worse than a silent one: it
    teaches people to skim the report. Found as the single ALLY-PDF-040 in
    sp26/hw/3's 179 formulas. Measured: the page is byte-identical unwrapped.
    """
    plan = plan_for(tmp_path, "Line one$\\\\$Line two and a$\\,$thin space.\n")

    assert plan.counts() == {"ALLY-SRC-046": 2}
    assert plan.result() == "Line one\\\\Line two and a\\,thin space.\n"


def test_real_maths_is_never_unwrapped(tmp_path):
    plan = plan_for(tmp_path, "Here $x + 1$ and $\\vec{i}$ and $\\alpha$ stay.\n")

    assert not plan.changed

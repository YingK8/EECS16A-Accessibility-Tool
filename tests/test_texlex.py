"""Tests for the scanning and editing layer.

The comment-awareness tests are the most important in the suite. The previous
generation of this tooling used bare regexes; because 25-33% of
``\\includegraphics`` call sites in this corpus sit on commented-out lines, it
wrapped dead code in a live environment, which swallowed the ``\\begin`` into the
comment and left an unmatched ``\\end`` — a guaranteed compile failure. Those
regressions are pinned here.
"""

from __future__ import annotations

import pytest

from latexa11y.errors import EditConflictError
from latexa11y.texlex import EditBuffer, TexSource


# ---------------------------------------------------------------------- #
# comment awareness
# ---------------------------------------------------------------------- #


def test_commented_macro_is_not_found():
    src = TexSource(
        "\\includegraphics{live.png}\n"
        "%   \\includegraphics{dead.png}\n"
        "    % \\includegraphics{also_dead.png}\n"
    )
    found = list(src.finditer(r"\\includegraphics"))
    assert len(found) == 1
    # The surviving match must be the live one on line 1, not either dead one.
    assert src.line_of(found[0].start()) == 1
    assert src.text.startswith("\\includegraphics{live.png}", found[0].start())


def test_escaped_percent_does_not_start_a_comment():
    src = TexSource(r"50\% of \includegraphics{a.png} is fine")
    assert len(list(src.finditer(r"\\includegraphics"))) == 1
    assert src.comments == ()


def test_double_backslash_then_percent_is_a_comment():
    # `\\` is a control symbol, so the following `%` really does open a comment.
    src = TexSource("row one \\\\% trailing note\n\\includegraphics{a.png}")
    assert len(src.comments) == 1
    assert len(list(src.finditer(r"\\includegraphics"))) == 1


def test_comment_consumes_its_newline():
    # TeX joins lines across a comment; the scanner must model that so that
    # offsets after a comment stay correct.
    src = TexSource("a%comment\nb")
    assert src.comments[0].start == 1
    assert src.comments[0].end == len("a%comment\n")


def test_verbatim_protects_percent_and_macros():
    src = TexSource(
        "\\begin{verbatim}\n"
        "% this is not a comment\n"
        "\\includegraphics{not_real.png}\n"
        "\\end{verbatim}\n"
        "\\includegraphics{real.png}\n"
    )
    assert src.comments == ()
    assert len(list(src.finditer(r"\\includegraphics"))) == 1


def test_lstlisting_is_verbatim():
    src = TexSource(
        "\\begin{lstlisting}\n%\\includegraphics{x}\n\\end{lstlisting}\n"
        "\\includegraphics{y}"
    )
    assert len(list(src.finditer(r"\\includegraphics"))) == 1


def test_verb_delimiter_is_respected():
    src = TexSource("\\verb|%not a comment| and \\includegraphics{a.png}")
    assert src.comments == ()
    assert len(list(src.finditer(r"\\includegraphics"))) == 1


def test_unterminated_verbatim_does_not_leak():
    # Rather than mis-scanning the remainder of a broken file, treat the tail as
    # verbatim so nothing downstream edits it.
    src = TexSource("\\begin{verbatim}\n\\includegraphics{x}\n")
    assert len(list(src.finditer(r"\\includegraphics"))) == 0


def test_is_code_distinguishes_blanked_spaces():
    # A blanked-out comment character becomes a space; a real space is also a
    # space. `is_code` must not decide by comparing the two strings.
    src = TexSource("a % b\nc")
    space_in_comment = src.text.index("% b") + 1
    real_space = src.text.index(" ")
    assert src.is_code(real_space)
    assert not src.is_code(space_in_comment)


# ---------------------------------------------------------------------- #
# brace balancing
# ---------------------------------------------------------------------- #


def test_nested_braces_in_optional_argument():
    src = TexSource(r"\includegraphics[alt={A {nested} description}]{x.png}")
    start = src.text.index("[")
    group = src.match_optional(start)
    assert group is not None
    assert src.text[group.inner] == "alt={A {nested} description}"


def test_escaped_braces_do_not_unbalance():
    src = TexSource(r"\alt{a \{ b \} c}")
    group = src.match_group(src.text.index("{"))
    assert group is not None
    assert src.text[group.inner] == r"a \{ b \} c"


def test_brace_group_ignores_braces_inside_comments():
    src = TexSource("\\alt{real % }\n content}")
    group = src.match_group(src.text.index("{"))
    assert group is not None
    assert group.end == len(src.text)


def test_macro_call_parses_optional_and_required():
    src = TexSource(r"\includegraphics[width=2in]{figs/a.png}")
    calls = src.macro_calls("includegraphics", optional=1, required=1)
    assert len(calls) == 1
    assert calls[0].optional == ("width=2in",)
    assert calls[0].required == ("figs/a.png",)


def test_macro_call_does_not_match_longer_name():
    src = TexSource(r"\qns{Title} \qnsextra{Other}")
    calls = src.macro_calls("qns", required=1)
    assert len(calls) == 1
    assert calls[0].required == ("Title",)


# ---------------------------------------------------------------------- #
# environments
# ---------------------------------------------------------------------- #


def test_nested_environments_report_outermost_only():
    src = TexSource(
        "\\begin{tikzpicture}\nouter\n"
        "\\begin{tikzpicture}\ninner\n\\end{tikzpicture}\n"
        "\\end{tikzpicture}"
    )
    spans = src.environments("tikzpicture")
    assert len(spans) == 1
    assert spans[0].start == 0
    assert spans[0].end == len(src.text)


def test_environment_options_are_captured():
    src = TexSource("\\begin{axis}[xlabel=$a$, ymin=0]\n\\addplot {x};\n\\end{axis}")
    span = src.environments("axis")[0]
    assert span.options == "xlabel=$a$, ymin=0"
    assert src.text[span.body].strip().startswith("\\addplot")


def test_commented_environment_is_skipped():
    src = TexSource("% \\begin{circuitikz}\n% \\end{circuitikz}\n")
    assert src.environments("circuitikz") == []


# ---------------------------------------------------------------------- #
# normalisation / content identity
# ---------------------------------------------------------------------- #


def test_normalised_is_stable_across_indentation_and_comments():
    a = TexSource("\\draw (0,0) -- (1,1);")
    b = TexSource("   \\draw   (0,0)  % a note\n    -- (1,1);  ")
    assert a.normalised() == b.normalised()


def test_normalised_preserves_meaningful_content():
    src = TexSource("\\addplot coordinates {(1,2) (3,4)};")
    assert "(1,2) (3,4)" in src.normalised()


# ---------------------------------------------------------------------- #
# edit buffer
# ---------------------------------------------------------------------- #


def test_edits_apply_right_to_left_without_shifting():
    original = "AAA BBB CCC"
    buffer = EditBuffer()
    buffer.replace(0, 3, "xxxxxx", rule="R1")
    buffer.replace(8, 11, "y", rule="R2")
    assert buffer.apply(original) == "xxxxxx BBB y"


def test_wrap_never_passes_content_through_python():
    original = "\\includegraphics{a.png}"
    buffer = EditBuffer()
    buffer.wrap(0, len(original), "\\begin{AltOnly}{alt}\n", "\n\\end{AltOnly}", rule="W")
    result = buffer.apply(original)
    assert original in result
    assert result.startswith("\\begin{AltOnly}{alt}\n")
    assert result.endswith("\n\\end{AltOnly}")


def test_overlapping_edits_raise_rather_than_clobber():
    buffer = EditBuffer()
    buffer.replace(0, 5, "a", rule="R1", reason="first")
    buffer.replace(3, 8, "b", rule="R2", reason="second")
    with pytest.raises(EditConflictError) as excinfo:
        buffer.apply("0123456789")
    assert "R1" in str(excinfo.value) and "R2" in str(excinfo.value)


def test_two_insertions_at_the_same_point_keep_order():
    buffer = EditBuffer()
    buffer.insert(3, "<", rule="A")
    buffer.insert(3, ">", rule="B")
    assert buffer.apply("abcdef") == "abc<>def"


def test_edit_beyond_source_length_is_rejected():
    buffer = EditBuffer()
    buffer.replace(0, 99, "x")
    with pytest.raises(EditConflictError):
        buffer.apply("short")


def test_empty_buffer_round_trips_exactly():
    original = "\\documentclass{article}\n% comment\n\\begin{document}\nx\n\\end{document}\n"
    assert EditBuffer().apply(original) == original
    assert EditBuffer().diff(original) == ""


def test_summary_counts_per_rule():
    buffer = EditBuffer()
    buffer.insert(0, "a", rule="R1")
    buffer.insert(1, "b", rule="R1")
    buffer.insert(2, "c", rule="R2")
    assert buffer.summary() == {"R1": 2, "R2": 1}


# ---------------------------------------------------------------------- #
# encoding round-trip
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", ["ascii only", "unicode: λ ± Ω →", "tab\tand\r\nCRLF"])
def test_source_round_trips_byte_for_byte(tmp_path, payload):
    path = tmp_path / "sample.tex"
    path.write_text(payload, encoding="utf-8")
    src = TexSource.from_path(path)
    assert src.encode() == path.read_bytes()


def test_latin1_fallback_round_trips(tmp_path):
    path = tmp_path / "legacy.tex"
    raw = b"caf\xe9 \\includegraphics{a.png}\n"  # invalid UTF-8
    path.write_bytes(raw)
    src = TexSource.from_path(path)
    assert src.encoding == "latin-1"
    assert src.encode() == raw
    assert len(list(src.finditer(r"\\includegraphics"))) == 1

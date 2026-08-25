"""LaTeX math -> MathML + spoken text, keyed by latex-lab's own hash.

Why the formulas are not scanned out of the ``.tex``
---------------------------------------------------

``texlex`` has no concept of math mode: ``environments()`` finds ``\\begin``
-delimited math and nothing else, which is roughly 6% of this corpus. The rest
is inline ``$…$``, and a ``$``-pairing pass would have to re-derive catcodes,
``\\$``, verbatim and macro-hidden delimiters to get it right.

The engine already knows. ``\\tagpdfsetup{math/mathml/write-dummy}`` makes
latex-lab emit *every formula it actually tagged* to
``<jobname>-mathml-dummy.html``, each with the source and the MD5 latex-lab
computed for it. That is ground truth, it covers inline math for free, and it
cannot disagree with what ends up in the PDF.

Why the hash is latex-lab's and not this package's
--------------------------------------------------

``scan`` identifies figures by ``sha256[:12]`` of the normalised source.
Math cannot use that: latex-lab looks its MathML up under
``g__math_mathml_<hash>_tl`` (``latex-lab-math.ltx``), so the only key that gets
ingested is the one *it* computed. Worklog ids become ``math-<md5[:12]>``; the
worklog entry grammar is prefix-agnostic, so nothing else changes.

What ships in the PDF
---------------------

``/Alt`` gets the spoken string; the MathML and the TeX source ride along as
associated files, which latex-lab attaches by itself (``math/mathml/AF`` and
``math/tex/AF`` are both on by default). Speech alone would mean turning those
*off*, so serving both audiences is the cheaper option as well as the better
one: a screen reader speaks the ``/Alt``, and a braille or TeX-literate reader
can still reach the notation.

Which engine says it
--------------------

MathCAT, vendored at ``vendor/MathCAT`` and driven through the small Rust
binary in ``mathspeech-driver/``. It replaced the Speech Rule Engine, which was
chosen first only because MathCAT ships as a Rust crate with no PyPI wheel and
no npm package -- an objection to the bindings, not to the engine. Building one
binary answers it, and MathCAT is what NVDA and JAWS actually speak this corpus
with.

The vendoring is a *fork* (``YingK8/MathCAT``, ``upstream`` pointing at
``daisy/MathCAT``) because upstream reads no ``mtable`` line attribute at all,
so ``[A|b]`` and ``[A b]`` come out identically. On a linear-algebra course that
is the difference between a system of equations and a 2 by 3 matrix, so the
fork carries one rule that says "augmented matrix". Rebasing is
``git -C vendor/MathCAT fetch upstream && git rebase upstream/main``.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..errors import LatexAllyError, MissingDependency

__all__ = [
    "DRIVER",
    "RULES_DIR",
    "Formula",
    "convert",
    "expand_macros",
    "normalise_speech",
    "read_dummy",
    "write_sources",
]

#: One `<div>` of the dummy file. latex-lab writes the source with `&` and `<`
#: escaped and nothing else, so `html.unescape` is exactly the right inverse.
#: The hash is UPPERCASE hex -- `\str_mdfive_hash:n` produces uppercase, and so
#: must any key we generate, or the lookup silently misses every formula.
_ENTRY = re.compile(
    r"<h2>\\mml\s*(?P<index>\d+)</h2>\s*"
    r"<p>(?P<tex>.*?)</p>\s*"
    r"<p>(?P<hash>[0-9A-Fa-f]+)\s*</p>",
    re.DOTALL,
)

#: Delimiters that carry no structure and must come off before conversion --
#: left on, the engine reads "dollar sign" aloud.
_DELIMITERS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (re.compile(r"\A\$\$(.*)\$\$\Z", re.DOTALL), True),
    (re.compile(r"\A\\\[(.*)\\\]\Z", re.DOTALL), True),
    (re.compile(r"\A\\begin\s*\{displaymath\}(.*)\\end\s*\{displaymath\}\Z", re.DOTALL), True),
    (re.compile(r"\A\$(.*)\$\Z", re.DOTALL), False),
    (re.compile(r"\A\\\((.*)\\\)\Z", re.DOTALL), False),
    (re.compile(r"\A\\begin\s*\{math\}(.*)\\end\s*\{math\}\Z", re.DOTALL), False),
)

#: Environments that must be handed to the converter **intact**. They are what
#: tells it that `&` is an alignment tab and `\\` is a row break; strip the
#: wrapper and `align` comes back as "y ampersand equals m x plus b" instead of
#: "Line 1: y equals m x plus b". This is not an edge case in this corpus --
#: `align`/`align*` outnumber `equation`/`equation*` roughly four to one.
_STRUCTURED = re.compile(
    r"\A\\begin\s*\{(?:align|alignat|gather|multline|eqnarray|flalign|equation|split)\*?\}"
)

#: `alignat` takes a column-count argument the converter does not understand: it
#: yields zero rows and every `&` is spoken as "ampersand". `align` renders the
#: same content correctly, and the count is presentational.
_ALIGNAT = re.compile(r"(\\(?:begin|end)\s*)\{alignat(\*?)\}(\s*\{\d+\})?")

#: `bmatrix*` takes an alignment option (`[r]`) the converter cannot parse; it
#: falls back to reading the whole thing as an `align`, so a matrix is announced
#: as "4 equations; equation 1; ...". The alignment is presentational.
_STARRED_MATRIX = re.compile(r"(\\(?:begin|end)\s*)\{([bBpvV]?matrix)\*\}(\s*\[[^\]]*\])?")

#: Course macros the converter has never heard of, so it passes them through as
#: literal text and the reader hears the macro *name*: "mat cap u is equal to".
#:
#: Measured over 861 formulas of real `write-dummy` output from 12 documents:
#: 284 of them -- **a third** -- carried at least one, and `\mat` alone
#: accounted for 591 occurrences. Nothing about that is engine-specific; the
#: MathML is already wrong before any speech engine sees it.
#:
#: Every expansion below is the definition the corpus itself gives, taken by
#: majority where semesters disagree. `\mat` is the one that mattered and the
#: one worth checking: 39 of its 47 live definitions are `\mathbf{#1}`, five say
#: `\begin{bmatrix}#1\end{bmatrix}` and three say `\begin{matrix}`. `\mathbf` is
#: also what the usage demands -- of 12,478 calls the top twelve are `\mat{A}`,
#: `\mat{V}`, `\mat{R}` and so on, single capitals naming a matrix. Expanding
#: those to a bracketed environment would have MathCAT announce "the 1 by 1
#: matrix cap a" twelve thousand times.
#:
#: Written as *prefix* rewrites wherever the macro takes one argument: replacing
#: `\mat{` with `\mathbf{` never touches the braces, so it cannot mis-nest on
#: `\mat{\vec{x}}` the way a `{([^{}]*)}` capture would.
_MACRO_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\\wt\s*\{"), r"\\tilde{"),
)

#: A `\mat` argument that is a matrix *body* rather than a matrix *name*.
#:
#: The corpus uses the macro both ways, and the semesters disagree about which
#: it is: 39 of 47 live definitions are `\mathbf{#1}`, five are
#: `\begin{bmatrix}#1\end{bmatrix}`. Measured over every call, 11,279
#: arguments are a name (`\mat{A}`, `\mat{V}`) and 1,199 are a body
#: (`\mat{| & | \\ \vec{a}_1 & \vec{a}_2 \\ | & |}`).
#:
#: Expanding a body to `\mathbf{...}` produces invalid MathML and MathCAT
#: refuses the whole formula, so it ships with no `/Alt` -- three of them in
#: notes_sp21/note23 alone. Expanding a name to a bracketed environment would
#: have the reader hear "the 1 by 1 matrix cap a" eleven thousand times.
#:
#: An `&` or a `\\` inside the argument can only be a body, so the content
#: decides and neither reading has to be guessed at.
_MATRIX_BODY = re.compile(r"&|\\\\")

#: The argument-less ones, and the unit macros `siunitx` provides. `\SI{5}{\ohm}`
#: is two arguments, so it is spelled out rather than prefixed.
_MACRO_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\\SI\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"\1 \2"),
    (re.compile(r"\\kohm\b"), r"\\mathrm{k\\Omega}"),
    (re.compile(r"\\ohm\b"), r"\\Omega"),
    (re.compile(r"\\milli\s*\\?volt\b|\\millivolt\b"), r"\\mathrm{mV}"),
    (re.compile(r"\\volt\b"), r"\\mathrm{V}"),
    (re.compile(r"\\meter\b"), r"\\mathrm{m}"),
    (re.compile(r"\\ampere\b"), r"\\mathrm{A}"),
    (re.compile(r"\\second\b"), r"\\mathrm{s}"),
    (re.compile(r"\\R\b"), r"\\mathbb{R}"),
    (re.compile(r"\\e\b(?![a-zA-Z])"), r"\\vec{e}"),
    (re.compile(r"\\dag\b"), r"\\dagger"),
    (re.compile(r"\\hdots\b"), r"\\cdots"),
    # Transpose. MathCAT recognises `^T` as the transpose intent and says
    # "transpose"; `^{\top}` reaches no such rule and comes out as the literal
    # "superscript top". The corpus writes it the second way 11,345 times, and
    # `\top` never appears outside a superscript there -- so in this material
    # it always means transpose, and saying so is not a guess.
    (re.compile(r"\^\s*\{\s*\\top\s*\}"), r"^{T}"),
    (re.compile(r"\^\s*\\top\b"), r"^{T}"),
    # A rule drawn across a table cell. It is decoration; in speech it is noise.
    (re.compile(r"\\horzbar\b"), ""),
    # Delimiter sizing. The converter leaves `\big\{` as literal text, so the
    # reader hears "backslash brace" instead of "open brace". Purely
    # presentational, and `\left`/`\right` are dropped for the same reason.
    (re.compile(r"\\(?:bB)?(?:big|Big|bigg|Bigg)[lrm]?\b\s*"), ""),
)

#: `aligned` is `align`'s inline-able twin, and the converter emits invalid
#: MathML for it -- MathCAT then refuses the whole formula and it ships with no
#: `/Alt`. `align*` produces the reading the author meant, nested inside
#: `equation*` or not. **[verified]** both ways.
_ALIGNED = re.compile(r"(\\(?:begin|end)\s*)\{aligned\}")

#: `flalign` is `align` stretched to the margins -- the trailing `&` on each row
#: is a spacing anchor, nothing more. The converter emits invalid MathML for it,
#: MathCAT refuses, and the formula ships with no `/Alt`. Caught by
#: ALLY-PDF-040 on a real build: one `flalign*` out of sp26/hw/5's 272 formulas.
_FLALIGN = re.compile(r"(\\(?:begin|end)\s*)\{flalign(\*?)\}")

#: `eqnarray` is `align`'s deprecated predecessor and the converter emits
#: invalid MathML for it, so the formula ships with no `/Alt` at all. 48 sites
#: in the live scope; it was the last ALLY-PDF-040 in sp26/hw/7.
#:
#: The two differ in column count: eqnarray is `lhs & rel & rhs`, align is
#: `lhs & rel-and-rhs`. So the middle separator of each row has to go, and only
#: the SECOND `&` of a row is the middle one.
_EQNARRAY = re.compile(r"(\\(?:begin|end)\s*)\{eqnarray(\*?)\}")
_EQNARRAY_BODY = re.compile(
    r"(\\begin\s*\{eqnarray\*?\})(.*?)(\\end\s*\{eqnarray\*?\})", re.DOTALL
)


def _collapse_eqnarray_columns(body: str) -> str:
    r"""Drop the middle column separator of every ``eqnarray`` row."""

    def one(match: re.Match[str]) -> str:
        rows = []
        for row in re.split(r"\\\\", match.group(2)):
            cells = row.split("&")
            # `lhs & rel & rhs` -> `lhs & rel rhs`; anything else is left alone
            # rather than guessed at.
            rows.append(f"{cells[0]}&{' '.join(cells[1:])}" if len(cells) == 3 else row)
        return match.group(1) + "\\\\".join(rows) + match.group(3)

    return _EQNARRAY_BODY.sub(one, body)


#: One-argument macros whose *closing* brace also has to change, so they cannot
#: be prefix rewrites. ``\norm{\vec{v}}`` is the shape that matters: a
#: ``{([^{}]*)}`` capture stops at the inner brace and leaves ``\norm`` behind.
#: The `qty` family is `physics`, which this corpus's preamble loads
#: (`\usepackage{physics}` in `sp26/preambleFa25.tex`); `norm` and `abs` are the
#: course's own. Longest name first is not needed -- each is matched whole, with
#: a following letter rejected, so `\normalsize` is not `\norm`.
_MACRO_FENCES: dict[str, tuple[str, str]] = {
    "norm": (r"\lVert ", r" \rVert"),
    "abs": (r"\lvert ", r" \rvert"),
    "bmqty": (r"\begin{bmatrix}", r"\end{bmatrix}"),
    "pmqty": (r"\begin{pmatrix}", r"\end{pmatrix}"),
    # Plain delimiters, not `\left(`/`\right)`: a `\left ... \right` pair
    # cannot cross a `\\` row break, and two of this corpus's `\pqty` calls
    # span one. Sized delimiters are presentational; the speech is identical.
    "pqty": ("(", ")"),
    "bqty": ("[", "]"),
    "vqty": ("|", "|"),
    "qty": ("(", ")"),
}

#: A matrix environment carrying a superscript or subscript. **This is the one
#: that costs the most.** ``latex2mathml`` emits an ``<msup>`` with the wrong
#: number of children for ``\begin{bmatrix}…\end{bmatrix}^{\top}``, and MathCAT
#: refuses it outright -- "msup should have 2 children" -- so a transposed
#: matrix gets no ``/Alt`` at all. On a linear-algebra course that is not an
#: edge case. Wrapping the environment in a brace group is enough to make the
#: MathML well-formed, and changes nothing about what it means.
_SCRIPTED_MATRIX = re.compile(
    r"\\begin\s*\{([bBpvV]?matrix)\}(.*?)\\end\s*\{\1\}(?=\s*[\^_])",
    re.DOTALL,
)


def _expand_fence(body: str, name: str, opener: str, closer: str) -> str:
    r"""Replace ``\name{…}`` with ``opener … closer``, matching braces properly.

    Scans rather than pattern-matches because the argument nests:
    ``\norm{\vec{v}}`` and ``\bmqty{\mathbf{S}}`` are both real corpus shapes.
    """
    marker = "\\" + name
    out: list[str] = []
    index = 0
    while True:
        found = body.find(marker, index)
        # `\normal` must not match `\norm`.
        while found != -1 and body[found + len(marker) : found + len(marker) + 1].isalpha():
            found = body.find(marker, found + 1)
        if found == -1:
            out.append(body[index:])
            return "".join(out)
        cursor = found + len(marker)
        while cursor < len(body) and body[cursor] in " \t\n":
            cursor += 1
        if cursor >= len(body) or body[cursor] != "{":
            out.append(body[index : found + len(marker)])
            index = found + len(marker)
            continue
        depth = 0
        end = cursor
        while end < len(body):
            char = body[end]
            if char == "\\":
                end += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if depth != 0:  # unbalanced source; leave it for ALLY-PDF-041 to report
            out.append(body[index : found + len(marker)])
            index = found + len(marker)
            continue
        out.append(body[index:found])
        out.append(opener + body[cursor + 1 : end] + closer)
        index = end + 1


def _expand_fence_by_content(body: str, name: str, choose) -> str:
    r"""Like :func:`_expand_fence`, but the delimiters depend on the argument.

    ``\mat`` is the only macro here that means two different things, and which
    one it means is legible from the argument -- see :data:`_MATRIX_BODY`.
    """
    marker = "\\" + name
    out: list[str] = []
    index = 0
    while True:
        found = body.find(marker, index)
        while found != -1 and body[found + len(marker) : found + len(marker) + 1].isalpha():
            found = body.find(marker, found + 1)
        if found == -1:
            out.append(body[index:])
            return "".join(out)
        cursor = found + len(marker)
        while cursor < len(body) and body[cursor] in " \t\n":
            cursor += 1
        if cursor >= len(body) or body[cursor] != "{":
            out.append(body[index : found + len(marker)])
            index = found + len(marker)
            continue
        depth = 0
        end = cursor
        while end < len(body):
            char = body[end]
            if char == "\\":
                end += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if depth != 0:  # unbalanced source; ALLY-PDF-041 reports what results
            out.append(body[index : found + len(marker)])
            index = found + len(marker)
            continue
        argument = body[cursor + 1 : end]
        opener, closer = choose(argument)
        out.append(body[index:found])
        out.append(opener + argument + closer)
        index = end + 1


#: Cross-reference bookkeeping inside display maths. These place a target for
#: ``\\ref``; they typeset nothing and mean nothing aloud. Left in, the converter
#: treats the key as content and a reader spells it out letter by letter:
#: `\\label{eq:vref}` is heard as "label e q colon v r e f" in the middle of an
#: equation. Measured on a 109-document sample: 25 formulas across 10 documents.
#:
#: The braces are matched non-greedily and the whole call is dropped, argument
#: and all -- unlike a macro whose argument is content, there is nothing here to
#: keep.
_BOOKKEEPING = re.compile(
    r"\\(?:label|nonumber|notag|tag\s*\*?|(?:this)?tagsuffix)\s*\{[^{}]*\}"
    r"|\\(?:nonumber|notag)\b"
)


#: Prose set BETWEEN the rows of an align, from mathtools. It carries the
#: author's reasoning -- "Subtract: (1) - 2*(2)", "Now we plug in (3)" -- and
#: 299 of them across 39 files in this corpus. latex-lab tags the whole align
#: as one Formula, so its /Alt replaces the subtree and the sentences are never
#: announced; the converter meanwhile reads the macro name as content and says
#: "backslash shortintertext, Subtract colon t w o", spelling the words out.
#:
#: `\\text` is the same thing said properly: measured through the real
#: converter, `\\text{Subtract: two}` speaks as "equation 2; Subtract: two".
_INTERTEXT = re.compile(r"\\(?:short)?intertext\s*\{")

#: A cross-reference inside maths. It typesets the equation's number, which is
#: not knowable at this layer -- the number is assigned by LaTeX during the
#: build, and this converts the source. Left in, the converter reads the macro
#: and its key as content: "eqref, e q n, colon o n e", and worse, its presence
#: inside a `\\text` breaks the text handling so the surrounding prose is
#: spelled out letter by letter too -- "w e g e t t h e u n i q u e s o l u t i
#: o n" is a real /Alt from fa20/hw/1.
#:
#: Said as words instead. "the referenced equation" is longer than "(1)" and
#: does not say which, but it is a phrase rather than a spelled-out key, and it
#: does not corrupt the sentence around it.
_EQREF = re.compile(r"\\(?:eq)?ref\s*\{[^{}]*\}")


def lift_intertext(body: str) -> str:
    r"""Turn ``\intertext``/``\shortintertext`` into ``\text`` so it is spoken.

    A rename, not a reflow: the prose stays exactly where the author put it, in
    the row order they wrote, and only the macro carrying it changes to one the
    converter understands.
    """
    return _INTERTEXT.sub(r"\\text{", body)


def spell_out_references(body: str) -> str:
    r"""Say ``\eqref{k}`` as words rather than spelling its key aloud."""
    return _EQREF.sub("the referenced equation", body)


def strip_bookkeeping(body: str) -> str:
    r"""Remove cross-reference machinery that has no spoken form.

    ``\label`` is the one that matters: it is common in this corpus, it is
    invisible on the page, and a reader announces its key one letter at a time.
    ``\nonumber`` and ``\notag`` are here for the same reason -- they control
    numbering, not content.
    """
    return _BOOKKEEPING.sub("", body)


def expand_macros(body: str) -> str:
    """Expand the course macros ``latex2mathml`` would otherwise read as text.

    Not a TeX expander and not trying to be: this is the same shape as
    :data:`_ALIGNAT`, a fixed list of constructs measured to break the
    converter on this corpus. Anything not on the list still reaches the reader
    as the macro's name, which ``ALLY-PDF-041`` reports.
    """
    body = spell_out_references(lift_intertext(strip_bookkeeping(body)))
    for pattern, replacement in _MACRO_PREFIXES:
        body = pattern.sub(replacement, body)
    if "\\mat" in body:
        body = _expand_fence_by_content(
            body,
            "mat",
            lambda arg: (
                ("\\begin{bmatrix}", "\\end{bmatrix}")
                if _MATRIX_BODY.search(arg)
                else ("\\mathbf{", "}")
            ),
        )
    for name, (opener, closer) in _MACRO_FENCES.items():
        if "\\" + name in body:
            body = _expand_fence(body, name, opener, closer)
    for pattern, replacement in _MACRO_WORDS:
        body = pattern.sub(replacement, body)
    body = _ALIGNED.sub(r"\1{align*}", body)
    body = _FLALIGN.sub(r"\1{align\2}", body)
    body = _EQNARRAY.sub(r"\1{align\2}", _collapse_eqnarray_columns(body))
    return _SCRIPTED_MATRIX.sub(r"{\\begin{\1}\2\\end{\1}}", body)


def _unwrap(tex: str) -> tuple[str, bool]:
    """Strip bare math delimiters. Returns ``(body, is_display)``.

    Structured environments are deliberately *not* stripped -- see
    ``_STRUCTURED``.
    """
    body = tex.strip()
    for pattern, display in _DELIMITERS:
        match = pattern.match(body)
        if match is not None:
            inner = match.group(1).strip()
            return inner, display or _STRUCTURED.match(inner) is not None
    if _STRUCTURED.match(body):
        return body, True
    return body, False


#: The repository root, three levels up from this module.
REPO_ROOT = Path(__file__).resolve().parents[3]
#: `cargo build --release` in `mathspeech-driver/`.
DRIVER = REPO_ROOT / "mathspeech-driver" / "target" / "release" / "latexally-mathspeech"
#: MathCAT loads its rules from disk. They are in the submodule, not unzipped
#: out of a build directory, which is the whole reason for vendoring rather
#: than depending on the published crate.
RULES_DIR = REPO_ROOT / "vendor" / "MathCAT" / "Rules"


@dataclass(slots=True)
class Formula:
    """One unique formula, however many times it occurs."""

    hash: str
    tex: str
    count: int = 1

    @property
    def id(self) -> str:
        """Worklog id. Prefixed so it cannot collide with a figure's."""
        return f"math-{self.hash[:12]}"

    @property
    def body(self) -> str:
        """The formula with its outermost delimiters removed."""
        return _unwrap(self.tex)[0]

    @property
    def display(self) -> bool:
        return _unwrap(self.tex)[1]


#: A control sequence with `@` in its name -- ``\if@eqnstar``, ``\@currentHref``,
#: ``\hyper@makecurrent``. Those are kernel and package internals, never author
#: maths: outside ``\makeatletter`` an author cannot even write one.
#:
#: They reach the dummy file because latex-lab's ``write-dummy`` captures the
#: *rendered source template*, and for a numbered ``eqnarray`` that template is
#: hyperref's expanded label machinery rather than the formula. Seven of this
#: corpus's 861 entries are that, and they are not equations at all.
#:
#: Dropping them is a correction, not a loss. They used to be rejected by
#: MathCAT as invalid MathML, which looked like a limitation; once the
#: ``eqnarray`` rewrite made them *convertible*, they started producing fluent
#: nonsense instead -- "table with 4 rows and 2 columns; row 1; column 1;
#: backslash if at sign e q n s t a r". A reader hears that as if it were the
#: formula. No ``/Alt`` and an ``ALLY-PDF-040`` is the honest outcome.
_INTERNAL_TEX = re.compile(r"\\[A-Za-z]*@")


def read_dummy(path: Path) -> list[Formula]:
    """Parse ``<jobname>-mathml-dummy.html`` into unique formulas.

    Deduplicated by hash, because the corpus repeats itself heavily -- the
    measured figure is 523,828 occurrences over 35,504 distinct strings. The
    count is kept so a worklog can sort by how much a description is worth.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    formulas: dict[str, Formula] = {}
    for match in _ENTRY.finditer(text):
        digest = match.group("hash")
        source = html.unescape(match.group("tex"))
        if _INTERNAL_TEX.search(source):
            continue
        existing = formulas.get(digest)
        if existing is not None:
            existing.count += 1
            continue
        formulas[digest] = Formula(digest, html.unescape(match.group("tex")).strip())
    return list(formulas.values())


def _to_mathml(formula: Formula) -> str:
    try:
        from latex2mathml.converter import convert as latex_to_mathml
    except ImportError as error:  # pragma: no cover - exercised by the doctor gate
        raise MissingDependency("latex2mathml", "math", "Math descriptions") from error
    body, display = _unwrap(formula.tex)
    body = _ALIGNAT.sub(r"\1{align\2}", body)
    body = _STARRED_MATRIX.sub(r"\1{\2}", body)
    body = expand_macros(body)
    return latex_to_mathml(body, display="block" if display else "inline")


#: Bumped whenever anything upstream of the speech string changes -- the macro
#: table, the engine, its preferences, the fork's rules. It is stored in the
#: cache and checked on read, because the cache key cannot see any of them.
RECIPE = "mathcat-0.7.5+augmented-matrix+temp-name/clearspeak/macros-5-intertext-label-eqref"

#: Not a valid MD5, so it can never collide with a formula's hash.
_RECIPE_KEY = "#recipe"


def _speak(pairs: list[tuple[str, str]], *, domain: str, timeout: int) -> dict[str, str]:
    """Hand every MathML string to one MathCAT process and read the speech back.

    One process for the whole batch, not one per formula: MathCAT loads 160
    rule files at startup, which at 35,504 unique formulas is the difference
    between seconds and hours. The JSON-Lines protocol is what makes that safe
    -- a formula MathCAT chokes on comes back as one ``error`` record instead of
    taking the batch down with it.
    """
    if not pairs:
        return {}
    if not DRIVER.is_file():
        raise LatexAllyError(
            "the math speech driver is not built",
            hint=(
                f"run `cargo build --release` in {DRIVER.parents[2]}; "
                "`latexally doctor` reports this as T011"
            ),
        )
    if not RULES_DIR.is_dir():
        raise LatexAllyError(
            "MathCAT's rules are missing",
            hint=f"run `git submodule update --init` in {REPO_ROOT}",
        )
    payload = "\n".join(json.dumps({"hash": h, "mathml": m}) for h, m in pairs)
    result = subprocess.run(
        [str(DRIVER), str(RULES_DIR), domain],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
        # The driver's own exit code is checked below, and a non-zero one is
        # reported with its stderr rather than raised as a CalledProcessError
        # that loses it.
        check=False,
    )
    if result.returncode != 0:
        raise LatexAllyError(
            "the speech driver failed",
            hint=(result.stderr or "").strip()[:400] or "no stderr",
        )
    speech: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "speech" in record:
            speech[record["hash"]] = record["speech"]
    return speech


def convert(
    formulas: list[Formula],
    *,
    cache: Path | None = None,
    domain: str = "ClearSpeak",
    timeout: int = 900,
) -> dict[str, tuple[str, str]]:
    """``hash -> (mathml, speech)`` for every formula that converted.

    The cache is what makes half a million occurrences tractable: it is keyed by
    latex-lab's hash, which is stable across file edits, renames and semester
    rollovers, so a rerun after changing one equation converts one equation.

    A formula that fails to convert is dropped rather than raised on. The check
    tier is what reports it -- ``ALLY-PDF-040`` fires on the missing ``/Alt``,
    which is a finding a human can act on, whereas a build that dies on one bad
    equation out of 35,504 is not.
    """
    known: dict[str, tuple[str, str]] = {}
    if cache is not None and cache.is_file():
        try:
            stored = json.loads(cache.read_text())
        except (OSError, json.JSONDecodeError):
            stored = {}
        # The key is latex-lab's hash of the *source*, which is exactly what
        # makes the cache survive edits and renames -- and exactly why it cannot
        # notice that the conversion itself changed. Expanding `\mat` to
        # `\mathbf` altered a third of this corpus's speech without altering one
        # hash, so a rerun would have served the old strings forever. The
        # recipe stamp is the cheap fix: a mismatch discards the file.
        if stored.get(_RECIPE_KEY) == RECIPE:
            known = {
                k: tuple(v)  # type: ignore[misc]
                for k, v in stored.items()
                if k != _RECIPE_KEY
            }

    pending = [f for f in formulas if f.hash not in known]
    mathml: dict[str, str] = {}
    for formula in pending:
        try:
            mathml[formula.hash] = _to_mathml(formula)
        except MissingDependency:
            raise
        except Exception:
            continue  # unconvertible source; ALLY-PDF-040 will report the gap

    spoken = _speak(sorted(mathml.items()), domain=domain, timeout=timeout)
    for digest, markup in mathml.items():
        # Whitespace-only speech is not a result. `$\\\\$` -- a line break that
        # ended up inside maths -- converts to a lone `<mspace linebreak>`, and
        # MathCAT is right to say nothing about it. Recording that as a success
        # writes an EMPTY token list, which makes `\tl_if_exist` true and ships
        # `/Alt ()`. An empty /Alt is worse than none: it satisfies a naive
        # "does every Formula have /Alt" check and says nothing to a reader.
        # Dropped here instead, so ALLY-PDF-040 reports the gap.
        if spoken.get(digest, "").strip():
            known[digest] = (markup, spoken[digest])

    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {_RECIPE_KEY: RECIPE}
        payload.update({k: list(v) for k, v in known.items()})
        cache.write_text(json.dumps(payload, indent=1))
    return known


def normalise_speech(text: str) -> str:
    r"""Fold speech into characters pdfTeX can actually write.

    MathCAT renders ``mathvariant="bold"`` by substituting the Unicode
    mathematical-bold letters themselves, so ``$\textbf{vector projection}$``
    comes back as ``\U0001D42F\U0001D41E...`` rather than as the words. Two
    things go wrong with that. It is useless as speech, and every one of those
    codepoints is four UTF-8 octets, which pdfTeX cannot encode -- the
    generated token list then leaks LaTeX's own error text into the PDF:

        Figure 1 shows ... known as \UTFvi ii@four@octets vector projection

    That shipped on page 1 of sp26/hw/2, in a build the tool reported as clean
    with zero errors, and no check saw it: they read the structure tree and the
    /Alt coverage, never the page. ``ALLY-PDF-033`` reads the page now.

    NFKC is the exact inverse of the substitution -- it maps every
    mathematical-alphanumeric back to its base letter -- and it was measured to
    clear all 8 non-ASCII characters MathCAT emits across this corpus's 861
    formulas. Anything still outside the BMP after that is dropped rather than
    written, because pdfTeX cannot encode it either; characters *inside* the
    BMP are left alone, since those it handles and they may be meaningful.
    """
    folded = unicodedata.normalize("NFKC", text)
    return "".join(char for char in folded if ord(char) <= 0xFFFF)


def _escape_tex_string(text: str) -> str:
    """Make a speech string safe as a PDF string inside an expl3 token list.

    Not `apply.escape_description`: that one escapes text on its way
    *into* a `.tex` body, where `_` and `^` are math shifts. This is a bare PDF
    string, where the only characters that matter are the ones that would end
    the argument or start a control sequence.
    """
    text = normalise_speech(text)
    for char in ("\\", "{", "}", "#", "%", "$", "&", "_", "^", "~"):
        text = text.replace(char, " ")
    # `~` and not " ": the generated table is read under \ExplSyntaxOn, where a
    # literal space is discarded. Emitting spaces produces a perfectly valid
    # /Alt that reads "thefractionwithnumerator..." to every screen reader.
    return "~".join(text.split())


def write_sources(
    results: dict[str, tuple[str, str]],
    formulas: list[Formula],
    stem: str,
    outdir: Path,
) -> tuple[Path, Path]:
    """Write the two files the next LaTeX run reads.

    ``<stem>-mathml.html`` is not a format of our choosing: latex-lab reads it
    by letting ``\\mml`` grab up to ``</h2>``, then ``<p>…</p>`` twice, then
    everything to ``<math`` -- and terminates on a *newline* followed by
    ``</div>``. The layout below is what that grammar accepts, and
    ``\\l__tag_math_mathml_files_clist`` already defaults to ``\\jobname-mathml``,
    so nothing has to be configured for it to be picked up.
    """
    outdir.mkdir(parents=True, exist_ok=True)

    lines = ["<!DOCTYPE html>", "<html>"]
    speech_lines = [
        "% Generated by latexally mathspeech. Do not edit; regenerate.",
        "\\ExplSyntaxOn",
    ]
    for index, formula in enumerate(formulas, start=1):
        found = results.get(formula.hash)
        if found is None:
            continue
        markup, speech = found
        lines += [
            "<div>",
            f"<h2>\\mml {index}</h2>",
            f"<p>{html.escape(formula.tex, quote=False)}</p>",
            f"<p>{formula.hash}</p>",
            markup,
            "</div>",
        ]
        speech_lines.append(
            f"\\tl_gset:cn {{ g__latexally_speech_{formula.hash} _tl }} "
            f"{{ {_escape_tex_string(speech)} }}"
        )
    lines.append("</html>")
    speech_lines.append("\\ExplSyntaxOff")

    mathml_path = outdir / f"{stem}-mathml.html"
    speech_path = outdir / f"{stem}-mathspeech.ltx"
    mathml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    speech_path.write_text("\n".join(speech_lines) + "\n", encoding="utf-8")
    return mathml_path, speech_path

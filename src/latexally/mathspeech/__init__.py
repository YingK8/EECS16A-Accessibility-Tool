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
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import LatexAllyError, MissingDependency

__all__ = ["Formula", "read_dummy", "convert", "write_sources", "REPO_NODE_MODULES"]

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
#: left on, SRE reads "dollar sign" aloud.
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


DRIVER = Path(__file__).with_name("speech.cjs")
#: `npm install` at the repository root, three levels up from this module.
REPO_NODE_MODULES = Path(__file__).resolve().parents[3] / "node_modules"


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
    return latex_to_mathml(body, display="block" if display else "inline")


def _speak(pairs: list[tuple[str, str]], *, domain: str, timeout: int) -> dict[str, str]:
    """Hand every MathML string to one SRE process and read the speech back."""
    if not pairs:
        return {}
    if shutil.which("node") is None:
        raise LatexAllyError(
            "node not found; math speech cannot be generated",
            hint="install Node 20+; `latexally doctor` reports this as T011",
        )
    if not (REPO_NODE_MODULES / "speech-rule-engine").is_dir():
        raise LatexAllyError(
            "speech-rule-engine is not installed",
            hint=f"run `npm install` in {REPO_NODE_MODULES.parent}",
        )
    payload = "\n".join(json.dumps({"hash": h, "mathml": m}) for h, m in pairs)
    result = subprocess.run(
        ["node", str(DRIVER), domain],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_NODE_MODULES.parent,
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
    domain: str = "clearspeak",
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
        known = {k: tuple(v) for k, v in json.loads(cache.read_text()).items()}  # type: ignore[misc]

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
        if digest in spoken:
            known[digest] = (markup, spoken[digest])

    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({k: list(v) for k, v in known.items()}, indent=1))
    return known


def _escape_tex_string(text: str) -> str:
    """Make a speech string safe as a PDF string inside an expl3 token list.

    Not `apply.escape_description`: that one escapes text on its way
    *into* a `.tex` body, where `_` and `^` are math shifts. This is a bare PDF
    string, where the only characters that matter are the ones that would end
    the argument or start a control sequence.
    """
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

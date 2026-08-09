"""Comment-aware, brace-balanced LaTeX source scanning.

Why this exists rather than a regex or an off-the-shelf parser
--------------------------------------------------------------
Three properties are non-negotiable for this project, and no single existing
library gives all three:

1. **Comment awareness.** In the EECS 16A corpus, 31% of ``\\includegraphics``
   call sites and 18% of ``circuitikz`` environments sit on commented-out lines.
   A regex that cannot see ``%`` will wrap dead code in a live environment,
   swallow the ``\\begin`` into the comment, and break the build. This is not
   hypothetical: it is why the previous tool's output was reverted.

2. **Byte-faithful round-trips.** We only ever splice into the original text at
   recorded offsets; we never reconstruct a file from a parse tree. Diffs stay
   minimal and reviewable, which matters because both TAs and LLM agents will
   read them.

3. **Tolerance of broken input.** The corpus contains ``\\usepackage{ams math}``
   (a real typo), hand-rolled ``\\def``\\ s that shadow kernel names, and
   ``\\font`` primitives. A parser that demands well-formed input is useless
   here; a scanner that classifies regions and balances braces is not.

The scanner classifies every character as code, comment, or verbatim, and all
searching happens over code regions only.

TeX rules implemented
---------------------
* ``%`` starts a comment **unless** escaped as ``\\%``. The comment consumes the
  newline as well (TeX does), which is why ``%`` at end of line joins lines.
* A control sequence is ``\\`` plus either a run of letters (a control *word*)
  or exactly one non-letter (a control *symbol*). Consuming these wholesale is
  what makes ``\\%``, ``\\{``, ``\\}`` and ``\\\\`` handled for free.
* ``verbatim``, ``lstlisting``, ``Verbatim``, ``minted``, ``alltt`` and friends
  suppress comment and brace interpretation entirely.
* ``\\verb<d>...<d>`` and ``\\lstinline<d>...<d>`` take an arbitrary delimiter.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ..errors import SourceError

__all__ = [
    "Region",
    "BraceGroup",
    "EnvSpan",
    "MacroCall",
    "TexSource",
    "VERBATIM_ENVIRONMENTS",
]


#: Environments whose bodies are read verbatim: no comments, no brace matching.
VERBATIM_ENVIRONMENTS: frozenset[str] = frozenset(
    {
        "verbatim",
        "verbatim*",
        "Verbatim",
        "Verbatim*",
        "BVerbatim",
        "LVerbatim",
        "lstlisting",
        "minted",
        "alltt",
        "listing",
        "filecontents",
        "filecontents*",
        "comment",
    }
)

#: Macros that take an arbitrary single-character delimiter instead of braces.
_DELIMITED_MACROS: frozenset[str] = frozenset({"verb", "lstinline", "mintinline"})

_LETTERS = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True, slots=True)
class Region:
    """A half-open character span ``[start, end)`` of a single classification."""

    start: int
    end: int
    kind: str  # "comment" | "verbatim"

    def __contains__(self, pos: int) -> bool:
        return self.start <= pos < self.end


@dataclass(frozen=True, slots=True)
class BraceGroup:
    """A balanced ``{...}`` group. ``inner`` excludes the braces themselves."""

    start: int  # index of the opening brace
    end: int  # index just past the closing brace
    inner_start: int
    inner_end: int

    @property
    def inner(self) -> slice:
        return slice(self.inner_start, self.inner_end)


@dataclass(frozen=True, slots=True)
class EnvSpan:
    """A ``\\begin{name}...\\end{name}`` pair, with nesting handled."""

    name: str
    start: int  # index of the backslash of \begin
    end: int  # index just past the closing brace of \end{name}
    body_start: int  # just past \begin{name} (and past its options, if consumed)
    body_end: int  # index of the backslash of \end
    options: str | None = None  # raw text of a leading [...] if present
    options_span: tuple[int, int] | None = None

    @property
    def body(self) -> slice:
        return slice(self.body_start, self.body_end)


@dataclass(frozen=True, slots=True)
class MacroCall:
    """A macro invocation with its parsed arguments."""

    name: str
    start: int  # index of the backslash
    end: int  # index just past the final consumed argument
    optional: tuple[str, ...] = ()
    optional_spans: tuple[tuple[int, int], ...] = ()
    required: tuple[str, ...] = ()
    required_spans: tuple[tuple[int, int], ...] = ()

    @property
    def name_end(self) -> int:
        return self.start + 1 + len(self.name)


class TexSource:
    """A single ``.tex``/``.sty``/``.cls`` file, scanned and queryable.

    Construct with :meth:`from_path` (which handles encoding) or directly from a
    string for tests.
    """

    __slots__ = (
        "text",
        "path",
        "encoding",
        "_comments",
        "_verbatims",
        "_masked",
        "_live_mask",
        "_line_starts",
    )

    def __init__(self, text: str, *, path: Path | None = None, encoding: str = "utf-8") -> None:
        self.text = text
        self.path = path
        self.encoding = encoding
        self._comments: list[Region] = []
        self._verbatims: list[Region] = []
        self._scan()
        # Two parallel views of the source, both offset-identical to `text`:
        #
        #   _masked     the source with comments and verbatim blanked to spaces,
        #               so a regex can only ever match live code, yet a match
        #               position still indexes correctly into `text`.
        #   _live_mask  one flag per character: True = live LaTeX, False =
        #               commented out or verbatim. Needed as a separate record
        #               because a blanked character is a space, and a space is
        #               indistinguishable from a real one by comparison alone.
        self._masked, self._live_mask = self._build_masked()
        self._line_starts = self._build_line_starts()

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #

    @classmethod
    def from_path(cls, path: Path | str) -> "TexSource":
        """Read a file, preserving its original encoding for later write-back.

        The corpus is mostly UTF-8 but contains a handful of legacy 8-bit files.
        Decoding with ``latin-1`` as a fallback is lossless for round-tripping
        because every byte maps to exactly one code point.
        """
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:  # pragma: no cover - filesystem dependent
            raise SourceError(f"cannot read {path}: {exc}") from exc
        for encoding in ("utf-8", "latin-1"):
            try:
                return cls(raw.decode(encoding), path=path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise SourceError(  # pragma: no cover - latin-1 never fails
            f"cannot decode {path}",
            hint="convert the file to UTF-8 and retry",
        )

    def encode(self, text: str | None = None) -> bytes:
        """Encode back using the original encoding, for byte-faithful writes."""
        return (self.text if text is None else text).encode(self.encoding)

    # ------------------------------------------------------------------ #
    # scanning
    # ------------------------------------------------------------------ #

    def _scan(self) -> None:
        text = self.text
        n = len(text)
        i = 0
        while i < n:
            ch = text[i]

            if ch == "\\":
                i = self._scan_control_sequence(i)
                continue

            if ch == "%":
                nl = text.find("\n", i)
                end = n if nl == -1 else nl + 1
                self._comments.append(Region(i, end, "comment"))
                i = end
                continue

            i += 1

    def _scan_control_sequence(self, i: int) -> int:
        """Consume one control sequence starting at ``i``; return the next index.

        Returning past the whole construct is what gives us escape handling for
        free: ``\\%`` is consumed here, so the ``%`` never reaches the comment
        branch.
        """
        text = self.text
        n = len(text)
        match = _LETTERS.match(text, i + 1)
        if match is None:
            # Control symbol: backslash plus exactly one character (or a lone
            # trailing backslash at EOF).
            return min(i + 2, n)

        name = match.group(0)
        after = match.end()

        if name == "begin":
            env = self._peek_environment_name(after)
            if env is not None and env[0] in VERBATIM_ENVIRONMENTS:
                return self._consume_verbatim_environment(i, env[0])
            return after

        if name in _DELIMITED_MACROS:
            return self._consume_delimited(after)

        return after

    def _peek_environment_name(self, pos: int) -> tuple[str, int] | None:
        """Read ``{name}`` at ``pos``, skipping whitespace. Returns (name, end)."""
        text = self.text
        j = pos
        while j < len(text) and text[j] in " \t":
            j += 1
        if j >= len(text) or text[j] != "{":
            return None
        close = text.find("}", j + 1)
        if close == -1:
            return None
        return text[j + 1 : close], close + 1

    def _consume_verbatim_environment(self, begin_index: int, name: str) -> int:
        """Mark ``\\begin{name}...\\end{name}`` verbatim and skip past it."""
        text = self.text
        closer = f"\\end{{{name}}}"
        end = text.find(closer, begin_index)
        if end == -1:
            # Unterminated verbatim: treat the rest of the file as verbatim
            # rather than silently mis-scanning what follows.
            self._verbatims.append(Region(begin_index, len(text), "verbatim"))
            return len(text)
        stop = end + len(closer)
        self._verbatims.append(Region(begin_index, stop, "verbatim"))
        return stop

    def _consume_delimited(self, pos: int) -> int:
        """Consume ``\\verb``-style ``<delim>...<delim>`` starting after the name."""
        text = self.text
        n = len(text)
        j = pos
        if j < n and text[j] == "*":
            j += 1
        # \lstinline accepts a bracketed option list first.
        if j < n and text[j] == "[":
            depth = 0
            while j < n:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
        if j >= n:
            return n
        delim = text[j]
        # Brace form: \lstinline{...}
        if delim == "{":
            close = text.find("}", j + 1)
            stop = n if close == -1 else close + 1
        else:
            close = text.find(delim, j + 1)
            stop = n if close == -1 else close + 1
        self._verbatims.append(Region(j, stop, "verbatim"))
        return stop

    def _build_masked(self) -> tuple[str, bytearray]:
        live = bytearray(b"\x01") * len(self.text)
        if not self._comments and not self._verbatims:
            return self.text, live
        buf = list(self.text)
        for region in (*self._comments, *self._verbatims):
            for k in range(region.start, region.end):
                live[k] = 0
                if buf[k] != "\n":
                    # Keep newlines so line numbers and `^`/`$` anchors survive.
                    buf[k] = " "
        return "".join(buf), live

    def _build_line_starts(self) -> list[int]:
        starts = [0]
        for match in re.finditer(r"\n", self.text):
            starts.append(match.end())
        return starts

    # ------------------------------------------------------------------ #
    # region queries
    # ------------------------------------------------------------------ #

    @property
    def comments(self) -> Sequence[Region]:
        return tuple(self._comments)

    @property
    def verbatims(self) -> Sequence[Region]:
        return tuple(self._verbatims)

    @property
    def masked(self) -> str:
        """Source with comments and verbatim blanked out, offsets preserved."""
        return self._masked

    def is_code(self, pos: int) -> bool:
        """True when ``pos`` is live LaTeX (not commented out, not verbatim)."""
        if pos < 0 or pos >= len(self.text):
            return False
        return bool(self._live_mask[pos])

    def is_commented(self, pos: int) -> bool:
        return any(pos in region for region in self._comments)

    def line_of(self, pos: int) -> int:
        """1-based line number containing ``pos``."""
        return bisect_right(self._line_starts, pos)

    def column_of(self, pos: int) -> int:
        """1-based column number of ``pos``."""
        return pos - self._line_starts[self.line_of(pos) - 1] + 1

    def line_text(self, line: int) -> str:
        start = self._line_starts[line - 1]
        end = self._line_starts[line] if line < len(self._line_starts) else len(self.text)
        return self.text[start:end].rstrip("\n")

    # ------------------------------------------------------------------ #
    # searching
    # ------------------------------------------------------------------ #

    def finditer(self, pattern: str | re.Pattern[str]) -> Iterator[re.Match[str]]:
        """Iterate regex matches over *code only*.

        Match objects index into the masked text, whose offsets are identical to
        ``self.text``. Use ``self.text[m.start():m.end()]`` to recover the true
        source, which matters when a match spans a line-ending comment.
        """
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        return compiled.finditer(self._masked)

    def search(self, pattern: str | re.Pattern[str], pos: int = 0) -> re.Match[str] | None:
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        return compiled.search(self._masked, pos)

    # ------------------------------------------------------------------ #
    # brace / bracket balancing
    # ------------------------------------------------------------------ #

    def match_group(self, pos: int, *, skip_whitespace: bool = True) -> BraceGroup | None:
        """Read a balanced ``{...}`` starting at or just after ``pos``.

        Escaped braces and braces inside comments or verbatim are ignored, which
        is exactly what a naive ``[^{}]*`` regex gets wrong on alt text such as
        ``{A {nested} description}``.
        """
        return self._match_delimited(pos, "{", "}", skip_whitespace=skip_whitespace)

    def match_optional(self, pos: int, *, skip_whitespace: bool = True) -> BraceGroup | None:
        """Read a balanced ``[...]`` starting at or just after ``pos``."""
        return self._match_delimited(pos, "[", "]", skip_whitespace=skip_whitespace)

    def _match_delimited(
        self, pos: int, opener: str, closer: str, *, skip_whitespace: bool
    ) -> BraceGroup | None:
        text = self.text
        masked = self._masked
        n = len(text)
        j = pos
        if skip_whitespace:
            while j < n and text[j] in " \t\r\n":
                j += 1
        if j >= n or masked[j] != opener:
            return None
        depth = 0
        k = j
        while k < n:
            ch = masked[k]
            if ch == "\\":
                # Skip the escaped character so \{ and \} never move `depth`.
                k += 2
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return BraceGroup(start=j, end=k + 1, inner_start=j + 1, inner_end=k)
            k += 1
        return None

    # ------------------------------------------------------------------ #
    # structural queries
    # ------------------------------------------------------------------ #

    def environments(self, name: str | Iterable[str]) -> list[EnvSpan]:
        """Find every live ``\\begin{name}...\\end{name}``, handling nesting.

        Nested same-name environments (``tikzpicture`` inside ``tikzpicture``)
        return only the outermost span, which is what an alt-text wrapper wants.
        """
        names = {name} if isinstance(name, str) else set(name)
        alternation = "|".join(re.escape(candidate) for candidate in sorted(names))
        pattern = re.compile(
            r"\\(begin|end)\s*\{(" + alternation + r")\}",
            re.DOTALL,
        )

        spans: list[EnvSpan] = []
        stack: list[tuple[str, int, int]] = []  # (name, begin_start, body_start)
        for match in self.finditer(pattern):
            kind, env = match.group(1), match.group(2)
            if kind == "begin":
                stack.append((env, match.start(), match.end()))
                continue
            # Unwind to the matching begin; tolerate crossed environments.
            while stack:
                open_env, begin_start, body_start = stack.pop()
                if open_env != env:
                    continue
                if not stack:  # outermost only
                    options = self.match_optional(body_start)
                    spans.append(
                        EnvSpan(
                            name=env,
                            start=begin_start,
                            end=match.end(),
                            body_start=options.end if options else body_start,
                            body_end=match.start(),
                            options=(
                                self.text[options.inner] if options else None
                            ),
                            options_span=(
                                (options.start, options.end) if options else None
                            ),
                        )
                    )
                break
        spans.sort(key=lambda span: span.start)
        return spans

    def macro_calls(
        self,
        name: str,
        *,
        optional: int = 0,
        required: int = 0,
        star: bool = False,
    ) -> list[MacroCall]:
        """Find calls to ``\\name`` and parse a fixed argument signature.

        ``optional`` is the maximum number of leading ``[...]`` groups to
        consume; ``required`` the number of ``{...}`` groups. Arguments that are
        absent simply do not appear in the result, so a call site with fewer
        arguments than declared is reported rather than skipped.
        """
        pattern = re.compile(r"\\" + re.escape(name) + (r"\*?" if star else "") + r"(?![A-Za-z@])")
        calls: list[MacroCall] = []
        for match in self.finditer(pattern):
            cursor = match.end()
            opt_values: list[str] = []
            opt_spans: list[tuple[int, int]] = []
            for _ in range(optional):
                group = self.match_optional(cursor)
                if group is None:
                    break
                opt_values.append(self.text[group.inner])
                opt_spans.append((group.start, group.end))
                cursor = group.end
            req_values: list[str] = []
            req_spans: list[tuple[int, int]] = []
            for _ in range(required):
                group = self.match_group(cursor)
                if group is None:
                    break
                req_values.append(self.text[group.inner])
                req_spans.append((group.start, group.end))
                cursor = group.end
            calls.append(
                MacroCall(
                    name=name,
                    start=match.start(),
                    end=cursor,
                    optional=tuple(opt_values),
                    optional_spans=tuple(opt_spans),
                    required=tuple(req_values),
                    required_spans=tuple(req_spans),
                )
            )
        return calls

    # ------------------------------------------------------------------ #
    # normalisation, for content-addressed identity
    # ------------------------------------------------------------------ #

    def normalised(self, start: int = 0, end: int | None = None) -> str:
        """Comment-stripped, whitespace-collapsed text of a span.

        This is the input to the content hash that gives figures and equations a
        stable identity across file edits, file renames and semester rollovers.
        Two figures that differ only in indentation or in a stripped comment must
        hash identically, or the catalog fragments and every description has to
        be rewritten each term.
        """
        end = len(self.text) if end is None else end
        chunks: list[str] = []
        cursor = start
        for region in sorted(self._comments, key=lambda r: r.start):
            if region.end <= start or region.start >= end:
                continue
            chunks.append(self.text[cursor : max(cursor, region.start)])
            cursor = max(cursor, region.end)
        chunks.append(self.text[cursor:end])
        return re.sub(r"\s+", " ", "".join(chunks)).strip()

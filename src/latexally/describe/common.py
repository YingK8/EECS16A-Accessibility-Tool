"""Shared parsing helpers for the deterministic describers.

Everything in ``describe/`` is rule-based and reproducible: the same source
always yields the same skeleton, with no model in the loop. That is a hard
requirement of this project, and it is also what makes the output reviewable --
a TA can check a description against the source line by line.

What these describers produce is a **skeleton of facts**, not finished alt text.
They can state that a plot has axes ``a`` and ``b`` over 0 to 9 and four points
at (2,2), (4,6), (6,7), (8,8). They cannot state what the figure is *for*. The
worklog keeps the two clearly separated so nobody ships a skeleton by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Skeleton", "split_top_level", "parse_options", "latex_to_text", "format_number"]


@dataclass(slots=True)
class Skeleton:
    """Machine-derived facts about one figure."""

    genre: str
    summary: str = ""
    details: list[str] = field(default_factory=list)
    #: Tabular data worth emitting as a real table rather than prose.
    table: list[tuple[str, ...]] = field(default_factory=list)
    table_header: tuple[str, ...] = ()
    confidence: str = "low"  # high | medium | low
    #: What a human still has to supply, in plain language.
    needs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "genre": self.genre,
            "summary": self.summary,
            "details": self.details,
            "table": [list(row) for row in self.table],
            "table_header": list(self.table_header),
            "confidence": self.confidence,
            "needs": self.needs,
        }

    def as_text(self) -> str:
        parts = [self.summary] if self.summary else []
        parts.extend(self.details)
        return " ".join(part.rstrip(".") + "." for part in parts if part)


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on a separator that is not inside braces, brackets or parentheses.

    TikZ option lists nest freely (``label={[red]above:$V_1$}``), so a plain
    ``str.split(",")`` shreds them.
    """
    pieces: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        if char == separator and depth <= 0:
            pieces.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        pieces.append("".join(current).strip())
    return [piece for piece in pieces if piece]


def parse_options(text: str | None) -> dict[str, str]:
    """Parse a TikZ/pgfplots ``key=value, flag`` list into a dict.

    Bare flags map to the empty string, so ``only marks`` is detectable as a
    key. Values keep their braces stripped one level.
    """
    if not text:
        return {}
    options: dict[str, str] = {}
    for piece in split_top_level(text):
        key, sep, value = piece.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip() if sep else ""
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1].strip()
        options[key] = value
    return options


_MATH_REPLACEMENTS = (
    (re.compile(r"\\(?:mathbf|mathrm|mathit|textbf|textit|text|mbox|vec|mat|bm)\s*\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"\1 over \2"),
    (re.compile(r"\\sqrt\s*\{([^{}]*)\}"), r"square root of \1"),
    (re.compile(r"\\(?:left|right)\b"), ""),
    (re.compile(r"\\[a-zA-Z]+"), " "),
    (re.compile(r"[{}$]"), ""),
    # Component and axis labels are read best as written: "C1", "S1", "V_BB,min"
    # -> "C1", "S1", "VBB,min". Spelling it "C sub 1" is correct for a formula
    # and wrong for a part name, and this helper only ever sees short labels --
    # equations go through the separate math-speech pipeline.
    (re.compile(r"\s*_\s*\{?([A-Za-z0-9,]+)\}?"), r"\1"),
    (re.compile(r"\s*\^\s*\{?([A-Za-z0-9]+)\}?"), r" to the \1"),
)


def latex_to_text(fragment: str | None) -> str:
    """Best-effort plain-text rendering of a short LaTeX fragment.

    Used for axis labels and node text, which are almost always simple maths
    like ``$V_{C_1}$``. ``/Alt`` is a plain PDF string, so a screen reader would
    otherwise announce "dollar backslash v sub c one dollar".

    Deliberately conservative: it never guesses at complex expressions, and the
    worklog always shows the original source next to the result so a human can
    correct it.
    """
    if not fragment:
        return ""
    text = fragment
    for pattern, replacement in _MATH_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def format_number(value: str) -> str:
    """Trim trailing zeros so ``2.0`` reads as ``2``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value.strip()
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"

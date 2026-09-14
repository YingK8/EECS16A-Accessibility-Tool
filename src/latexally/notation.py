r"""Course notation: conventions a profile declares, checked and applied.

Not accessibility rules. A course that writes vectors bold, conjugates as
``^*`` and discrete-time signals with brackets says so under ``notation:`` in
its profile. ``check`` reports every departure as ``ALLY-FMT-<id>``; ``build``
fixes them in the mirror, never in the corpus.

Every rule is confined to maths. ``a(n)`` in prose is an article, not a signal.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .check.rules import _DISPLAY_ENVS, Finding, Severity, inline_math_spans
from .config import NotationRule
from .texlex import EditBuffer, TexSource

__all__ = [
    "FilePlan",
    "apply_files",
    "apply_notation",
    "check_notation",
    "math_spans",
    "plan_files",
    "rule_name",
    "sites",
]

#: Maths other than `$...$`, which cannot be a regex: see inline_math_spans.
# ponytail: `\text{...}` inside maths still counts as maths; exclude it if a rule
# ever needs to.
_DELIMITED_MATH = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)"
    r"|\\begin\s*\{(" + _DISPLAY_ENVS + r")(\*?)\}.*?\\end\s*\{\1\2\}",
    re.DOTALL,
)

#: The argument of `\vec x`, written without braces.
_TOKEN = re.compile(r"\s*(\\[A-Za-z]+|[^\s{}\\%$])")

#: An argument that needs no parentheses: `z`, `\psi`, `\hat{\psi}`.
_ATOM = re.compile(r"\A\s*(?:[A-Za-z0-9]|\\[A-Za-z]+(?:\s*\{[^{}]*\})?)\s*\Z")


def rule_name(rule: NotationRule) -> str:
    return f"ALLY-FMT-{rule.id}"


def math_spans(source: TexSource) -> list[tuple[int, int]]:
    """``(start, end)`` of every maths region, comments and verbatim excluded."""
    spans = inline_math_spans(source.masked)
    spans += [match.span() for match in _DELIMITED_MATH.finditer(source.masked)]
    return spans


def _expand(template: str, match: re.Match[str], text: str) -> str:
    r"""``\1`` in ``template`` -> group 1, read from the unmasked ``text``.

    Not ``match.expand``: that treats ``\e`` in ``\ell_\1`` as an escape.
    """

    def group(ref: re.Match[str]) -> str:
        start, end = match.span(int(ref.group(1)))
        return text[start:end] if start >= 0 else ""

    return re.sub(r"\\(\d)", group, template)


def _candidates(source: TexSource, rule: NotationRule) -> Iterator[tuple[int, int, str]]:
    if rule.pattern is not None:
        for match in rule.pattern.finditer(source.masked):
            yield match.start(), match.end(), _expand(rule.to, match, source.text)
        return
    names = "|".join(re.escape(name) for name in rule.macros)
    for match in source.finditer(re.compile(r"\\(?:" + names + r")(?![A-Za-z@])")):
        group = source.match_group(match.end())
        if group is not None:
            arg, end = source.text[group.inner], group.end
        else:
            token = _TOKEN.match(source.masked, match.end())
            if token is None:
                continue
            arg, end = token.group(1), token.end()
        if rule.parenthesize and not _ATOM.match(arg):
            arg = f"({arg})"
        yield match.start(), end, rule.to.replace("{arg}", arg)


def sites(source: TexSource, rule: NotationRule) -> list[tuple[int, int, str]]:
    """``(start, end, replacement)`` for every site of ``rule`` inside maths."""
    spans = math_spans(source)
    return [
        site
        for site in _candidates(source, rule)
        if any(lo <= site[0] and site[1] <= hi for lo, hi in spans)
    ]


def check_notation(
    source: TexSource, rules: Iterable[NotationRule], name: str
) -> list[Finding]:
    findings = []
    for rule in rules:
        for start, end, replacement in sites(source, rule):
            found = source.text[start:end]
            findings.append(
                Finding(
                    rule=rule_name(rule),
                    severity=Severity.WARNING,
                    message=f"{rule.message or 'course notation'}: {found!r}",
                    file=name,
                    line=source.line_of(start),
                    standard="course notation (profile), not a WCAG or PDF/UA rule",
                    hint=f"write {replacement!r}"
                    + (". `build` applies this in the mirror" if rule.fix else ""),
                    data={"found": found, "fix": replacement},
                )
            )
    return findings


def apply_notation(
    text: str, rules: Iterable[NotationRule]
) -> tuple[str, dict[str, int]]:
    r"""``text`` with every ``fix`` rule applied, and ``rule -> sites``.

    One rule at a time, rescanning after each, so ``\overline{\vec{x}}`` gets
    both fixes without two edits overlapping.
    """
    counts: dict[str, int] = {}
    for rule in rules:
        if not rule.fix:
            continue
        buffer = EditBuffer()
        end = -1
        for start, stop, replacement in sites(TexSource(text), rule):
            # ponytail: the same rule nested in itself (`\vec{\vec{x}}`) fixes the
            # outer site only; loop to a fixed point if that ever matters.
            if start < end:
                continue
            buffer.replace(start, stop, replacement, rule=rule_name(rule))
            end = stop
        if buffer:
            text = buffer.apply(text)
            counts[rule_name(rule)] = len(buffer)
    return text, counts


@dataclass(slots=True)
class FilePlan:
    """What the rules would do to one file, before anything is written."""

    path: Path
    source: TexSource
    updated: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.updated != self.source.text

    def diff(self, *, context: int = 3) -> str:
        return "".join(
            difflib.unified_diff(
                self.source.text.splitlines(keepends=True),
                self.updated.splitlines(keepends=True),
                fromfile=f"a/{self.path.name}",
                tofile=f"b/{self.path.name}",
                n=context,
            )
        )

    def write(self) -> bool:
        """Apply to disk, in the file's own encoding. Returns whether it changed."""
        if not self.changed:
            return False
        self.path.write_bytes(self.source.encode(self.updated))
        return True


def plan_files(paths: Iterable[Path], rules: Iterable[NotationRule]) -> list[FilePlan]:
    """Every file these rules would change, with its diff. Writes nothing.

    Files that cannot be read are skipped rather than raised on: a corpus of
    17,677 files has a few that are not valid UTF-8, and one of them must not
    stop the rest from being fixed.
    """
    rules = tuple(rules)
    plans: list[FilePlan] = []
    for path in paths:
        try:
            source = TexSource.from_path(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        updated, counts = apply_notation(source.text, rules)
        plan = FilePlan(Path(path), source, updated, counts)
        if plan.changed:
            plans.append(plan)
    return plans


def apply_files(paths: Iterable[Path], rules: Iterable[NotationRule]) -> dict[str, int]:
    """Apply ``rules`` to each file in place, returning ``rule -> sites``.

    The build passes mirror paths; ``latexally notation --write`` passes corpus
    paths, behind a clean-worktree guard.
    """
    counts: dict[str, int] = {}
    for plan in plan_files(paths, rules):
        plan.write()
        for rule, sites_fixed in plan.counts.items():
            counts[rule] = counts.get(rule, 0) + sites_fixed
    return counts

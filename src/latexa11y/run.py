"""What one conversion run is: its scope, its choices, and where its output goes.

This module exists so that "convert these homeworks, keep these styles, put the
outputs there" is a *value* rather than a sequence of commands. The TUI's only
job is to build a :class:`RunConfig`; the build engine's only job is to consume
one. Neither knows the other exists, and so the interactive path and the
scripted path cannot drift apart -- which is exactly what happened while the
pipeline lived in ``examples/build-corpus.sh`` and the style choices lived in
LaTeX macros a human typed by hand.

A run config round-trips through YAML, so:

* the TUI writes ``run.yaml`` next to its output and a rerun is one command;
* CI and LLM agents drive the same code path a person does;
* the TUI is testable by asserting on the config it produces, with no terminal.

**Every option here names a cost.** An accessibility toggle that silently
repaginates a document is how staff lose trust in the tool, so the cost is
carried in the data model (:attr:`Toggle.cost`) rather than buried in prose that
only the author ever reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from .config import Profile
from .errors import ConfigError

__all__ = [
    "Toggle",
    "STANDARD_TOGGLES",
    "Standards",
    "ColorChoice",
    "AltChoice",
    "Output",
    "RunConfig",
    "Assignment",
    "discover_assignments",
    "find_driver",
]

WRITE_MODES = ("mirror", "in-place")
COLOR_MODES = ("conforming", "house")
ALT_MODES = ("worklog", "placeholders", "off")


# ---------------------------------------------------------------------- #
# standards
# ---------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Toggle:
    """One accessibility standard the run may apply, and what it costs.

    ``cost`` is shown verbatim in the TUI next to the checkbox. "None measurable"
    is a claim backed by a number elsewhere in the repo; where a toggle really
    does move the page, it says so, because a staff member who discovers that
    for themselves stops believing the rest of the tool.
    """

    key: str
    label: str
    default: bool
    cost: str
    detail: str


#: Declared once, consumed by the dataclass below, the TUI and the docs. Adding
#: a standard means adding a row here and a branch in ``build.preamble_for``.
STANDARD_TOGGLES: tuple[Toggle, ...] = (
    Toggle(
        "tagging",
        "Tag structure (PDF/UA)",
        True,
        "~2.6% of pixels move",
        "The structure tree itself: headings, figures, tables, reading order. "
        "Everything else here is worthless without it. The cost is repagination "
        "from tagging's own spacing, and is unavoidable on any route to a "
        "tagged PDF.",
    ),
    Toggle(
        "retrofit",
        "Course macro retrofit",
        True,
        "none measurable (0.002%)",
        "Patches the course's own .sty in place rather than replacing it, so "
        "the document keeps its exact look. Measured against sp26/hw/9.",
    ),
    Toggle(
        "bookmarks",
        "PDF bookmark outline",
        True,
        "none",
        "The navigable tree in a reader's sidebar (WCAG 2.1 AA, technique "
        "PDF2). Tagging never writes /Outlines, so this is separate work.",
    ),
    Toggle(
        "question_tags",
        "Question headings as real H2 tags",
        False,
        "reflows 1 question in 5",
        "A heading tag may not sit inside a paragraph, so this forces a \\par "
        "after each question title. Measured: 74 of 362 \\qns calls in the live "
        "question bank (20%) are followed immediately by text. Off by default, "
        "which keeps the page identical and still puts every question in the "
        "bookmark tree.",
    ),
    Toggle(
        "unicode_map",
        "Extractable text (ToUnicode)",
        True,
        "none",
        "Makes Type 1 font text -- including the Dunhill masthead -- copyable "
        "and searchable rather than mojibake.",
    ),
)

_TOGGLES_BY_KEY = {toggle.key: toggle for toggle in STANDARD_TOGGLES}


@dataclass(slots=True)
class Standards:
    """Which accessibility standards this run applies, and which it leaves alone."""

    tagging: bool = True
    retrofit: bool = True
    bookmarks: bool = True
    question_tags: bool = False
    unicode_map: bool = True

    @classmethod
    def defaults(cls) -> "Standards":
        return cls(**{toggle.key: toggle.default for toggle in STANDARD_TOGGLES})

    def enabled(self) -> tuple[str, ...]:
        return tuple(t.key for t in STANDARD_TOGGLES if getattr(self, t.key))

    def toggle(self, key: str) -> None:
        if key not in _TOGGLES_BY_KEY:
            raise ConfigError(
                f"unknown standard {key!r}",
                hint="known: " + ", ".join(_TOGGLES_BY_KEY),
            )
        setattr(self, key, not getattr(self, key))

    def as_dict(self) -> dict:
        return {toggle.key: getattr(self, toggle.key) for toggle in STANDARD_TOGGLES}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Standards":
        data = data or {}
        unknown = set(data) - set(_TOGGLES_BY_KEY)
        if unknown:
            raise ConfigError(
                f"unknown standards: {', '.join(sorted(unknown))}",
                hint="known: " + ", ".join(_TOGGLES_BY_KEY),
            )
        merged = {t.key: bool(data.get(t.key, t.default)) for t in STANDARD_TOGGLES}
        return cls(**merged)


# ---------------------------------------------------------------------- #
# colour, descriptions, output
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class ColorChoice:
    """Whether to remap course colours that fail WCAG contrast.

    ``house`` keeps the course's originals. It is a supported choice and not a
    mistake -- a document may be recoloured downstream, or printed -- but it is
    the one setting that can leave a run non-conforming while everything else
    passes, so ``describe`` says so plainly.
    """

    mode: str = "conforming"
    #: Extra name -> hex overrides layered on top of the profile's own map.
    overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in COLOR_MODES:
            raise ConfigError(
                f"unknown colour mode {self.mode!r}",
                hint="use one of: " + ", ".join(COLOR_MODES),
            )

    def replacements(self, profile: Profile) -> dict[str, str]:
        if self.mode == "house":
            return {}
        return {**profile.colors.replace, **self.overrides}

    def describe(self, profile: Profile) -> str:
        if self.mode == "house":
            return "course originals kept (may fail WCAG 1.4.3)"
        count = len(self.replacements(profile))
        return f"conforming palette ({count} remapped)"

    def as_dict(self) -> dict:
        return {"mode": self.mode, "overrides": dict(self.overrides)}

    @classmethod
    def from_dict(cls, data: dict | None) -> "ColorChoice":
        data = data or {}
        return cls(
            mode=str(data.get("mode", "conforming")),
            overrides={str(k): str(v) for k, v in (data.get("overrides") or {}).items()},
        )


@dataclass(slots=True)
class AltChoice:
    """How far the run goes towards figure descriptions.

    ``worklog``      scan figures and write the Markdown worklog; sources untouched.
    ``placeholders`` also inject ``<<TODO:id>>`` markers at each figure, so a TA
                     editing the .tex sees what still needs writing.
    ``off``          skip figure scanning entirely.

    Placeholders are safe only because they are *build-failing*: latexa11y-core
    raises a hard LaTeX error on any placeholder string under strict mode. That
    is the whole reason the option can exist. The previous generation of this
    tooling injected the same markers with no such guard, and an unfilled one
    shipped into a PDF as real /Alt -- passing both a naive "every Figure has
    /Alt" check and veraPDF, producing a silent false claim of conformance.
    """

    mode: str = "worklog"
    #: False downgrades the placeholder error to a warning. Draft builds only.
    strict: bool = True

    def __post_init__(self) -> None:
        if self.mode not in ALT_MODES:
            raise ConfigError(
                f"unknown description mode {self.mode!r}",
                hint="use one of: " + ", ".join(ALT_MODES),
            )

    @property
    def scans(self) -> bool:
        return self.mode in ("worklog", "placeholders")

    @property
    def injects(self) -> bool:
        return self.mode == "placeholders"

    def describe(self) -> str:
        if self.mode == "off":
            return "not scanned"
        if self.mode == "worklog":
            return "worklog only, no source edits"
        return "placeholders injected" + ("" if self.strict else " (NOT strict — draft only)")

    def as_dict(self) -> dict:
        return {"mode": self.mode, "strict": self.strict}

    @classmethod
    def from_dict(cls, data: dict | None) -> "AltChoice":
        data = data or {}
        return cls(mode=str(data.get("mode", "worklog")), strict=bool(data.get("strict", True)))


@dataclass(slots=True)
class Output:
    """Where everything this run produces is written.

    ``mirror``   the corpus is strictly read-only. Converted .tex, PDFs, logs and
                 worklogs all go under :attr:`root`, preserving the corpus's
                 relative layout.
    ``in-place`` the corpus .tex files are edited directly. Guarded: the build
                 engine refuses unless the corpus git worktree is clean, so there
                 is always something to diff against and revert to.
    """

    root: Path = Path("a11y-out")
    write_mode: str = "mirror"
    keep_pdf: bool = True
    keep_logs: bool = True
    keep_tex: bool = True

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.write_mode not in WRITE_MODES:
            raise ConfigError(
                f"unknown write mode {self.write_mode!r}",
                hint="use one of: " + ", ".join(WRITE_MODES),
            )

    @property
    def in_place(self) -> bool:
        return self.write_mode == "in-place"

    #: Sub-directories, named once so the TUI, the docs and the engine agree.
    def pdf_dir(self) -> Path:
        return self.root / "pdf"

    def log_dir(self) -> Path:
        return self.root / "logs"

    def tex_dir(self) -> Path:
        return self.root / "tex"

    def worklog_dir(self) -> Path:
        return self.root / "descriptions"

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "write_mode": self.write_mode,
            "keep_pdf": self.keep_pdf,
            "keep_logs": self.keep_logs,
            "keep_tex": self.keep_tex,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Output":
        data = data or {}
        return cls(
            root=Path(str(data.get("root", "a11y-out"))),
            write_mode=str(data.get("write_mode", "mirror")),
            keep_pdf=bool(data.get("keep_pdf", True)),
            keep_logs=bool(data.get("keep_logs", True)),
            keep_tex=bool(data.get("keep_tex", True)),
        )


# ---------------------------------------------------------------------- #
# the run config
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class RunConfig:
    """Everything one conversion run needs to know."""

    profile: str = "eecs16a"
    #: Corpus-relative assignment directories, e.g. ``sp26/hw/9``.
    assignments: tuple[str, ...] = ()
    standards: Standards = field(default_factory=Standards.defaults)
    colors: ColorChoice = field(default_factory=ColorChoice)
    alt: AltChoice = field(default_factory=AltChoice)
    output: Output = field(default_factory=Output)
    #: False is a dry run: nothing is written anywhere. The default, deliberately.
    write: bool = False

    def with_assignments(self, paths: Iterable[str]) -> "RunConfig":
        return replace(self, assignments=tuple(dict.fromkeys(paths)))

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "assignments": list(self.assignments),
            "standards": self.standards.as_dict(),
            "colors": self.colors.as_dict(),
            "alt": self.alt.as_dict(),
            "output": self.output.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RunConfig":
        data = data or {}
        if not isinstance(data, dict):
            raise ConfigError("a run config must be a YAML mapping")
        return cls(
            profile=str(data.get("profile", "eecs16a")),
            assignments=tuple(str(item) for item in (data.get("assignments") or ())),
            standards=Standards.from_dict(data.get("standards")),
            colors=ColorChoice.from_dict(data.get("colors")),
            alt=AltChoice.from_dict(data.get("alt")),
            output=Output.from_dict(data.get("output")),
        )

    def to_yaml(self) -> str:
        header = (
            "# latexa11y run configuration.\n"
            "#   latexa11y run --config <this file>      replay it exactly\n"
            "#   latexa11y run                           edit it in the TUI\n"
            "# `write` is deliberately not stored: committing to a corpus is a\n"
            "# decision made at the moment of running, never inherited from a file.\n"
        )
        return header + yaml.safe_dump(self.as_dict(), sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> "RunConfig":
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"malformed run config: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: Path | str) -> "RunConfig":
        path = Path(path)
        if not path.is_file():
            raise ConfigError(
                f"no such run config: {path}",
                hint="run `latexa11y run` once; it writes run.yaml beside its output",
            )
        return cls.from_yaml(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- #
# discovery
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class Assignment:
    """One compilable unit of course material."""

    #: Corpus-relative directory, e.g. ``sp26/hw/9``.
    path: str
    #: Profile-declared kind: homework, discussion, exam, note…
    kind: str
    #: Driver file name within that directory, e.g. ``sol9.tex``.
    driver: str | None
    tex_files: int = 0

    @property
    def buildable(self) -> bool:
        return self.driver is not None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "driver": self.driver,
            "tex_files": self.tex_files,
            "buildable": self.buildable,
        }


def find_driver(directory: Path) -> str | None:
    """The file to hand to pdflatex for this assignment, or ``None``.

    A driver is the file that carries ``\\begin{document}``; the rest of an
    assignment is ``\\input`` fragments (``body9.tex``) that do not compile on
    their own. The naming convention is checked first because it is what the
    corpus actually uses, and the content probe is the fallback for anything
    that does not follow it.
    """
    name = directory.name
    for candidate in (f"sol{name}.tex", "sol.tex", f"{name}.tex", f"prob{name}.tex"):
        if (directory / candidate).is_file():
            return candidate
    # Fallback: any .tex that really does open a document. Sorted so the choice
    # is deterministic across machines rather than filesystem-order dependent.
    for path in sorted(directory.glob("*.tex")):
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:20_000]
        except OSError:  # pragma: no cover
            continue
        if "\\begin{document}" in head:
            return path.name
    return None


def _kind_of(relative: str, kinds: dict[str, str]) -> str:
    """Classify by the profile's pattern map, longest pattern winning.

    Longest-first matters: ``sp26/hw`` must beat a bare ``hw`` when a profile
    declares both, otherwise the answer depends on dict order.
    """
    parts = relative.split("/")
    for pattern in sorted(kinds, key=len, reverse=True):
        needle = pattern.strip("/").split("/")
        if any(parts[i : i + len(needle)] == needle for i in range(len(parts))):
            return kinds[pattern]
    return "other"


def discover_assignments(
    profile: Profile,
    scope: str | None = None,
    *,
    kinds: dict[str, str] | None = None,
) -> list[Assignment]:
    """Every assignment directory in a scope, classified and driver-resolved.

    Works off ``profile.iter_files``, so profile excludes apply -- which is the
    whole point in a corpus where 17k of 17.6k .tex files are frozen per-semester
    snapshots nobody reads.
    """
    root = profile.corpus.root.resolve()
    kinds = kinds if kinds is not None else profile.corpus.kinds

    counts: dict[str, int] = {}
    for path in profile.iter_files(scope):
        if path.suffix.lower() != ".tex":
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        parent = relative.parent.as_posix()
        counts[parent] = counts.get(parent, 0) + 1

    found: list[Assignment] = []
    for relative, count in sorted(counts.items()):
        directory = root / relative
        found.append(
            Assignment(
                path=relative,
                kind=_kind_of(relative, kinds),
                driver=find_driver(directory),
                tex_files=count,
            )
        )
    return found


def group_by_kind(assignments: Iterable[Assignment]) -> dict[str, list[Assignment]]:
    """Assignments bucketed by kind, for the TUI's scope picker."""
    grouped: dict[str, list[Assignment]] = {}
    for assignment in assignments:
        grouped.setdefault(assignment.kind, []).append(assignment)
    return dict(sorted(grouped.items()))


def iter_selected(profile: Profile, config: RunConfig) -> Iterator[Assignment]:
    """The assignments a config names, resolved against the corpus.

    Raises rather than skipping a path that does not exist: a run that silently
    converts four of the five things you asked for is worse than one that stops.
    """
    root = profile.corpus.root.resolve()
    kinds = profile.corpus.kinds
    for relative in config.assignments:
        directory = (root / relative).resolve()
        if not directory.is_dir():
            raise ConfigError(
                f"no such assignment directory: {relative}",
                hint=f"paths are relative to the corpus root {root}",
            )
        yield Assignment(
            path=relative,
            kind=_kind_of(relative, kinds),
            driver=find_driver(directory),
            tex_files=len(list(directory.glob("*.tex"))),
        )

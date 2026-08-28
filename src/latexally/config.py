"""Course profiles and corpus scoping.

Everything course-specific lives in a YAML profile so the tool itself stays
generic. A new course is onboarded by writing a profile, not by editing Python:
that is what makes this "generalisable" rather than "EECS 16A with the strings
changed".

A profile answers four questions:

1. **Identity** — what goes in the PDF metadata and the running head.
2. **Scope** — which files are live material, which are frozen archives.
3. **Legacy mapping** — how this course's home-grown macros map onto tagged
   headings, which is the one genuinely course-specific piece of logic.
4. **Policy** — contrast floor, alt-text length tiers, artifact allow-list.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from .errors import ConfigError

__all__ = [
    "CourseIdentity",
    "CorpusScope",
    "HeadingMap",
    "FigurePolicy",
    "ColorPolicy",
    "EnginePolicy",
    "Profile",
    "load_profile",
    "builtin_profile_dir",
]

# Defaults live at module level, not as class attributes. These dataclasses use
# `slots=True`, where a class attribute is a slot descriptor rather than the
# default value -- so `EnginePolicy.legacy_testphase` returns a
# `member_descriptor` and any fallback written as `value or EnginePolicy.field`
# silently yields something un-iterable. That crashed `latexally doctor` for
# anyone who ran it without a profile, which is the very first thing a new user
# does.
DEFAULT_TESTPHASE: tuple[str, ...] = (
    "phase-III",
    "math",
    "table",
    "graphic",
    "firstaid",
)
DEFAULT_LATEXMK_ARGS: tuple[str, ...] = ("-interaction=nonstopmode", "-file-line-error")
DEFAULT_FIGURE_ENVIRONMENTS: tuple[str, ...] = (
    "tikzpicture",
    "circuitikz",
    "axis",
    "pgfpicture",
)
DEFAULT_BANNED_OPENERS: tuple[str, ...] = (
    "image of",
    "picture of",
    "photo of",
    "photograph of",
    "figure showing",
    "figure of",
    "this figure",
    "this diagram",
    "diagram showing",
    "a diagram of",
    "graphic of",
    "screenshot of",
)

#: Files that are never source material regardless of profile.
_ALWAYS_EXCLUDE = (
    "**/__latexindent_temp*.tex",
    "**/*.bak",
    "**/.git/**",
    "**/build/**",
    "**/_minted*/**",
    # This tool's own output. It lives inside the corpus now -- the
    # descriptions belong with the material -- and its mirrored `tex/` holds a
    # converted copy of every .tex a run touched. Without this the next run
    # scans them as if they were course material: figures counted twice,
    # already-converted sources converted again, and the corpus growing a
    # mirror of its mirror.
    "**/ally-out/**",
)


def _excluded(relative: str, pattern: str) -> bool:
    """Does the corpus-relative path ``relative`` match exclude ``pattern``?

    ``fnmatch`` has no notion of a path separator, so ``**/`` compiles to
    ``.*/`` -- which demands *at least one* directory before the match and
    therefore never fires at the corpus root. ``**/*_questionBank/**`` skipped
    ``fa19/su24_questionBank/...`` and let ``su24_questionBank/...`` straight
    through. Matching the pattern with its ``**/`` prefix stripped as well makes
    the leading ``**/`` mean "at any depth, including none", which is what every
    profile that writes one intends.
    """
    pattern = pattern.lstrip("/")
    if fnmatch.fnmatch(relative, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(relative, pattern[3:])


def builtin_profile_dir() -> Path:
    """Directory holding the profiles that ship with the package."""
    return Path(__file__).resolve().parent.parent.parent / "profiles"


def _builtin_profile_names() -> list[str]:
    """Every shipped profile, by name, for error messages and completion."""
    directory = builtin_profile_dir()
    if not directory.is_dir():
        return []
    return sorted(item.stem for item in directory.glob("*.yaml"))


def builtin_profile_names() -> list[str]:
    """Public alias: every installed profile, by name."""
    return _builtin_profile_names()


def default_builtin_profile() -> str | None:
    """The profile a picker should land on, or None when there is no answer.

    Declared in the profile itself with a top-level ``default: true``, so which
    course is current is course data like every other field -- not a name
    compiled into the tool, which would have to be edited to onboard a course
    or to hand this corpus to the next term's staff.

    With exactly one installed that one is the answer whether or not it says
    so. With several and none declaring, there is no answer: returning the
    alphabetically-first would be a guess wearing a default's clothes.
    """
    directory = builtin_profile_dir()
    if not directory.is_dir():
        return None
    declared = []
    for item in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(item.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            # A malformed profile is `load_profile`'s error to raise, with the
            # filename in it. Picking a default is not the place to fail.
            continue
        if isinstance(data, dict) and data.get("default") is True:
            declared.append(item.stem)
    if len(declared) == 1:
        return declared[0]
    if declared:
        # Two courses both claiming to be current is a contradiction in the
        # data, and quietly taking the first would hide it.
        return None
    return _only_builtin_profile()


def profile_summary(name: str) -> str:
    """``EE 66 - Signals, Dynamics, and Information`` for a picker row.

    Falls back to the bare name, because a profile too broken to read is still
    a profile someone may need to select in order to see the error.
    """
    path = builtin_profile_dir() / f"{name}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        course = data.get("course") or {}
        number = str(course.get("number") or "").strip()
        title = str(course.get("name") or "").strip()
    except (OSError, yaml.YAMLError, AttributeError):
        return name
    return " - ".join(part for part in (number, title) if part) or name


def _only_builtin_profile() -> str | None:
    """The one shipped profile, when there is exactly one.

    With a single course installed, ``-p eecs16a`` on every command is noise
    that can only be typed correctly or wrongly. Add a second profile and this
    stops guessing, which is the right answer then: silently picking one of two
    courses would convert the wrong corpus without saying so.
    """
    directory = builtin_profile_dir()
    if not directory.is_dir():
        return None
    profiles = sorted(directory.glob("*.yaml"))
    return profiles[0].stem if len(profiles) == 1 else None


@dataclass(slots=True)
class CourseIdentity:
    number: str = "COURSE"
    name: str = ""
    university: str = ""
    short_university: str = ""
    semester: str = ""
    language: str = "en-US"


@dataclass(slots=True)
class CorpusScope:
    """Which files are in play, expressed as glob include/exclude lists."""

    root: Path = field(default_factory=Path)
    include: tuple[str, ...] = ("**/*.tex",)
    exclude: tuple[str, ...] = ()
    named: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Driver filename prefix -> variant, e.g. ``{"prob": "problem"}``. An
    #: assignment ships several documents built from one body; this says which
    #: file is which. Empty falls back to run.DEFAULT_VARIANT_PREFIXES.
    variants: dict[str, str] = field(default_factory=dict)
    #: Path fragment -> human kind, e.g. ``{"hw": "homework", "dis": "discussion"}``.
    #: Lets the runner offer "all homeworks" without the Python knowing that this
    #: particular course spells that directory ``hw``.
    kinds: dict[str, str] = field(default_factory=dict)

    def patterns_for(self, scope: str | None) -> tuple[str, ...]:
        """Resolve a named scope (``sp26``, ``exams``) to include patterns."""
        if scope is None:
            return self.include
        if scope in self.named:
            return self.named[scope]
        # Allow an ad-hoc directory or glob that is not a declared named scope.
        candidate = (self.root / scope).resolve()
        if candidate.is_dir():
            try:
                relative = candidate.relative_to(self.root.resolve())
            except ValueError as exc:
                raise ConfigError(
                    f"scope {scope!r} is outside the corpus root {self.root}",
                    hint="pass a path inside the corpus, or add it to corpus.scopes",
                ) from exc
            return (f"{relative}/**/*.tex",)
        if any(ch in scope for ch in "*?["):
            return (scope,)
        raise ConfigError(
            f"unknown scope {scope!r}",
            hint=(
                "known scopes: "
                + (", ".join(sorted(self.named)) or "<none declared>")
                + "; or pass a directory path relative to the corpus root"
            ),
        )

    def iter_files(self, scope: str | None = None) -> Iterator[Path]:
        """Yield every source file in a scope, excludes applied, sorted."""
        root = self.root.resolve()
        if not root.is_dir():
            raise ConfigError(
                f"corpus root does not exist: {root}",
                hint="set corpus.root in the profile, or pass --corpus",
            )
        seen: set[Path] = set()
        results: list[Path] = []
        excludes = (*self.exclude, *_ALWAYS_EXCLUDE)
        for pattern in self.patterns_for(scope):
            for path in root.glob(pattern):
                if not path.is_file() or path in seen:
                    continue
                relative = path.relative_to(root).as_posix()
                if any(_excluded(relative, pat) for pat in excludes):
                    continue
                seen.add(path)
                results.append(path)
        yield from sorted(results)


@dataclass(slots=True)
class HeadingMap:
    """Legacy macro -> heading level in the tagged output.

    Levels are absolute document levels, not ``\\section`` nesting: 1 is the
    document title (``Title`` + ``H1``), 2 a question, 3 a part, 4 a solution.
    The checker enforces that no level is skipped (Matterhorn 14-003) and that
    the first heading is level 1 (14-002).
    """

    macros: dict[str, int] = field(default_factory=dict)
    environments: dict[str, int] = field(default_factory=dict)
    #: Macros whose content is a heading *label* rather than free text, e.g.
    #: ``\qitem`` numbers itself and needs a synthesised "Part (a)" string.
    autonumbered: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class FigurePolicy:
    #: Length above which a figure needs a body-level long description (tier T2).
    description_max_chars: int = 200
    #: Data-point count above which a data table beats prose.
    max_inline_data_points: int = 12
    max_series: int = 2
    max_graph_edges: int = 6
    #: Paths (relative to corpus root) that are decorative by human decision.
    #: Never inferred from the filename: `lefthalfpic.jpg` looks decorative and
    #: is load-bearing panorama content in an image-stitching question.
    artifact_allowlist: tuple[str, ...] = ()
    #: Environments treated as a single describable figure.
    figure_environments: tuple[str, ...] = DEFAULT_FIGURE_ENVIRONMENTS
    #: Phrases that must never open an alt string.
    banned_openers: tuple[str, ...] = DEFAULT_BANNED_OPENERS


@dataclass(slots=True)
class ColorPolicy:
    #: WCAG 2.1 SC 1.4.3 Level AA thresholds.
    min_contrast_normal: float = 4.5
    min_contrast_large: float = 3.0
    #: What the course actually defines. The single source of truth: every
    #: replacement is derived from these by darkening the original just enough
    #: to clear the floor, never chosen from a fixed palette.
    originals: dict[str, str] = field(default_factory=dict)
    #: Colour names known to be applied to large text only.
    large_text_colors: tuple[str, ...] = ()
    #: Assumed page background when a colour is used without an explicit one.
    background: str = "#FFFFFF"


@dataclass(slots=True)
class EnginePolicy:
    name: str = "pdflatex"
    #: Minimum LaTeX format date that supports `tagging=on` and `pdfstandard=ua-*`.
    min_format_date: str = "2025-06-01"
    pdf_standard: str = "ua-1"
    #: Paired with pdf_standard, not chosen independently: PDF/UA-1 is defined
    #: against ISO 32000-1, which is PDF 1.7. Raise this to 2.0 only alongside
    #: pdf_standard=ua-2.
    pdf_version: str = "1.7"
    #: ``package.sty -> commands to \let \relax before it loads``.
    #:
    #: Turning tagging on makes the kernel define names it did not define
    #: before, and a course package that defines the same name then dies with
    #: "Command \x already defined" on a source that has always compiled.
    unlet_before: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: testphase modules used on older toolchains, in declaration order.
    legacy_testphase: tuple[str, ...] = DEFAULT_TESTPHASE
    latexmk_args: tuple[str, ...] = DEFAULT_LATEXMK_ARGS
    min_runs: int = 3
    timeout_seconds: int = 300
    #: ``package.sty -> commands to \let \relax before it loads``.
    #:
    #: Turning tagging on makes the LaTeX kernel define things it did not
    #: define before, and a course package that defines the same name then
    #: dies with "Command \x already defined" -- on a source that has compiled
    #: for years and still compiles untagged. It is purely conversion-induced,
    #: so it is this tool's job to absorb it.
    #:
    #: Course-specific by nature, hence a profile key rather than a constant:
    #: which package clashes depends entirely on whose macros they are.
    unlet_before: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(slots=True)
class Profile:
    name: str = "default"
    course: CourseIdentity = field(default_factory=CourseIdentity)
    corpus: CorpusScope = field(default_factory=CorpusScope)
    headings: HeadingMap = field(default_factory=HeadingMap)
    figures: FigurePolicy = field(default_factory=FigurePolicy)
    colors: ColorPolicy = field(default_factory=ColorPolicy)
    engine: EnginePolicy = field(default_factory=EnginePolicy)
    #: Where catalogs and worklogs live, relative to the corpus root.
    source_path: Path | None = None

    def iter_files(self, scope: str | None = None) -> Iterator[Path]:
        return self.corpus.iter_files(scope)


# ---------------------------------------------------------------------- #
# loading
# ---------------------------------------------------------------------- #


def _as_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    raise ConfigError(f"{field_name} must be a string or a list of strings")


def _as_command_map(value: Any, field_name: str) -> dict[str, tuple[str, ...]]:
    """``{package.sty: [cmd, ...]}`` from YAML, values normalised to tuples.

    A bare string is accepted as a one-element list, because
    ``ulem.sty: "\\normalem"`` is what people write.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping of package to commands")
    return {
        str(key): _as_tuple(item, f"{field_name}.{key}")
        for key, item in value.items()
    }


def load_profile(
    path: Path | str | None = None,
    *,
    corpus_root: Path | str | None = None,
) -> Profile:
    """Load a profile from a YAML file, a builtin name, or defaults.

    ``path`` may be a filesystem path or a builtin profile name such as
    ``eecs16a``. ``corpus_root`` overrides ``corpus.root`` from the CLI.
    """
    if path is None:
        path = _only_builtin_profile()
    if path is None and _builtin_profile_names():
        # Two or more courses installed and none named. Refusing is the whole
        # point of `_only_builtin_profile` returning None here -- but it used
        # to fall through to an empty profile rooted at the working directory,
        # which found no documents and said "0 in scope default". Adding a
        # second profile therefore looked like it had broken the corpus.
        raise ConfigError(
            "more than one profile is installed, so which course is not obvious",
            hint=(
                "name one with -p: "
                + ", ".join(_builtin_profile_names())
                + "  (or pass a path to a profile YAML)"
            ),
        )
    if path is None:
        data: dict[str, Any] = {}
        source: Path | None = None
    else:
        candidate = Path(path)
        if not candidate.exists():
            builtin = builtin_profile_dir() / f"{path}.yaml"
            if not builtin.exists():
                available = sorted(
                    p.stem for p in builtin_profile_dir().glob("*.yaml")
                ) if builtin_profile_dir().is_dir() else []
                raise ConfigError(
                    f"no such profile: {path}",
                    hint=(
                        "builtin profiles: " + (", ".join(available) or "<none>")
                    ),
                )
            candidate = builtin
        try:
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"malformed profile {candidate}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"profile {candidate} must be a YAML mapping")
        data = loaded
        source = candidate

    course_data = data.get("course") or {}
    corpus_data = data.get("corpus") or {}
    headings_data = data.get("headings") or {}
    figures_data = data.get("figures") or {}
    colors_data = data.get("colors") or {}
    engine_data = data.get("engine") or {}

    root_value = corpus_root if corpus_root is not None else corpus_data.get("root", ".")
    root = Path(root_value)
    if not root.is_absolute():
        base = source.parent if source is not None else Path.cwd()
        root = (base / root).resolve()

    profile = Profile(
        name=str(data.get("name") or (source.stem if source else "default")),
        course=CourseIdentity(
            number=str(course_data.get("number", "COURSE")),
            name=str(course_data.get("name", "")),
            university=str(course_data.get("university", "")),
            short_university=str(course_data.get("short_university", "")),
            semester=str(course_data.get("semester", "")),
            language=str(course_data.get("language", "en-US")),
        ),
        corpus=CorpusScope(
            root=root,
            include=_as_tuple(corpus_data.get("include"), "corpus.include")
            or ("**/*.tex",),
            exclude=_as_tuple(corpus_data.get("exclude"), "corpus.exclude"),
            named={
                str(key): _as_tuple(value, f"corpus.scopes.{key}")
                for key, value in (corpus_data.get("scopes") or {}).items()
            },
            kinds={
                str(key): str(value)
                for key, value in (corpus_data.get("kinds") or {}).items()
            },
            variants={
                str(key): str(value)
                for key, value in (corpus_data.get("variants") or {}).items()
            },
        ),
        headings=HeadingMap(
            macros={str(k): int(v) for k, v in (headings_data.get("macros") or {}).items()},
            environments={
                str(k): int(v) for k, v in (headings_data.get("environments") or {}).items()
            },
            autonumbered={
                str(k): str(v) for k, v in (headings_data.get("autonumbered") or {}).items()
            },
        ),
        figures=FigurePolicy(
            description_max_chars=int(
                figures_data.get(
                    "description_max_chars",
                    # `alt_max_chars` is the pre-rename spelling; a profile
                    # written against it keeps working.
                    figures_data.get("alt_max_chars", 200),
                )
            ),
            max_inline_data_points=int(figures_data.get("max_inline_data_points", 12)),
            max_series=int(figures_data.get("max_series", 2)),
            max_graph_edges=int(figures_data.get("max_graph_edges", 6)),
            artifact_allowlist=_as_tuple(
                figures_data.get("artifact_allowlist"), "figures.artifact_allowlist"
            ),
            figure_environments=_as_tuple(
                figures_data.get("figure_environments"), "figures.figure_environments"
            )
            or DEFAULT_FIGURE_ENVIRONMENTS,
            banned_openers=_as_tuple(
                figures_data.get("banned_openers"), "figures.banned_openers"
            )
            or DEFAULT_BANNED_OPENERS,
        ),
        colors=ColorPolicy(
            min_contrast_normal=float(colors_data.get("min_contrast_normal", 4.5)),
            min_contrast_large=float(colors_data.get("min_contrast_large", 3.0)),
            originals={
                str(k): str(v) for k, v in (colors_data.get("originals") or {}).items()
            },
            large_text_colors=_as_tuple(
                colors_data.get("large_text_colors"), "colors.large_text_colors"
            ),
            background=str(colors_data.get("background", "#FFFFFF")),
        ),
        engine=EnginePolicy(
            name=str(engine_data.get("name", "pdflatex")),
            min_format_date=str(engine_data.get("min_format_date", "2025-06-01")),
            pdf_standard=str(engine_data.get("pdf_standard", "ua-1")),
            pdf_version=str(engine_data.get("pdf_version", "1.7")),
            unlet_before=_as_command_map(
                engine_data.get("unlet_before"), "engine.unlet_before"
            ),
            legacy_testphase=_as_tuple(
                engine_data.get("legacy_testphase"), "engine.legacy_testphase"
            )
            or DEFAULT_TESTPHASE,
            latexmk_args=_as_tuple(engine_data.get("latexmk_args"), "engine.latexmk_args")
            or DEFAULT_LATEXMK_ARGS,
            min_runs=int(engine_data.get("min_runs", 3)),
            timeout_seconds=int(engine_data.get("timeout_seconds", 300)),
        ),
        source_path=source,
    )
    return profile

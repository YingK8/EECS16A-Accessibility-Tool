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

#: Files that are never source material regardless of profile.
_ALWAYS_EXCLUDE = (
    "**/__latexindent_temp*.tex",
    "**/*.bak",
    "**/.git/**",
    "**/build/**",
    "**/_minted*/**",
)


def builtin_profile_dir() -> Path:
    """Directory holding the profiles that ship with the package."""
    return Path(__file__).resolve().parent.parent.parent / "profiles"


@dataclass(slots=True)
class CourseIdentity:
    number: str = "COURSE"
    name: str = ""
    university: str = ""
    short_university: str = ""
    semester: str = ""
    language: str = "en-US"

    def pdf_title(self, document_title: str) -> str:
        """The ``dc:title`` string. Matterhorn 06-003 requires it be non-empty."""
        parts = [part for part in (self.number, document_title) if part]
        return " — ".join(parts) if parts else "Untitled document"


@dataclass(slots=True)
class CorpusScope:
    """Which files are in play, expressed as glob include/exclude lists."""

    root: Path = field(default_factory=Path)
    include: tuple[str, ...] = ("**/*.tex",)
    exclude: tuple[str, ...] = ()
    named: dict[str, tuple[str, ...]] = field(default_factory=dict)

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
                if any(
                    fnmatch.fnmatch(relative, pat.lstrip("/")) for pat in excludes
                ):
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

    def level_of(self, macro: str) -> int | None:
        return self.macros.get(macro)


@dataclass(slots=True)
class FigurePolicy:
    #: Length above which a figure needs a body-level long description (tier T2).
    alt_max_chars: int = 200
    #: Data-point count above which a data table beats prose.
    max_inline_data_points: int = 12
    max_series: int = 2
    max_graph_edges: int = 6
    #: Paths (relative to corpus root) that are decorative by human decision.
    #: Never inferred from the filename: `lefthalfpic.jpg` looks decorative and
    #: is load-bearing panorama content in an image-stitching question.
    artifact_allowlist: tuple[str, ...] = ()
    #: Environments treated as a single describable figure.
    figure_environments: tuple[str, ...] = (
        "tikzpicture",
        "circuitikz",
        "axis",
        "pgfpicture",
    )
    #: Phrases that must never open an alt string.
    banned_openers: tuple[str, ...] = (
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


@dataclass(slots=True)
class ColorPolicy:
    #: WCAG 2.1 SC 1.4.3 Level AA thresholds.
    min_contrast_normal: float = 4.5
    min_contrast_large: float = 3.0
    #: Colour names to force-replace, e.g. because the course default fails AA.
    replace: dict[str, str] = field(default_factory=dict)
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
    pdf_version: str = "2.0"
    #: testphase modules used on older toolchains, in declaration order.
    legacy_testphase: tuple[str, ...] = (
        "phase-III",
        "math",
        "table",
        "graphic",
        "firstaid",
    )
    latexmk_args: tuple[str, ...] = ("-interaction=nonstopmode", "-file-line-error")
    min_runs: int = 3
    timeout_seconds: int = 300


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
    catalog_dir: str = "a11y"
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
            alt_max_chars=int(figures_data.get("alt_max_chars", 200)),
            max_inline_data_points=int(figures_data.get("max_inline_data_points", 12)),
            max_series=int(figures_data.get("max_series", 2)),
            max_graph_edges=int(figures_data.get("max_graph_edges", 6)),
            artifact_allowlist=_as_tuple(
                figures_data.get("artifact_allowlist"), "figures.artifact_allowlist"
            ),
            figure_environments=_as_tuple(
                figures_data.get("figure_environments"), "figures.figure_environments"
            )
            or FigurePolicy.figure_environments,
            banned_openers=_as_tuple(
                figures_data.get("banned_openers"), "figures.banned_openers"
            )
            or FigurePolicy.banned_openers,
        ),
        colors=ColorPolicy(
            min_contrast_normal=float(colors_data.get("min_contrast_normal", 4.5)),
            min_contrast_large=float(colors_data.get("min_contrast_large", 3.0)),
            replace={str(k): str(v) for k, v in (colors_data.get("replace") or {}).items()},
            large_text_colors=_as_tuple(
                colors_data.get("large_text_colors"), "colors.large_text_colors"
            ),
            background=str(colors_data.get("background", "#FFFFFF")),
        ),
        engine=EnginePolicy(
            name=str(engine_data.get("name", "pdflatex")),
            min_format_date=str(engine_data.get("min_format_date", "2025-06-01")),
            pdf_standard=str(engine_data.get("pdf_standard", "ua-1")),
            pdf_version=str(engine_data.get("pdf_version", "2.0")),
            legacy_testphase=_as_tuple(
                engine_data.get("legacy_testphase"), "engine.legacy_testphase"
            )
            or EnginePolicy.legacy_testphase,
            latexmk_args=_as_tuple(engine_data.get("latexmk_args"), "engine.latexmk_args")
            or EnginePolicy.latexmk_args,
            min_runs=int(engine_data.get("min_runs", 3)),
            timeout_seconds=int(engine_data.get("timeout_seconds", 300)),
        ),
        catalog_dir=str(data.get("catalog_dir", "a11y")),
        source_path=source,
    )
    return profile

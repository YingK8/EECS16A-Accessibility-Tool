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

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

import yaml

from .check.contrast import PALETTE_BINDINGS, minimum_conforming, palette_value
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
    "normalise_hex",
]

WRITE_MODES = ("mirror", "in-place", "edit")
#: `palette` is the default and `conforming` is the narrower, older behaviour.
#: The difference is reach, not arithmetic: `conforming` derives a value per
#: colour NAME and moves only the five names the course defines, which are used
#: in prose and in no drawing anywhere in this corpus. `palette` binds one set
#: of five tokens to both those names AND the xcolor base names the figures
#: spell their colours as, so a page stops using two unrelated blues.
COLOR_MODES = ("palette", "conforming", "house")
ALT_MODES = ("worklog", "placeholders", "caption", "off")

#: The artifacts a run produces, each separately relocatable. Named once here so
#: the model, the TUI and the docs cannot disagree about what a run writes.
ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("pdf", "PDFs", "the converted documents"),
    ("logs", "Build logs", "LaTeX output, kept for diagnosis"),
    ("tex", "Converted sources", "the .tex the PDFs were built from"),
    ("math", "Spoken math", "generated MathML, speech tables and the hash cache"),
    ("descriptions", "Alt-text log", "the Markdown worklogs staff fill in"),
    ("baseline", "Originals", "untouched builds, for the before/after comparison"),
)

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalise_hex(value: str) -> str:
    """``0645ad`` / ``#0645AD`` / ``#06a`` -> ``#0645AD``.

    Raises on anything else rather than guessing: a colour silently read as
    black would be a contrast "pass" nobody asked for.
    """
    match = _HEX.match(value.strip())
    if not match:
        raise ConfigError(
            f"{value!r} is not a hex colour",
            hint="write it as #RRGGBB, for example #0645AD",
        )
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    return "#" + digits.upper()


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """``#0645AD`` -> (0.02, 0.27, 0.68), the 0..1 scale WCAG luminance needs."""
    digits = normalise_hex(value)[1:]
    return tuple(int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4))


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
        True,
        "0.00–0.79% (measured)",
        "Makes each question title a real H2 structure element, so a screen "
        "reader can jump between questions with its heading key. Bookmarks do "
        "NOT provide that -- the outline is a separate object graph from the "
        "structure tree -- so without this a reader has an H1 and then nothing "
        "until the body text.\n\n"
        "This was off by default until it was measured. A heading may not sit "
        "inside a paragraph, so it forces a \\par after the title, and 74 of "
        "362 \\qns calls are followed immediately by text rather than a blank "
        "line -- from which a visual cost was inferred. Measured across six "
        "assignments, including all three in sp26 that exhibit the pattern: "
        "0.00%, 0.00%, 0.00%, 0.00%, 0.42%, 0.79%, with page counts identical "
        "throughout. The \\par collapses into the list item's existing "
        "\\parskip rather than adding to it.",
    ),
    Toggle(
        "math_speech",
        "Spoken math (Formula /Alt)",
        True,
        "none",
        "Converts every tagged formula to a spoken string, so a reader hears "
        "\"the fraction with numerator x squared minus 1\" rather than "
        "latex-lab's default, which is the LaTeX source read out as "
        "backslashes. Needs the MathCAT driver and the [math] extra.\n\n"
        "On by default. It was off, on the argument that it is the slowest "
        "stage and needs a toolchain the rest does not -- but latexally-math "
        "deliberately leaves /Alt EMPTY rather than falling back to the LaTeX "
        "source, so the default produced a document whose every formula was "
        "silent, with no error to say so. Measured on fa26/dis/00B: 38 "
        "formulas, 38 empty /Alt. Turn it off for a fast structural check, "
        "never for a document going to a reader.",
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
    question_tags: bool = True
    math_speech: bool = True
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

    mode: str = "palette"
    #: Extra name -> hex overrides layered on top of the profile's own map.
    overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in COLOR_MODES:
            raise ConfigError(
                f"unknown colour mode {self.mode!r}",
                hint="use one of: " + ", ".join(COLOR_MODES),
            )

    def replacements(self, profile: Profile) -> dict[str, str]:
        """Name -> hex for every colour this run changes.

        The full picture, not a list of confirmations: this is what the colour
        table shows and what ``run.yaml`` records, so it has to describe the run
        that will actually happen.

        Under ``conforming`` each of the profile's originals is darkened just
        enough to clear its floor, and one that already conforms is absent.
        Under ``palette`` every colour is already remapped. ``overrides`` layer
        on top and win either way -- including an override to a colour's own
        original, which is how "keep this one" is recorded.
        """
        if self.mode == "house":
            return {}
        if self.mode == "palette":
            # Every colour the profile names, already remapped. The palette is
            # not a proposal waiting on approval: `\accesspalette` binds these
            # in the .sty whether or not anyone opens the colour screen, so a
            # `replacements` that returned only what someone had confirmed
            # described a different run from the one that would happen -- and
            # the colour table, which reads this, showed a corpus of unchanged
            # colours next to a build that changed all of them.
            # Every name `\accesspalette` binds, not only the ones the profile
            # declares. `green`, `purple` and `orange` are bound in the .sty and
            # are nowhere in ee66.yaml, so a corpus whose plots draw in
            # `green!70!black` had its green remapped by a run that never
            # mentioned green -- not on the colour screen, not in run.yaml.
            applied = {
                name: value
                for name in {**PALETTE_BINDINGS, **profile.colors.originals}
                if (value := palette_value(name))
            }
            return {**applied, **self.overrides}
        derived = {
            name: proposed
            for name, original in profile.colors.originals.items()
            if (proposed := minimum_conforming(
                hex_to_rgb(original),
                background=hex_to_rgb(profile.colors.background),
                target=(
                    profile.colors.min_contrast_large
                    if name in profile.colors.large_text_colors
                    else profile.colors.min_contrast_normal
                ),
            ))
        }
        return {**derived, **self.overrides}

    def set(self, name: str, value: str) -> None:
        """Override one colour. ``value`` is normalised to ``#RRGGBB``."""
        self.overrides[name] = normalise_hex(value)

    def reset(self, name: str) -> None:
        self.overrides.pop(name, None)

    def describe(self, profile: Profile) -> str:
        if self.mode == "house":
            return "course originals kept (may fail WCAG 1.4.3)"
        if self.mode == "palette":
            kept = sum(
                1
                for name, value in self.overrides.items()
                if value.upper() == (profile.colors.originals.get(name) or "").upper()
            )
            notes = []
            if kept:
                notes.append(f"{kept} rejected, kept as the course had them")
            if len(self.overrides) - kept:
                notes.append(f"{len(self.overrides) - kept} set by hand")
            return "one palette across text and figures" + (
                f" ({', '.join(notes)})" if notes else ""
            )
        count = len(self.replacements(profile))
        custom = f", {len(self.overrides)} confirmed by hand" if self.overrides else ""
        return f"{count} colours darkened to the floor{custom}"

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
    ``caption``      also add a visible ``\\caption{}`` to figures that have
                     none, so the marker an author has to fill in is one they
                     read on the page rather than one only a reader hears.
    ``off``          skip figure scanning entirely.

    A marker that reaches a PDF is visible in it. ``caption`` puts it on the
    page, and ``placeholders`` leaves it where the build log and
    ``latexally check`` both name it. Neither stops a build: ``strict``
    survives for anyone who wants that gate back, defaulting off.
    """

    #: ``caption`` by default: the figures are the part of this corpus that is
    #: actually inaccessible, and a default of ``off`` meant the common run
    #: silently skipped them. Captions rather than bare markers because an
    #: unfilled one is then printed on the page, where it cannot be missed.
    mode: str = "caption"
    #: True makes an unfilled placeholder a hard LaTeX error rather than a
    #: warning. Off by default: a build that refuses is a build nobody can look
    #: at, and the marker is reported by `check` and visible in the PDF either
    #: way. Set it in run.yaml to get the gate back.
    strict: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ALT_MODES:
            raise ConfigError(
                f"unknown description mode {self.mode!r}",
                hint="use one of: " + ", ".join(ALT_MODES),
            )

    @property
    def scans(self) -> bool:
        """Whether this run does alt-text work: worklogs, markers, warnings.

        ``caption`` is NOT alt-text work. It adds a caption and stops there, so
        a run that chose it gets no worklog, no ``<<TODO>>`` in ``/Alt`` and no
        undescribed-figure warnings. Alt text is then reached deliberately, with
        ``latexally scan`` and ``latexally check``, rather than arriving as a
        side effect of asking for captions.
        """
        return self.mode in ("worklog", "placeholders")

    @property
    def injects(self) -> bool:
        """Whether ``<<TODO>>`` markers are written into ``/Alt``."""
        return self.mode == "placeholders"

    @property
    def captions(self) -> bool:
        """Whether a figure with no ``\\caption{}`` should be given one."""
        return self.mode == "caption"

    @property
    def touches_sources(self) -> bool:
        """Whether the conversion edits .tex at all, for either reason."""
        return self.injects or self.captions

    def describe(self) -> str:
        if self.mode == "off":
            return "figures not scanned — no alt text will be written"
        if self.mode == "worklog":
            return "list figures needing alt text (your .tex files are not edited)"
        if self.mode == "caption":
            return "list figures, mark each one, AND add a caption where there is none"
        return "list figures AND mark each one in the .tex"

    def as_dict(self) -> dict:
        return {"mode": self.mode, "strict": self.strict}

    @classmethod
    def from_dict(cls, data: dict | None) -> "AltChoice":
        data = data or {}
        return cls(mode=str(data.get("mode", "worklog")), strict=bool(data.get("strict", False)))


@dataclass(slots=True)
class Output:
    """Where everything this run produces is written.

    ``mirror``   the corpus is strictly read-only. Converted .tex, PDFs, logs and
                 worklogs all go under :attr:`root`, preserving the corpus's
                 relative layout.
    ``in-place`` identical, except the finished PDF is written beside the
                 document it was built from instead of into ``root/pdf``. The
                 corpus ``.tex`` is still never edited.
    ``edit``     everything ``in-place`` does, and the converted sources are
                 written back over the corpus originals, so the folder builds
                 with a bare ``pdflatex`` and no TEXINPUTS. This is the only
                 mode that edits course material, and it is the reason
                 ``latexally revert`` exists.

    The distinction between the last two is not pedantry. ``in-place`` was for
    years the mode whose name promised source edits and delivered a PDF, and
    the promise is now kept by a mode that says so.
    """

    #: Replaced with ``<corpus>/ally-out`` by :meth:`anchor` unless the caller
    #: names one. A bare relative default meant "wherever you happened to be
    #: standing", which put a run's output inside the tool's own checkout.
    root: Path = Path("ally-out")
    #: ``in-place``: the PDF lands beside the document it was built from, which
    #: is where someone looking for it expects it. Still not a licence to edit
    #: the source -- only ``edit`` does that.
    write_mode: str = "in-place"
    keep_pdf: bool = True
    keep_logs: bool = True
    keep_tex: bool = True
    #: Per-artifact path overrides, keyed by the slugs in :data:`ARTIFACTS`.
    #: A relative value is taken relative to :attr:`root`; an absolute one wins
    #: outright, so a worklog can live in a shared drive while the PDFs do not.
    paths: dict[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.paths = {key: Path(value) for key, value in self.paths.items()}
        unknown = set(self.paths) - {slug for slug, _, _ in ARTIFACTS}
        if unknown:
            raise ConfigError(
                f"unknown output path(s): {', '.join(sorted(unknown))}",
                hint="known: " + ", ".join(slug for slug, _, _ in ARTIFACTS),
            )
        if self.write_mode not in WRITE_MODES:
            raise ConfigError(
                f"unknown write mode {self.write_mode!r}",
                hint="use one of: " + ", ".join(WRITE_MODES),
            )

    def anchor(self, profile) -> None:
        """Resolve a defaulted root against the corpus, in place.

        Only the default moves. A root the user typed -- on the command line or
        in a replayed ``run.yaml`` -- is theirs and is left exactly as given.
        """
        if self.root == Path("ally-out"):
            self.root = (profile.corpus.root.resolve() / "ally-out")

    @property
    def in_place(self) -> bool:
        """The PDF goes beside the document. ``edit`` implies it."""
        return self.write_mode in ("in-place", "edit")

    @property
    def edits_sources(self) -> bool:
        """The corpus ``.tex`` is rewritten. Only ``edit`` does this."""
        return self.write_mode == "edit"

    def path_for(self, slug: str) -> Path:
        """Where one artifact goes, as an ABSOLUTE path.

        Absolute matters, and not for tidiness. The engine runs pdflatex with
        ``cwd`` set to the directory being built and ``-output-directory`` set
        from here; a relative value is then resolved against *that* directory,
        not against the one the user typed it in. ``-o ally-out`` quietly wrote
        the PDF inside the mirrored source tree, and the log lookup that
        followed found nothing.

        ``as_dict`` still serialises the raw value, so ``run.yaml`` keeps the
        relative form and stays portable between machines.
        """
        override = self.paths.get(slug)
        if override is None:
            return (self.root / slug).absolute()
        return (override if override.is_absolute() else self.root / override).absolute()

    def set_path(self, slug: str, value: str | Path | None) -> None:
        """Relocate one artifact. ``None`` or empty restores the default."""
        if slug not in {name for name, _, _ in ARTIFACTS}:
            raise ConfigError(f"unknown output path {slug!r}")
        if value is None or str(value).strip() == "":
            self.paths.pop(slug, None)
        else:
            self.paths[slug] = Path(str(value).strip()).expanduser()

    #: Named accessors, so the engine never spells a slug itself.
    def pdf_dir(self) -> Path:
        return self.path_for("pdf")

    def log_dir(self) -> Path:
        return self.path_for("logs")

    def tex_dir(self) -> Path:
        return self.path_for("tex")

    def math_dir(self) -> Path:
        """Generated MathML, speech tables and the conversion cache."""
        return self.path_for("math")

    def worklog_dir(self) -> Path:
        return self.path_for("descriptions")

    def baseline_dir(self) -> Path:
        return self.path_for("baseline")

    #: The folder `in-place` writes an assignment's output into, inside the
    #: assignment's own directory.
    ACCESSIBLE_DIR = "accessible"

    def for_assignment(self, corpus_root: Path, assignment_path: str) -> "Output":
        """This output, re-rooted at one assignment's own `accessible/` folder.

        `mirror` keeps everything in one output tree away from the corpus.
        `in-place` puts an assignment's converted sources, PDF and logs in the
        folder the assignment lives in, under `accessible/`, so what was built
        sits with what it was built from.

        Descriptions do NOT move. A description is content-addressed and serves
        every assignment that uses the figure -- three quarters of this corpus's
        graphics come from the shared bank -- so one per assignment folder would
        make the shared ones ambiguous and ask for the same sentence twice. They
        stay in the run's own worklog directory unless the caller has already
        said otherwise.
        """
        if self.write_mode != "in-place":
            return self
        here = (Path(corpus_root) / assignment_path).absolute()
        moved = replace(
            self,
            root=(here / self.ACCESSIBLE_DIR),
            paths=dict(self.paths),
        )
        if "pdf" not in moved.paths:
            # The one thing anybody opens goes in the assignment folder itself,
            # not one level further in and not in a `pdf/` of its own.
            moved.paths["pdf"] = here
        if "tex" not in moved.paths:
            # `accessible/` IS the converted-source tree; no `tex/` level under
            # it. It cannot be flatter than this. The tree beneath mirrors the
            # corpus because that is what makes `\usepackage{../../../ee66}`
            # and `\input{../preambleFa24}` resolve -- TeX resolves a `../`
            # path against the current directory and does not consult TEXINPUTS
            # for it, so a flat directory of converted files does not compile.
            moved.paths["tex"] = moved.root
        if "descriptions" not in moved.paths:
            moved.paths["descriptions"] = self.worklog_dir()
        return moved

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "write_mode": self.write_mode,
            "keep_pdf": self.keep_pdf,
            "keep_logs": self.keep_logs,
            "keep_tex": self.keep_tex,
            "paths": {key: str(value) for key, value in sorted(self.paths.items())},
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Output":
        data = data or {}
        return cls(
            root=Path(str(data.get("root", "ally-out"))),
            write_mode=str(data.get("write_mode", "mirror")),
            keep_pdf=bool(data.get("keep_pdf", True)),
            keep_logs=bool(data.get("keep_logs", True)),
            keep_tex=bool(data.get("keep_tex", True)),
            paths={
                str(key): Path(str(value))
                for key, value in (data.get("paths") or {}).items()
            },
        )


# ---------------------------------------------------------------------- #
# the run config
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class RunConfig:
    """Everything one conversion run needs to know."""

    #: Set by whoever builds the config -- the CLI from the resolved profile,
    #: the runner from the picker. Empty only in a config that names no course,
    #: which `latexally build --config` then resolves from -p as usual.
    profile: str = ""
    #: Corpus-relative assignment directories, e.g. ``sp26/hw/9``.
    assignments: tuple[str, ...] = ()
    standards: Standards = field(default_factory=Standards.defaults)
    colors: ColorChoice = field(default_factory=ColorChoice)
    alt: AltChoice = field(default_factory=AltChoice)
    output: Output = field(default_factory=Output)
    #: Which variants to build. Empty means every variant each assignment has,
    #: which is the honest default: a course ships both the solutions and the
    #: blank handout, and it is the blank one students actually receive.
    variants: tuple[str, ...] = ()
    #: Build the untouched original as well, to measure what conversion cost.
    #: Off by default: it is a second full LaTeX run of every document -- 70s
    #: against 50s on sp26/hw/10 -- and the pixel diff it yields answers "what
    #: did conversion cost", which is a question asked while adopting the tool
    #: rather than on every rebuild of a homework.
    baseline: bool = False
    #: False is a dry run: nothing is written anywhere. The default, deliberately.
    write: bool = False
    #: Documents compiled at once. Each LaTeX run is three passes of a
    #: subprocess, so this is where the wall clock goes; the conversion work in
    #: front of it stays serial because an assignment's variants share one
    #: mirror directory.
    jobs: int = 1

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
            "variants": list(self.variants),
            "baseline": self.baseline,
            # Unlike `write`, this is a how-fast and not a commitment, so a
            # replayed run is entitled to inherit it.
            "jobs": self.jobs,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RunConfig":
        data = data or {}
        if not isinstance(data, dict):
            raise ConfigError("a run config must be a YAML mapping")
        return cls(
            profile=str(data.get("profile", "")),
            assignments=tuple(str(item) for item in (data.get("assignments") or ())),
            standards=Standards.from_dict(data.get("standards")),
            colors=ColorChoice.from_dict(data.get("colors")),
            alt=AltChoice.from_dict(data.get("alt")),
            output=Output.from_dict(data.get("output")),
            variants=tuple(str(item) for item in (data.get("variants") or ())),
            baseline=bool(data.get("baseline", False)),
            jobs=max(1, int(data.get("jobs") or 1)),
        )

    def to_yaml(self) -> str:
        header = (
            "# latexally run configuration.\n"
            "#   latexally run --config <this file>      replay it exactly\n"
            "#   latexally run                           edit it in the TUI\n"
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
                hint="run `latexally run` once; it writes run.yaml beside its output",
            )
        return cls.from_yaml(path.read_text(encoding="utf-8"))

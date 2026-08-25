"""How a run describes itself: paths, swatches, contrast, tables.

Lifted out of the old ``Wizard`` class when the runner moved to Textual. None
of it draws or reads anything -- every function here is ``(profile, config) ->
text`` -- so the same wording serves the review screen, the step screens and
anything else that has to say what a run is about to do.

The wording is load-bearing and was arrived at the hard way: "kept as the
course original" rather than a blank cell, an actual block of ink next to every
hex code, and a path shown relative to the working directory when it can be.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text

from ..config import Profile
from ..errors import LatexAllyError
from ..run import ARTIFACTS, RunConfig, hex_to_rgb, normalise_hex
from ..discover import VARIANT_LABELS

__all__ = [
    "available_variants",
    "color_names",
    "color_note",
    "color_rows",
    "colors_table",
    "contrast",
    "describe_output",
    "describe_variants",
    "floor_for",
    "output_table",
    "proposal_for",
    "show_path",
    "swatch",
]


def show_path(path: Path) -> str:
    """A path as short as it can be without becoming ambiguous.

    Paths are absolute internally, because a relative ``-output-directory``
    resolves against the subprocess's directory rather than the user's. Printing
    them raw puts a wrapped 80-character absolute path in every table.

    Three candidate spellings, shortest wins: relative to the working
    directory, relative to home as ``~/…``, or absolute. The home form earns
    its place now that the output root is anchored to the corpus rather than to
    wherever the command was run -- an absolute path to somebody's course
    repository is long, and on the runner's Output screen it pushed the
    artifact names off the side of the terminal.
    """
    absolute = path.absolute()
    candidates = [str(absolute)]
    try:
        candidates.append(str(absolute.relative_to(Path.cwd())))
    except (ValueError, OSError):
        pass
    try:
        candidates.append(str("~" / absolute.relative_to(Path.home())))
    except (ValueError, RuntimeError):
        pass
    return min(candidates, key=len)


# ---------------------------------------------------------------------- #
# one-line descriptions of current state
# ---------------------------------------------------------------------- #


def describe_variants(config: RunConfig) -> str:
    chosen = config.variants
    if not chosen:
        return "every version each assignment has (solutions AND blank)"
    labels = dict(VARIANT_LABELS)
    return ", ".join(f"{name} ({labels.get(name, name)})" for name in chosen)


def describe_output(config: RunConfig) -> str:
    output = config.output
    mode = (
        "in-place — PDFs beside the originals"
        if output.in_place
        else "mirror — corpus untouched"
    )
    return f"{output.root}/   ({mode})"


def available_variants(profile: Profile, config: RunConfig) -> dict[str, int]:
    """How many selected assignments actually have each variant.

    Shown because the answer is course-specific and often surprising:
    homeworks have solutions and a blank version, discussions add an
    answers-only build, and a few assignments have only one file.
    """
    from ..discover import iter_selected

    counts: dict[str, int] = {}
    try:
        for assignment in iter_selected(profile, config):
            for name in assignment.drivers:
                counts[name] = counts.get(name, 0) + 1
    except LatexAllyError:
        pass
    return counts


# ---------------------------------------------------------------------- #
# colour
# ---------------------------------------------------------------------- #


def swatch(value: str | None) -> str:
    """A filled block in the colour itself, so the hex is not the only cue.

    A hex code is unreadable as a colour to most people and to all of us in a
    hurry. Both Rich and Textual paint the background from the same markup, so
    the block shows the actual ink.
    """
    if not value:
        return "  "
    try:
        return f"[on {normalise_hex(value)}]  [/]"
    except LatexAllyError:
        return "[dim]??[/]"


def contrast(profile: Profile, value: str | None) -> tuple[float | None, str]:
    """``(ratio, rendered)`` against the profile's assumed page background."""
    if not value:
        return None, ""
    from ..check.contrast import contrast_ratio

    try:
        ratio = contrast_ratio(
            hex_to_rgb(value), hex_to_rgb(profile.colors.background)
        )
    except (LatexAllyError, ValueError):
        return None, "[dim]?[/]"
    floor = profile.colors.min_contrast_normal
    mark = "[green]✓[/]" if ratio >= floor else "[red]✗[/]"
    return ratio, f"{ratio:5.2f}:1 {mark}"


def floor_for(profile: Profile, name: str) -> float:
    """The ratio this colour has to reach; large text is allowed a lower one."""
    return (
        profile.colors.min_contrast_large
        if name in profile.colors.large_text_colors
        else profile.colors.min_contrast_normal
    )


def proposal_for(profile: Profile, name: str) -> str | None:
    """The smallest change that clears the floor, or ``None`` if it already does.

    Deliberately not a lookup in a "conforming" palette. A fixed palette is what
    answered a course blue of #3399E6 with #0645AD -- 8.53:1 where 4.5:1 was
    asked for, and reported as harder to read than the colour it replaced.
    """
    from ..check.contrast import minimum_conforming

    original = profile.colors.originals.get(name)
    if not original:
        return None
    try:
        return minimum_conforming(
            hex_to_rgb(original),
            background=hex_to_rgb(profile.colors.background),
            target=floor_for(profile, name),
        )
    except (LatexAllyError, ValueError):
        return None


def color_names(profile: Profile, config: RunConfig) -> list[str]:
    """Every colour this run touches, profile order then any additions."""
    names = list(profile.colors.originals)
    names += [name for name in config.colors.overrides if name not in names]
    return names


def color_note(profile: Profile, config: RunConfig, name: str) -> str:
    original = profile.colors.originals.get(name, "")
    ratio = contrast(profile, original)[0]
    current = config.colors.replacements(profile).get(name)
    settled = " *" if name in config.colors.overrides else ""
    measured = f"{ratio:.2f}:1" if ratio is not None else "?"
    if current and current != original:
        return f"{original} {measured} → {current}{settled}"
    return f"{original} {measured} → kept as the course original{settled}"


#: Column headings for :func:`color_rows`, shared by the Rich table on the
#: review screen and the DataTable the colour step navigates.
COLOR_COLUMNS: tuple[str, ...] = (
    "colour", "course original", "", "contrast", "→", "proposed", "", "contrast",
)


def color_rows(profile: Profile, config: RunConfig) -> list[list[Text]]:
    """One row per colour: original, swatch, ratio, what it becomes, ratio."""
    effective = config.colors.replacements(profile)
    rows: list[list[Text]] = []
    for name in color_names(profile, config):
        original = profile.colors.originals.get(name)
        new = effective.get(name)
        customised = name in config.colors.overrides
        rows.append(
            [
                Text.from_markup(name + (" [cyan]*[/]" if customised else "")),
                Text.from_markup(original or "[dim]unknown[/]"),
                Text.from_markup(swatch(original)),
                Text.from_markup(contrast(profile, original)[1]),
                Text("→" if new else ""),
                Text.from_markup(new or "[dim]kept[/]"),
                Text.from_markup(swatch(new)),
                Text.from_markup(contrast(profile, new)[1]),
            ]
        )
    return rows


def colors_table(profile: Profile, config: RunConfig) -> Table:
    table = Table(box=None, header_style="bold", padding=(0, 1))
    for index, heading in enumerate(COLOR_COLUMNS):
        table.add_column(
            heading,
            no_wrap=index in (0, 1, 5),
            justify="right" if heading == "contrast" else "left",
        )
    for row in color_rows(profile, config):
        table.add_row(*row)
    return table


# ---------------------------------------------------------------------- #
# output
# ---------------------------------------------------------------------- #


def output_table(config: RunConfig) -> Table:
    output = config.output
    table = Table(box=None, header_style="bold", padding=(0, 1))
    table.add_column("artifact", no_wrap=True)
    table.add_column("path", overflow="fold")
    table.add_column("", overflow="fold", style="dim")

    table.add_row(
        Text("Root", style="bold"),
        Text(str(output.root)),
        Text("everything else hangs off this"),
    )
    for slug, label, note in ARTIFACTS:
        customised = slug in output.paths
        table.add_row(
            Text.from_markup(label + (" [cyan]*[/]" if customised else "")),
            Text(show_path(output.path_for(slug))),
            Text(note),
        )
    return table


def under(path: Path, root: Path) -> str:
    """An artifact path spelled relative to the output root that contains it.

    The Output screen's own hint says "a relative one hangs off the root", and
    every default sits directly under it, so the useful thing to show is the
    part that differs -- `pdf`, `descriptions` -- not the absolute path with
    the root repeated on every one of six rows. Once the root became an
    absolute corpus path, repeating it pushed the artifact names off the side
    of the terminal entirely.
    """
    try:
        return str(path.absolute().relative_to(Path(root).absolute()))
    except ValueError:
        return show_path(path)

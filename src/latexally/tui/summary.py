"""How a run describes itself: paths, swatches, contrast, tables."""

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
    """A path as short as it can be without becoming ambiguous."""
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
    """How many selected assignments actually have each variant."""
    from ..discover import iter_selected

    counts: dict[str, int] = {}
    try:
        for assignment in iter_selected(profile, config):
            for name in assignment.drivers:
                counts[name] = counts.get(name, 0) + 1
    except LatexAllyError:
        pass
    return counts


def swatch(value: str | None) -> str:
    """A filled block in the colour itself, so the hex is not the only cue."""
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


def proposal_for(profile: Profile, name: str, mode: str = "conforming") -> str | None:
    """What this run would change ``name`` to, or ``None`` for "nothing"."""
    from ..check.contrast import minimum_conforming, palette_value

    if mode == "house":
        return None
    if mode == "palette":
        proposed = palette_value(name)
        original = profile.colors.originals.get(name)
        if proposed and original and proposed.upper() == original.upper():
            return None
        return proposed

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
    if config.colors.mode == "palette":
        # The palette also binds xcolor's own words, which is what the drawings
        # use -- and a course need not have declared them. Listing only the
        # declared ones showed a run that remapped `green` without saying so.
        from ..check.contrast import PALETTE_BINDINGS

        names += [name for name in PALETTE_BINDINGS if name not in names]
    names += [name for name in config.colors.overrides if name not in names]
    return names


def original_for(profile: Profile, name: str) -> str:
    """What the name means before this run: the course's value, or xcolor's."""
    from ..check.contrast import resolve_named, rgb_to_hex

    declared = profile.colors.originals.get(name)
    if declared:
        return declared
    base = resolve_named(name)
    return rgb_to_hex(base) if base else ""


def color_note(profile: Profile, config: RunConfig, name: str) -> str:
    original = original_for(profile, name)
    ratio = contrast(profile, original)[0]
    current = config.colors.replacements(profile).get(name)
    settled = " *" if name in config.colors.overrides else ""
    measured = f"{ratio:.2f}:1" if ratio is not None else "?"
    if current and current.upper() != original.upper():
        return f"{original} {measured} → {current}{settled}"
    return f"{original} {measured} → rejected, kept as the course had it{settled}"


COLOR_COLUMNS: tuple[str, ...] = (
    "colour", "course original", "", "contrast", "→", "becomes", "", "contrast",
)


def color_rows(profile: Profile, config: RunConfig) -> list[list[Text]]:
    """One row per colour: original, swatch, ratio, what it becomes, ratio."""
    effective = config.colors.replacements(profile)
    rows: list[list[Text]] = []
    for name in color_names(profile, config):
        original = original_for(profile, name)
        new = effective.get(name)
        customised = name in config.colors.overrides
        # Same value out as in means one of two different things, and the table
        # used to call both "rejected": a colour somebody looked at and chose to
        # keep, and one the palette binds to the value it already had.
        unchanged = bool(new and original and new.upper() == original.upper())
        rejected = unchanged and customised
        changing = bool(new) and not unchanged
        rows.append(
            [
                Text.from_markup(name + (" [cyan]*[/]" if customised else "")),
                Text.from_markup(original or "[dim]unknown[/]"),
                Text.from_markup(swatch(original)),
                Text.from_markup(contrast(profile, original)[1]),
                Text("→" if changing else ""),
                Text.from_markup(
                    new if changing else ("[cyan]rejected[/]" if rejected else "[dim]unchanged[/]")
                ),
                Text.from_markup(swatch(new) if changing else ""),
                Text.from_markup(contrast(profile, new)[1] if changing else ""),
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
    """An artifact path spelled relative to the output root that contains it."""
    try:
        return str(path.absolute().relative_to(Path(root).absolute()))
    except ValueError:
        return show_path(path)

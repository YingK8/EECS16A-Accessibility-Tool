"""Deterministic descriptions of pgfplots axes.

For a sampled plot the *data is the description*. Listing four points is
shorter, more accurate and more useful than any prose about the shape of a
curve, and it is exactly what the STEM description guidance asks for: convey the
data, not the drawing. So for plots with literal ``coordinates`` this module
often produces a complete, conformant alt string with no human input at all.

What it will not do is say what the plot *means* -- that a line is a
least-squares fit, or that the student should read off the intercept. Those
sentences are the human's job, and for a graded problem they may be the answer.
"""

from __future__ import annotations

import re

from ..texlex import TexSource
from .common import Skeleton, format_number, latex_to_text, parse_options, split_top_level

__all__ = ["describe_axis"]

_AXIS_ENV = ("axis", "semilogxaxis", "semilogyaxis", "loglogaxis", "polaraxis")
_ADDPLOT = re.compile(r"\\addplot(?P<dim>3)?(?P<star>\*)?\s*(?P<opts>\+?\s*\[[^\]]*\])?")
_COORD = re.compile(r"\(\s*(-?[\d.eE+-]+)\s*,\s*(-?[\d.eE+-]+)\s*\)")
_LEGEND = re.compile(r"\\(?:addlegendentry|legend)\s*\{")
_TICK_RANGE = re.compile(r"^\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*\.\.\.\s*,\s*(-?[\d.]+)\s*$")

_STYLE_WORDS = (
    ("only marks", "scatter"),
    ("ycomb", "stem"),
    ("const plot", "step"),
    ("smooth", "smooth curve"),
    ("dashed", "dashed line"),
    ("thick", None),
)


def _expand_ticks(spec: str) -> list[str]:
    """Expand ``{0,1,...,9}`` as well as literal tick lists."""
    spec = spec.strip().strip("{}")
    ellipsis = _TICK_RANGE.match(spec)
    if ellipsis:
        first, second, last = (float(value) for value in ellipsis.groups())
        step = second - first
        if step == 0:
            return []
        ticks: list[str] = []
        current = first
        # Guard against a malformed spec producing an unbounded list.
        for _ in range(1000):
            if (step > 0 and current > last + 1e-9) or (step < 0 and current < last - 1e-9):
                break
            ticks.append(format_number(str(current)))
            current += step
        return ticks
    return [format_number(piece) for piece in split_top_level(spec) if piece]


def _series_style(options: str) -> str:
    for needle, label in _STYLE_WORDS:
        if needle in options and label:
            return label
    return "curve"


def describe_axis(source: TexSource, start: int, end: int, max_points: int = 12) -> Skeleton:
    """Describe one ``axis`` environment given its span in ``source``."""
    body_source = TexSource(source.text[start:end])
    skeleton = Skeleton(genre="plot")

    axis_span = None
    for name in _AXIS_ENV:
        spans = body_source.environments(name)
        if spans:
            axis_span = spans[0]
            break
    if axis_span is None:
        skeleton.needs.append("no pgfplots axis found; describe the drawing by hand")
        return skeleton

    options = parse_options(axis_span.options)
    xlabel = latex_to_text(options.get("xlabel"))
    ylabel = latex_to_text(options.get("ylabel"))
    title = latex_to_text(options.get("title"))

    body = body_source.text[axis_span.body]

    # --- series -------------------------------------------------------- #
    series: list[dict] = []
    for match in _ADDPLOT.finditer(body):
        chunk = body[match.end() : match.end() + 4000]
        opts = match.group("opts") or ""
        entry: dict = {"style": _series_style(opts), "points": [], "expression": None}
        coordinates = re.search(r"coordinates\s*\{", chunk)
        if coordinates:
            closing = chunk.find("}", coordinates.end())
            block = chunk[coordinates.end() : closing if closing != -1 else None]
            entry["points"] = [
                (format_number(x), format_number(y)) for x, y in _COORD.findall(block)
            ]
            entry["style"] = "scatter" if "only marks" in opts else entry["style"]
        else:
            expression = re.match(r"\s*\{([^{}]*)\}", chunk)
            if expression:
                entry["expression"] = latex_to_text(expression.group(1))
            domain = parse_options(opts.strip("[]")).get("domain")
            entry["domain"] = domain
        series.append(entry)

    legend = []
    for match in re.finditer(_LEGEND, body):
        group = body_source.match_group(axis_span.body_start + match.end() - 1, skip_whitespace=False)
        if group is not None:
            legend.append(latex_to_text(body_source.text[group.inner]))

    # --- summary ------------------------------------------------------- #
    kinds = {entry["style"] for entry in series} or {"plot"}
    kind_word = kinds.pop() if len(kinds) == 1 else "plot"
    if xlabel and ylabel:
        skeleton.summary = f"{kind_word.capitalize()} of {ylabel} versus {xlabel}"
    elif title:
        skeleton.summary = f"{kind_word.capitalize()}: {title}"
    else:
        skeleton.summary = f"{kind_word.capitalize()}"
        skeleton.needs.append("axis labels are missing from the source; name the quantities")
    if len(series) > 1:
        skeleton.summary += f" with {len(series)} series"

    # --- axis ranges and ticks ----------------------------------------- #
    for prefix, label in (("x", xlabel or "horizontal axis"), ("y", ylabel or "vertical axis")):
        low, high = options.get(f"{prefix}min"), options.get(f"{prefix}max")
        ticks = _expand_ticks(options[f"{prefix}tick"]) if f"{prefix}tick" in options else []
        fragment = f"{label}"
        if low is not None and high is not None:
            fragment += f" from {format_number(low)} to {format_number(high)}"
        if ticks and len(ticks) <= 15:
            fragment += f", ticks at {', '.join(ticks)}"
        if low is not None or ticks:
            skeleton.details.append(fragment)

    # --- series detail --------------------------------------------------#
    total_points = sum(len(entry["points"]) for entry in series)
    for index, entry in enumerate(series):
        name = legend[index] if index < len(legend) else None
        prefix = f"{name}: " if name else ("" if len(series) == 1 else f"Series {index + 1}: ")
        if entry["points"]:
            if len(entry["points"]) <= max_points:
                points = "; ".join(f"({x}, {y})" for x, y in entry["points"])
                skeleton.details.append(
                    f"{prefix}{len(entry['points'])} data points: {points}"
                )
            else:
                skeleton.details.append(
                    f"{prefix}{len(entry['points'])} data points "
                    f"from ({entry['points'][0][0]}, {entry['points'][0][1]}) "
                    f"to ({entry['points'][-1][0]}, {entry['points'][-1][1]})"
                )
        elif entry.get("expression"):
            domain = entry.get("domain")
            span = f" over {domain.replace(':', ' to ')}" if domain else ""
            skeleton.details.append(f"{prefix}the function {entry['expression']}{span}")

    # --- data table for anything too long to speak --------------------- #
    if total_points > max_points:
        skeleton.table_header = (xlabel or "x", ylabel or "y")
        skeleton.table = [
            (x, y) for entry in series for x, y in entry["points"]
        ]
        skeleton.needs.append(
            f"{total_points} data points exceed the {max_points}-point limit for an "
            "alt string; emit the generated data table in the body instead"
        )

    # --- confidence ------------------------------------------------------#
    if series and all(entry["points"] or entry.get("expression") for entry in series):
        skeleton.confidence = "high" if (xlabel and ylabel) else "medium"
    else:
        skeleton.confidence = "low"
        skeleton.needs.append("the plotted data could not be read from the source")
    if not legend and len(series) > 1:
        skeleton.needs.append("no legend in the source; name each series by what it represents")
    return skeleton

"""WCAG colour-contrast arithmetic, evaluated at the LaTeX source level.

Contrast is checked in the source rather than in the PDF on purpose. Recovering
the *effective* colour of a glyph from a PDF means tracking the graphics state
across fills, clips, layers and images, and pgf draws through all of them; the
answer would be unreliable exactly where the corpus is most complex. A
``\\definecolor``/``\\textcolor`` pair, by contrast, is unambiguous, and it is
also where the fix belongs.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

__all__ = [
    "srgb_to_luminance",
    "contrast_ratio",
    "parse_color",
    "ColorDefinition",
    "find_colors",
    "minimum_conforming",
    "rgb_to_hex",
    "PALETTE",
    "PALETTE_BINDINGS",
    "SNAP_BINS",
    "ACHROMATIC_CHROMA",
    "snap_bin",
    "palette_value",
]

#: The palette: one colour per hue, for text and for drawings alike.
#:
#: Each is its primary's hue and chroma in OKLCH, at the lightness that
#: maximises ``min(page/4.5, ink/3.0)`` -- the same relative margin over the AA
#: floor for text on the page and over 1.4.11's floor against black axes. There
#: used to be a second, lighter set for drawings, because no colour could clear
#: both floors once a document re-darkened it with ``green!70!black``. Snapping
#: (``SNAP_BINS``) removes the mix, so one value per hue does it now.
#:
#: This is a MIRROR of ``\definecolor{ally*}`` in ``tex/latexally-core.sty``,
#: which is where the values actually take effect -- nothing here is emitted
#: into a document. It exists so the runner can show a person what a name will
#: become before they agree to it. ``tests/test_style_fidelity.py`` asserts the
#: two agree, because a mirror nobody checks is just a second source of truth.
PALETTE: dict[str, str] = {
    "allyBlue": "#1754FF",    # 5.60:1 page / 3.75:1 black ink, hue 227
    "allyRed": "#D20000",     # 5.61 / 3.74, hue 0.   Pure #FF0000 is 4.00:1 and fails AA.
    "allyGreen": "#007900",   # 5.62 / 3.73, hue 120. Pure #00FF00 is 1.37:1.
    "allyPurple": "#B800B8",  # 5.63 / 3.73, hue 300
    "allyOrange": "#A55000",  # 5.60 / 3.75, hue 29
}

#: Which colour name binds to which token, mirroring ``\accesspalette``.
#:
#: The second half is the half that matters and the half a text-only remap
#: misses: no figure in this corpus spells its colour as ``solutionColor``.
#: Every ``\addplot``, ``\draw`` and ``\fill`` uses a bare xcolor word, so
#: unless those move too, the prose is recoloured and the picture beside it is
#: not -- which is how one page came to draw its answer text in #187AC4 and its
#: answer vectors in #0000FF.
PALETTE_BINDINGS: dict[str, str] = {
    # the course's own names
    "solutionColor": "allyBlue",
    "solansColor": "allyBlue",
    "answerColor": "allyBlue",
    "blueish": "allyPurple",
    "redish": "allyRed",
    # xcolor's base names, which is what the drawings use
    "blue": "allyBlue",
    "red": "allyRed",
    "green": "allyGreen",
    "purple": "allyPurple",
    "orange": "allyOrange",
}

#: Below this, a colour is achromatic and left alone: black, white, ``gray!40``,
#: ``black!80``, the ``darkgray176`` a matplotlib export declares. Faintness is
#: the point of a grid line.
ACHROMATIC_CHROMA = 0.10

#: Hue -> the bin's colour NAME, mirroring ``\__access_snap_bin:w`` in the .sty.
#:
#: Rebinding a name reaches a drawing only when the drawing spells its colour as
#: that name, and this corpus mostly does not: blue arrives 27 ways
#: (``blue!40!black``, ``cyan!70!blue``, ``steelblue31119180``, ``lightblue``)
#: and green 14, three of them mixes off black. So the palette also snaps by
#: hue, at the point a colour is selected, and it snaps to the NAME, which picks
#: up whatever that name is bound to.
#:
#: Each entry is the upper bound of a bin; hues at or above the last one wrap
#: round to red.
SNAP_BINS: tuple[tuple[float, str], ...] = (
    (20, "red"),
    (70, "orange"),
    (165, "green"),
    (265, "blue"),
    (345, "purple"),
)


def snap_bin(rgb: tuple[float, float, float]) -> str | None:
    """The colour name ``rgb`` snaps to, or ``None`` if it is left alone."""
    high, low = max(rgb), min(rgb)
    if high - low <= ACHROMATIC_CHROMA:
        return None
    hue = colorsys.rgb_to_hsv(*rgb)[0] * 360
    for edge, name in SNAP_BINS:
        if hue < edge:
            return name
    return "red"



def palette_value(name: str) -> str | None:
    """The hex ``name`` binds to under the palette, or ``None`` if it does not."""
    token = PALETTE_BINDINGS.get(name)
    return PALETTE.get(token) if token else None

_DEFINECOLOR = re.compile(
    r"\\definecolor\s*\{(?P<name>[^{}]+)\}\s*\{(?P<model>[^{}]+)\}\s*\{(?P<spec>[^{}]+)\}"
)

#: Colours xcolor knows by name, as sRGB triples in 0-1.
_NAMED: dict[str, tuple[float, float, float]] = {
    "black": (0, 0, 0),
    "white": (1, 1, 1),
    "red": (1, 0, 0),
    "green": (0, 1, 0),
    "blue": (0, 0, 1),
    "cyan": (0, 1, 1),
    "magenta": (1, 0, 1),
    "yellow": (1, 1, 0),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
    "darkgray": (0.25, 0.25, 0.25),
    "lightgray": (0.75, 0.75, 0.75),
    "brown": (0.75, 0.5, 0.25),
    "orange": (1, 0.5, 0),
    "purple": (0.75, 0, 0.25),
    "violet": (0.5, 0, 0.5),
    "pink": (1, 0.75, 0.75),
    "olive": (0.5, 0.5, 0),
    "teal": (0, 0.5, 0.5),
    "lime": (0.75, 1, 0),
}


@dataclass(slots=True)
class ColorDefinition:
    name: str
    model: str
    spec: str
    rgb: tuple[float, float, float] | None
    line: int


def parse_color(model: str, spec: str) -> tuple[float, float, float] | None:
    """Convert an xcolor model/spec pair to sRGB in 0-1, or ``None``.

    Case is significant and must not be normalised away: xcolor's ``rgb`` takes
    components in 0-1 while ``RGB`` takes them in 0-255. Lower-casing the model
    silently reads ``RGB{31,119,180}`` as out-of-range 0-1 values, clamps them
    to white, and reports a 1.00:1 contrast ratio for every pgfplots colour.
    """
    raw_model = model.strip()
    model = raw_model.lower()
    values = [piece.strip() for piece in spec.split(",")]
    try:
        if raw_model == "RGB" and len(values) == 3:
            return tuple(min(1.0, max(0.0, float(value) / 255)) for value in values)  # type: ignore[return-value]
        if model == "rgb" and len(values) == 3:
            return tuple(min(1.0, max(0.0, float(value))) for value in values)  # type: ignore[return-value]
        if model == "gray" and len(values) == 1:
            level = float(values[0])
            return (level, level, level)
        if model == "html" and len(values) == 1:
            raw = values[0].lstrip("#")
            if len(raw) == 6:
                return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
        if model == "cmyk" and len(values) == 4:
            c, m, y, k = (float(value) for value in values)
            return (
                (1 - min(1.0, c + k)),
                (1 - min(1.0, m + k)),
                (1 - min(1.0, y + k)),
            )
    except (TypeError, ValueError):
        return None
    return None


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def srgb_to_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2.1 relative luminance. Components are 0..1, NOT 0..255.

    The scale is checked rather than assumed. Handing this 0..255 values does
    not raise on its own -- ``_channel`` is a polynomial and happily evaluates
    anything -- it returns a ratio that is merely wrong: (6, 69, 173) on white
    reports 16.79:1 instead of 8.53:1. Both "pass" 4.5:1, so the mistake never
    surfaces as a failure, only as a conformance claim computed from nonsense.
    """
    if any(component > 1.0 for component in rgb):
        raise ValueError(
            f"sRGB components must be 0..1, got {rgb}; divide 0..255 values by 255"
        )
    r, g, b = (_channel(component) for component in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(
    foreground: tuple[float, float, float], background: tuple[float, float, float]
) -> float:
    """WCAG 2.1 contrast ratio, always >= 1."""
    light = srgb_to_luminance(foreground)
    dark = srgb_to_luminance(background)
    if light < dark:
        light, dark = dark, light
    return (light + 0.05) / (dark + 0.05)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """``(0.09, 0.48, 0.77)`` -> ``#187AC4``, clamped to the 8-bit sRGB grid."""
    return "#" + "".join(
        f"{max(0, min(255, round(component * 255))):02X}" for component in rgb
    )


def minimum_conforming(
    rgb: tuple[float, float, float],
    *,
    background: tuple[float, float, float],
    target: float,
) -> str | None:
    """The smallest change to ``rgb`` that reaches ``target`` against ``background``.

    Returns ``#RRGGBB``, or ``None`` when the colour already conforms -- so a
    caller can say "no change needed" instead of proposing the colour back.

    Hue and saturation are held and only HSL lightness moves, away from the
    background: darker on a light page, lighter on a dark one. That keeps the
    course's blue recognisably the course's blue. It is also, to within
    ΔE 0.1, the nearest conforming colour in CIELAB -- contrast depends only on
    relative luminance, and L* is monotone in luminance, so "drop L* to the
    threshold and leave a and b alone" is very nearly what this computes.

    The search steps the **rounded 8-bit value**, not the float. A float that
    just clears 4.5:1 can round to a hex that does not, and the hex is what
    ships: #3399E6 lands on #187AC4 at 4.55:1 for exactly this reason.
    """
    if contrast_ratio(rgb, background) >= target:
        return None

    hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
    # Away from the page. On white this darkens; on a dark page it lightens.
    step = -1 / 512 if srgb_to_luminance(background) > srgb_to_luminance(rgb) else 1 / 512

    while 0.0 <= lightness <= 1.0:
        candidate = rgb_to_hex(colorsys.hls_to_rgb(hue, lightness, saturation))
        if contrast_ratio(_from_hex(candidate), background) >= target:
            return candidate
        lightness += step

    # Black or white, whichever we were heading for. Unreachable for any target
    # a page can actually meet, but a loop that can end must end somewhere.
    return "#000000" if step < 0 else "#FFFFFF"


def _from_hex(value: str) -> tuple[float, float, float]:
    digits = value.lstrip("#")
    return tuple(int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4))


def resolve_named(name: str) -> tuple[float, float, float] | None:
    return _NAMED.get(name.strip().lower())


def find_colors(text: str, line_of) -> list[ColorDefinition]:
    """Every ``\\definecolor`` in a source, resolved to sRGB where possible."""
    found: list[ColorDefinition] = []
    for match in _DEFINECOLOR.finditer(text):
        found.append(
            ColorDefinition(
                name=match.group("name").strip(),
                model=match.group("model").strip(),
                spec=match.group("spec").strip(),
                rgb=parse_color(match.group("model"), match.group("spec")),
                line=line_of(match.start()),
            )
        )
    return found

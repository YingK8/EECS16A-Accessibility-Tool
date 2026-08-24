"""Deterministic descriptions of circuitikz schematics.

People assume circuit topology needs a model to recover. It does not. A
circuitikz path is a cursor walk::

    \\draw (0,0) node[ground]{}
          (0,3) to [C=$C_1$,v=$V_1$] ++(0,-3)
          (0,3) to [switch, l=$S_1$] ++(6,0)
                to [C=$C_2$, v=$V_2$] ++(0,-3)
          node[ground]{};

Track the cursor: ``(x,y)`` sets it, ``++(dx,dy)`` advances it, and every
``to[...]`` becomes an **edge between two coordinates**. Snap coordinates to a
tolerance and the result is a netlist -- component list, node degrees, ground
connections, and a traversal order that follows the circuit electrically rather
than visually.

That last point is the whole reason this matters. The description rule for
circuits is to traverse source -> branches -> ground and never say "the box on
the left", because spatial narration tells a non-sighted student nothing about
the circuit. A netlist gives us electrical order for free.

What remains human: naming the topology ("this is a voltage divider") and
stating what is unknown. For a graded question the topology name is often the
answer, so the tool deliberately does not guess it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..texlex import TexSource
from .common import Skeleton, latex_to_text, parse_options

__all__ = ["describe_circuit"]

#: Component key -> (singular, plural). Keys are circuitikz `to[...]` types.
COMPONENTS: dict[str, tuple[str, str]] = {
    "R": ("resistor", "resistors"),
    "vR": ("variable resistor", "variable resistors"),
    "phR": ("photoresistor", "photoresistors"),
    "C": ("capacitor", "capacitors"),
    "L": ("inductor", "inductors"),
    "V": ("voltage source", "voltage sources"),
    "sV": ("voltage source", "voltage sources"),
    "battery": ("battery", "batteries"),
    "battery1": ("battery", "batteries"),
    "I": ("current source", "current sources"),
    "cV": ("controlled voltage source", "controlled voltage sources"),
    "cI": ("controlled current source", "controlled current sources"),
    "switch": ("switch", "switches"),
    "spst": ("switch", "switches"),
    "ospst": ("open switch", "open switches"),
    "cspst": ("closed switch", "closed switches"),
    "generic": ("component", "components"),
    "twoport": ("two-port element", "two-port elements"),
    "D": ("diode", "diodes"),
    "Do": ("diode", "diodes"),
    "leD": ("LED", "LEDs"),
    "ammeter": ("ammeter", "ammeters"),
    "voltmeter": ("voltmeter", "voltmeters"),
    "ohmmeter": ("ohmmeter", "ohmmeters"),
}

#: Path segments that carry no component.
_WIRE_TYPES = {"short", "open"}

_TO = re.compile(r"\bto\s*\[([^\]]*)\]")
_ABS_COORD = re.compile(r"\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
_REL_COORD = re.compile(r"\+\+?\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
_NAMED = re.compile(r"\(\s*([A-Za-z][\w\- ]*?)(?:\.([\w\-+]+))?\s*\)")
_NODE = re.compile(r"\bnode\s*(?:\[([^\]]*)\])?\s*(?:\(([^)]*)\))?\s*\{([^{}]*)\}")
_DRAW = re.compile(r"\\(?:draw|path|node)\b")
_LABEL_KEYS = ("l", "l_", "l^", "a", "a_", "a^")
_VALUE_KEYS = ("v", "v_", "v^", "i", "i_", "i^")


def _snap(x: float, y: float, tolerance: float = 0.35) -> tuple[float, float]:
    return (round(x / tolerance) * tolerance, round(y / tolerance) * tolerance)


def _component_label(options: dict[str, str], kind: str) -> str | None:
    """The printed label of a component, e.g. ``C_1`` from ``C=$C_1$``."""
    if options.get(kind):
        return latex_to_text(options[kind])
    for key in _LABEL_KEYS:
        if options.get(key):
            return latex_to_text(options[key])
    return None


def _annotations(options: dict[str, str]) -> list[str]:
    """Labelled voltages and currents attached to a component."""
    found = []
    for key in _VALUE_KEYS:
        if options.get(key):
            noun = "voltage" if key.startswith("v") else "current"
            found.append(f"{noun} {latex_to_text(options[key])}")
    return found


def describe_circuit(source: TexSource, start: int, end: int) -> Skeleton:
    """Describe one ``circuitikz`` environment given its span in ``source``."""
    body = source.normalised(start, end)
    skeleton = Skeleton(genre="circuit")

    edges: list[dict] = []
    ground_points: set[tuple[float, float]] = set()
    named_nodes: dict[str, str] = {}
    degree: dict[tuple[float, float], int] = defaultdict(int)

    # --- ground and named nodes ---------------------------------------- #
    for match in _NODE.finditer(body):
        options = parse_options(match.group(1))
        name = (match.group(2) or "").strip()
        text = latex_to_text(match.group(3))
        if any(key.startswith("ground") or key == "ground" for key in options):
            ground_points.add(("ground", len(ground_points)))  # counted, not located
        if name:
            named_nodes[name] = text or name

    ground_count = sum(
        1
        for match in _NODE.finditer(body)
        if any(key == "ground" or key.startswith("ground") for key in parse_options(match.group(1)))
    )

    # --- walk each path ------------------------------------------------- #
    # A path is tokenised into an ordered sequence of coordinates and
    # components, then walked once. Scanning ahead to "the next to[...]"
    # instead is subtly wrong: in
    #     (0,3) to [C=$C_1$] ++(0,-3)  (0,3) to [switch] ...
    # the text between two components holds TWO coordinates -- the component's
    # endpoint and the start of the next branch -- so taking the last one
    # records the capacitor as connecting (0,3) to (0,3).
    for segment in _split_paths(body):
        cursor: tuple[float, float] | None = None
        cursor_name: str | None = None
        pending: dict | None = None
        for token in _tokenise(segment):
            if token["type"] == "component":
                pending = token
                continue
            # token is a coordinate: it moves the cursor.
            previous, previous_name = cursor, cursor_name
            cursor, cursor_name = _apply_coordinate(token, cursor)
            if pending is not None:
                options = pending["options"]
                kind = pending["kind"]
                if kind and kind not in _WIRE_TYPES:
                    edges.append(
                        {
                            "kind": kind,
                            "label": _component_label(options, kind),
                            "annotations": _annotations(options),
                            "from": previous_name or previous,
                            "to": cursor_name or cursor,
                        }
                    )
                    for endpoint in (previous, cursor):
                        if endpoint is not None:
                            degree[endpoint] += 1
                pending = None

    # --- op-amps and other block nodes ---------------------------------- #
    block_nodes = [
        latex_to_text(match.group(2) or match.group(3) or "")
        for match in _NODE.finditer(body)
        if "op amp" in (match.group(1) or "") or "opamp" in (match.group(1) or "")
    ]

    # --- summary --------------------------------------------------------#
    tally = Counter(edge["kind"] for edge in edges)
    phrases: list[str] = []
    for kind, count in tally.most_common():
        singular, plural = COMPONENTS.get(kind, (kind, kind))
        labels = [edge["label"] for edge in edges if edge["kind"] == kind and edge["label"]]
        noun = singular if count == 1 else plural
        if labels:
            phrases.append(f"{count} {noun} ({', '.join(labels)})")
        else:
            phrases.append(f"{count} {noun}")
    if block_nodes:
        phrases.insert(0, f"{len(block_nodes)} operational amplifier"
                       + ("s" if len(block_nodes) > 1 else ""))

    if phrases:
        skeleton.summary = "Circuit with " + ", ".join(phrases)
        if ground_count:
            skeleton.summary += (
                f", and {ground_count} ground connection"
                + ("s" if ground_count > 1 else "")
            )
        skeleton.confidence = "medium"
    else:
        skeleton.summary = "Circuit diagram"
        skeleton.confidence = "low"
        skeleton.needs.append("no components could be read from the source; describe by hand")

    # --- connection detail, in path order (electrical, not spatial) ----- #
    for edge in edges:
        singular = COMPONENTS.get(edge["kind"], (edge["kind"], ""))[0]
        name = f" {edge['label']}" if edge["label"] else ""
        detail = f"{singular.capitalize()}{name} connects {_node_name(edge['from'])} to {_node_name(edge['to'])}"
        if edge["annotations"]:
            detail += ", with " + " and ".join(edge["annotations"])
        skeleton.details.append(detail)

    skeleton.needs.append(
        "name the topology (for example voltage divider, summing amplifier) only if "
        "that is not what the question asks the student to work out"
    )
    return skeleton


def _node_name(node) -> str:
    if node is None:
        return "an unlabelled node"
    if isinstance(node, str):
        return f"node {node}"
    x, y = node
    return f"node at ({x:g}, {y:g})"


def _split_paths(body: str) -> list[str]:
    """Split a picture body into individual ``\\draw``/``\\path`` statements."""
    pieces = []
    for match in _DRAW.finditer(body):
        end = body.find(";", match.end())
        pieces.append(body[match.end() : end if end != -1 else len(body)])
    return pieces


_TOKEN = re.compile(
    r"(?P<component>\bto\s*\[(?P<opts>[^\]]*)\])"
    r"|(?P<coord>\+\+?\s*\(\s*-?[\d.]+\s*,\s*-?[\d.]+\s*\)"
    r"|\(\s*-?[\d.]+\s*,\s*-?[\d.]+\s*\)"
    r"|\(\s*[A-Za-z][\w\- ]*(?:\.[\w\-+]+)?\s*\))"
)


def _tokenise(segment: str) -> list[dict]:
    """Ordered coordinates and components along one path."""
    tokens: list[dict] = []
    for match in _TOKEN.finditer(segment):
        if match.group("component"):
            options = parse_options(match.group("opts"))
            kind = next(
                (key for key in options if key in COMPONENTS or key in _WIRE_TYPES), None
            )
            tokens.append({"type": "component", "kind": kind, "options": options})
        else:
            tokens.append({"type": "coord", "text": match.group("coord").strip()})
    return tokens


def _apply_coordinate(token: dict, cursor: tuple[float, float] | None):
    """Move the cursor by one coordinate token.

    Named anchors such as ``(opamp.out)`` set a symbolic cursor, which is how
    op-amp circuits stay describable even though their pins carry no numeric
    coordinates.
    """
    text = token["text"]
    relative = _REL_COORD.fullmatch(text)
    if relative:
        dx, dy = float(relative.group(1)), float(relative.group(2))
        base = cursor or (0.0, 0.0)
        return _snap(base[0] + dx, base[1] + dy), None
    absolute = _ABS_COORD.fullmatch(text)
    if absolute:
        return _snap(float(absolute.group(1)), float(absolute.group(2))), None
    named = _NAMED.fullmatch(text)
    if named:
        anchor = named.group(2)
        label = (f"{named.group(1)}.{anchor}" if anchor else named.group(1)).strip()
        return cursor, label
    return cursor, None

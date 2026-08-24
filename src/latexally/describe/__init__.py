"""Deterministic, non-AI description skeletons.

Dispatch by genre. Every describer returns a :class:`Skeleton` of *facts*; none
of them writes finished alt text, and the worklog keeps the two apart so a
skeleton can never be mistaken for a description a human approved.
"""

from __future__ import annotations

import re

from ..scan import FigureRef
from ..texlex import TexSource
from .circuitikz import describe_circuit
from .common import Skeleton, latex_to_text
from .pgfplots import describe_axis

__all__ = ["Skeleton", "describe", "describe_reference"]

_AXIS_PRESENT = re.compile(r"\\begin\s*\{(?:semilog[xy]|loglog|polar)?axis\}")
_STATE_NODE = re.compile(r"\\node\s*\[([^\]]*state[^\]]*)\]\s*\(([^)]*)\)[^{]*\{([^{}]*)\}")
_EDGE = re.compile(
    r"\(\s*([\w\- ]+)\s*\)\s*edge\s*(?:\[[^\]]*\])?\s*(?:node\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\})?"
    r"\s*\(\s*([\w\- ]+)\s*\)"
)
_LOOP = re.compile(r"edge\s*\[[^\]]*loop[^\]]*\]\s*node\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
#: Locates the START of a node's text group. The group itself is read with a
#: brace matcher, never with `\{([^{}]+)\}`: the single most common label in this
#: corpus is `{$V_{in}$}`, whose nested braces defeat that pattern entirely.
#: Both spellings are matched -- `\node[...] {text}` and the path form
#: `-- node[above] {text} --`, which has no backslash and is just as common.
_NODE_START = re.compile(
    r"(?:\\node|(?<![A-Za-z\\])node)\s*"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?:\([^)]*\)\s*)?"
    r"(?:at\s*\([^)]*\)\s*)?"
    r"(?::[^{]*)?"
)


def describe_reference(reference: FigureRef, source: TexSource | None = None) -> Skeleton:
    """Skeleton for one scanned figure."""
    if source is None:
        source = TexSource.from_path(reference.file)
    return describe(
        kind=reference.kind,
        source=source,
        start=reference.start,
        end=reference.end,
        image_path=reference.image_path,
        missing_image=reference.missing_image,
    )


def describe(
    *,
    kind: str,
    source: TexSource,
    start: int,
    end: int,
    image_path: str | None = None,
    missing_image: bool = False,
) -> Skeleton:
    if kind == "circuitikz":
        return _note_embedded_images(describe_circuit(source, start, end), source, start, end)
    if kind in ("axis", "semilogxaxis", "semilogyaxis", "loglogaxis", "polaraxis"):
        return describe_axis(source, start, end)
    if kind in ("tikzpicture", "pgfpicture"):
        body = source.text[start:end]
        if _AXIS_PRESENT.search(body):
            return describe_axis(source, start, end)
        return _note_embedded_images(_describe_tikz(source, start, end), source, start, end)
    if kind == "includegraphics":
        return _describe_raster(image_path, missing_image)
    skeleton = Skeleton(genre="unknown")
    skeleton.needs.append(f"no describer for {kind!r}; write the description by hand")
    return skeleton


_EMBEDDED_IMAGE = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


def _note_embedded_images(
    skeleton: Skeleton, source: TexSource, start: int, end: int
) -> Skeleton:
    """Flag drawings that are really a wrapper around an external image.

    A ``circuitikz`` environment whose only content is an ``\\includegraphics``
    of a photograph is not a circuit, and no amount of path parsing will
    describe it. Naming the embedded file tells the human exactly what to open.
    """
    images = _EMBEDDED_IMAGE.findall(source.text[start:end])
    if not images:
        return skeleton
    skeleton.needs.insert(
        0,
        "this drawing embeds an external image ("
        + ", ".join(images)
        + "); open it and describe what it shows",
    )
    skeleton.confidence = "low"
    return skeleton


def _node_labels(body: str) -> list[str]:
    """Text of every node in a picture, with balanced-brace extraction."""
    scanner = TexSource(body)
    labels: list[str] = []
    seen: set[str] = set()
    position = 0
    while True:
        match = _NODE_START.search(scanner.masked, position)
        if match is None:
            break
        group = scanner.match_group(match.end())
        if group is None:
            position = match.end() + 1
            continue
        text = latex_to_text(body[group.inner])
        if text and text not in seen:
            seen.add(text)
            labels.append(text)
        position = group.end
    return labels


def _describe_tikz(source: TexSource, start: int, end: int) -> Skeleton:
    """Generic TikZ: state machines get real structure, others get node text."""
    body = source.normalised(start, end)
    skeleton = Skeleton(genre="diagram")

    states = [
        (match.group(2).strip(), latex_to_text(match.group(3)) or match.group(2).strip())
        for match in _STATE_NODE.finditer(body)
    ]
    if states:
        skeleton.genre = "state-machine"
        names = [label for _, label in states]
        skeleton.summary = (
            f"State-transition diagram with {len(states)} state"
            + ("s" if len(states) > 1 else "")
            + f": {', '.join(names)}"
        )
        identifier_to_label = dict(states)
        transitions = []
        for match in _EDGE.finditer(body):
            src = identifier_to_label.get(match.group(1).strip(), match.group(1).strip())
            dst = identifier_to_label.get(match.group(3).strip(), match.group(3).strip())
            weight = latex_to_text(match.group(2) or "")
            transitions.append(
                f"{src} to {dst}" + (f", labelled {weight}" if weight else "")
            )
        for match in _LOOP.finditer(body):
            weight = latex_to_text(match.group(1))
            transitions.append(f"a self-loop labelled {weight}")
        if transitions:
            skeleton.details.append("Transitions: " + "; ".join(transitions) + ".")
            skeleton.confidence = "medium"
        else:
            skeleton.needs.append("no transitions could be read; list them by hand")
        return skeleton

    labels = _node_labels(body)
    if labels:
        skeleton.summary = "Diagram"
        skeleton.details.append("Labels appearing in the drawing: " + ", ".join(labels[:20]) + ".")
        skeleton.needs.append(
            "only the drawing's text labels could be recovered; state what the diagram "
            "shows and how the labelled elements relate"
        )
    else:
        skeleton.summary = "Diagram"
        skeleton.needs.append("nothing machine-readable in this drawing; describe it by hand")
    skeleton.confidence = "low"
    return skeleton


def _describe_raster(image_path: str | None, missing_image: bool) -> Skeleton:
    """Rasters carry no recoverable content. Say so plainly.

    Nothing about a PNG's meaning is derivable from LaTeX source: the tool knows
    the filename and nothing else. Pretending otherwise would be worse than
    useless here, so the skeleton stays empty and the worklog supplies the
    surrounding question text to make the human's job cheap.
    """
    skeleton = Skeleton(genre="image", confidence="low")
    skeleton.summary = ""
    if missing_image:
        skeleton.needs.append(
            f"the image file {image_path!r} could not be found; fix the path before describing it"
        )
    skeleton.needs.append(
        "raster images carry no machine-readable content -- open the image and "
        "describe what a student needs from it to answer the question"
    )
    return skeleton

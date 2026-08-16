"""Read the structure tree, outline and metadata out of a tagged PDF.

pikepdf has no high-level tagged-PDF API (pikepdf#461), so the ``/K`` tree is
walked by hand. That is fine, and it is what gives us exact control over the
things that matter here: resolving ``/RoleMap``, distinguishing a structure
element from a marked-content reference, and telling "this Figure has no /Alt"
apart from "this Figure has an /Alt that is a placeholder".

PyMuPDF is deliberately not used for this. Its only access path is raw xref
poking (``xref_get_key``), which is documented to return ``None`` or raise on
``/Alt`` and ``/ActualText`` (PyMuPDF#4764). It is a good renderer and a bad
structure reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..errors import MissingDependency

__all__ = ["StructNode", "PdfStructure", "read_structure"]

#: Standard structure types that denote a heading, with their level.
_HEADING_LEVELS = {f"H{n}": n for n in range(1, 7)}


@dataclass(slots=True)
class StructNode:
    """One structure element."""

    tag: str  # role-mapped, e.g. "H1"
    raw_tag: str  # as written, e.g. "section"
    depth: int
    alt: str | None = None
    actual_text: str | None = None
    expansion: str | None = None
    lang: str | None = None
    title: str | None = None
    #: Marked-content ids owned directly by this element.
    mcids: list[int] = field(default_factory=list)
    #: Index into the flat node list of this node's parent.
    parent: int | None = None

    @property
    def heading_level(self) -> int | None:
        return _HEADING_LEVELS.get(self.tag)

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "raw_tag": self.raw_tag,
            "depth": self.depth,
            "alt": self.alt,
            "actual_text": self.actual_text,
            "lang": self.lang,
            "title": self.title,
            "mcids": self.mcids,
        }


@dataclass(slots=True)
class PdfStructure:
    """Everything the checker needs from one PDF."""

    path: Path
    tagged: bool
    nodes: list[StructNode] = field(default_factory=list)
    outline: list[tuple[int, str]] = field(default_factory=list)
    #: (title, page number or None, destination type) for every outline entry.
    #: A bookmark that lists the document without moving to it is a defect no
    #: count of bookmarks can detect -- see `bookmark_targets`.
    outline_targets: list[tuple[str, int | None, str | None]] = field(
        default_factory=list
    )
    title: str | None = None
    lang: str | None = None
    page_count: int = 0
    marked: bool = False
    suspects: list[str] = field(default_factory=list)

    def of_tag(self, *tags: str) -> list[StructNode]:
        wanted = set(tags)
        return [node for node in self.nodes if node.tag in wanted]

    @property
    def headings(self) -> list[StructNode]:
        return [node for node in self.nodes if node.heading_level is not None]

    @property
    def heading_levels(self) -> list[int]:
        return [node.heading_level for node in self.headings if node.heading_level]

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "tagged": self.tagged,
            "marked": self.marked,
            "title": self.title,
            "lang": self.lang,
            "page_count": self.page_count,
            "outline": [{"level": lvl, "title": txt} for lvl, txt in self.outline],
            "nodes": [node.as_dict() for node in self.nodes],
        }


def _decode(value: Any) -> str | None:
    """PDF strings may be literal, hex, or UTF-16BE with a BOM."""
    if value is None:
        return None
    try:
        raw = bytes(value)
    except Exception:
        return str(value)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    return raw.decode("pdfdoc", errors="replace") if hasattr(bytes, "decode") else str(value)


def read_structure(path: Path | str) -> PdfStructure:
    """Walk a PDF's structure tree, outline and metadata."""
    try:
        import pikepdf
    except ImportError as exc:  # pragma: no cover - exercised by env without extra
        raise MissingDependency("pikepdf", "pdf", "reading a PDF structure tree") from exc

    path = Path(path)
    result = PdfStructure(path=path, tagged=False)

    with pikepdf.open(path) as pdf:
        result.page_count = len(pdf.pages)

        root = pdf.Root
        mark_info = root.get("/MarkInfo")
        result.marked = bool(mark_info and mark_info.get("/Marked"))

        lang = root.get("/Lang")
        result.lang = _decode(lang) if lang is not None else None

        try:
            with pdf.open_metadata() as meta:
                result.title = meta.get("dc:title")
        except Exception:  # pragma: no cover - malformed XMP
            result.title = None
        if not result.title:
            info = root.get("/Info") or pdf.trailer.get("/Info")
            if info is not None and "/Title" in info:
                result.title = _decode(info.get("/Title"))

        try:
            with pdf.open_outline() as outline:
                result.outline = list(_flatten_outline(outline.root))
        except Exception:  # pragma: no cover - no outline
            result.outline = []
        try:
            result.outline_targets = _outline_targets(pdf)
        except Exception:  # pragma: no cover - malformed outline
            result.outline_targets = []

        struct_root = root.get("/StructTreeRoot")
        if struct_root is None:
            return result
        result.tagged = True

        role_map = {}
        raw_role_map = struct_root.get("/RoleMap")
        if raw_role_map is not None:
            for key, value in raw_role_map.items():
                role_map[str(key).lstrip("/")] = str(value).lstrip("/")

        _walk(struct_root.get("/K"), 0, None, role_map, result.nodes, set())

    return result


def _flatten_outline(items, depth: int = 1) -> Iterator[tuple[int, str]]:
    for item in items:
        yield depth, str(item.title)
        yield from _flatten_outline(item.children, depth + 1)


def _walk(
    node: Any,
    depth: int,
    parent: int | None,
    role_map: dict[str, str],
    out: list[StructNode],
    seen: set[int],
) -> None:
    """Recursively collect structure elements from a ``/K`` value.

    ``/K`` is polymorphic: an array, a dictionary (structure element *or* a
    marked-content reference), or a bare integer MCID. Only dictionaries with
    ``/S`` are structure elements; everything else is content.
    """
    import pikepdf

    if node is None:
        return

    if isinstance(node, pikepdf.Array):
        for kid in node:
            _walk(kid, depth, parent, role_map, out, seen)
        return

    if isinstance(node, int):
        if parent is not None:
            out[parent].mcids.append(int(node))
        return

    if not isinstance(node, pikepdf.Dictionary):
        return

    # An /MCR or /OBJR is a reference to content, not a structure element.
    if "/S" not in node:
        if parent is not None and "/MCID" in node:
            out[parent].mcids.append(int(node["/MCID"]))
        return

    # Guard against cyclic /K graphs in malformed files.
    try:
        identity = node.objgen[0]
        if identity and identity in seen:
            return
        if identity:
            seen.add(identity)
    except Exception:  # pragma: no cover - direct objects have no objgen
        pass

    raw_tag = str(node["/S"]).lstrip("/")
    element = StructNode(
        tag=role_map.get(raw_tag, raw_tag),
        raw_tag=raw_tag,
        depth=depth,
        alt=_decode(node.get("/Alt")),
        actual_text=_decode(node.get("/ActualText")),
        expansion=_decode(node.get("/E")),
        lang=_decode(node.get("/Lang")),
        title=_decode(node.get("/T")),
        parent=parent,
    )
    out.append(element)
    index = len(out) - 1
    _walk(node.get("/K"), depth + 1, index, role_map, out, seen)


def _named_destinations(pdf) -> dict:
    """Every name in the /Names /Dests tree, flattened."""
    found: dict = {}

    def walk(node) -> None:
        names = node.get("/Names")
        if names is not None:
            for index in range(0, len(names), 2):
                found[str(names[index])] = names[index + 1]
        for kid in node.get("/Kids", []) or []:
            walk(kid)

    names = pdf.Root.get("/Names")
    if names is not None and names.get("/Dests") is not None:
        walk(names["/Dests"])
    return found


def _outline_targets(pdf) -> list[tuple[str, int | None, str | None]]:
    """Where each bookmark actually goes: (title, page, destination type).

    Written because a bookmark can be perfectly formed and still navigate
    nowhere. ``\\bookmark[dest=...]`` REFERENCES a destination rather than
    creating one, and the bookmark package then invents the missing anchors at
    the top of page 1. Titles, nesting, counts and the /Dests tree all looked
    correct; every entry jumped to page 1. Only resolving the destination to a
    page number shows it.

    The type matters as much as the page: ``/XYZ`` carries coordinates and lands
    on the heading, ``/Fit`` only says "this page".
    """
    import pikepdf

    pages = {page.obj.objgen: number for number, page in enumerate(pdf.pages, 1)}
    named = _named_destinations(pdf)

    def resolve(spec) -> tuple[int | None, str | None]:
        # A destination is an array; getting to it may pass through a name (into
        # the /Dests tree) or a dictionary wrapper, and a name may point at
        # either. Bounded, so a malformed file cannot loop forever.
        for _ in range(4):
            if spec is None:
                return None, None
            if isinstance(spec, pikepdf.Array):
                break
            if isinstance(spec, pikepdf.Dictionary):
                spec = spec.get("/D")
                continue
            spec = named.get(str(spec))
        if not isinstance(spec, pikepdf.Array) or len(spec) == 0:
            return None, None
        page = pages.get(getattr(spec[0], "objgen", None))
        kind = str(spec[1]) if len(spec) > 1 else None
        return page, kind

    targets: list[tuple[str, int | None, str | None]] = []

    def walk(item) -> None:
        while item is not None:
            spec = item.get("/Dest")
            if spec is None:
                action = item.get("/A")
                if action is not None and str(action.get("/S", "")) == "/GoTo":
                    spec = action.get("/D")
            page, kind = resolve(spec)
            targets.append((_decode(item.get("/Title")) or "", page, kind))
            first = item.get("/First")
            if first is not None:
                walk(first)
            item = item.get("/Next")

    outlines = pdf.Root.get("/Outlines")
    if outlines is not None:
        walk(outlines.get("/First"))
    return targets

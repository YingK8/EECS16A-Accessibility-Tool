"""Marked-content analysis of a PDF page content stream.

The structure tree says what the tags *are*; the content stream says what text
each tag actually covers. Both are needed to answer the question this project
exists to answer: **would a screen reader speak this?**

The motivating case: an ``AltOnly`` region carries a correct ``/Alt`` and looks
perfect in the structure tree, yet still leaks its contents if any tagged
element opened inside it. That defect is invisible in the LaTeX log, invisible
in the tag list, and invisible to veraPDF -- it shows up only as a readable
marked-content sequence nested inside the Figure. This module finds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import MissingDependency

__all__ = ["MarkedRegion", "PageContent", "read_page_content", "normalise", "SUPPRESSING_TAGS"]

#: Tags whose content assistive technology does not read as prose. A Figure is
#: announced via its /Alt; an Artifact is not announced at all.
SUPPRESSING_TAGS = frozenset({"Figure", "Artifact", "Formula"})

_MC_OPERATOR = re.compile(
    rb"/(?P<tag>\w+)\s*(?P<props><<.*?>>|/\w+)?\s*(?P<op>BDC|BMC)\b|\bEMC\b",
    re.DOTALL,
)
_MCID = re.compile(rb"/MCID\s+(\d+)")
#: Literal strings in text-showing operators. Good enough to detect *presence*
#: of glyphs; it is not a font-aware text extractor and does not claim to be.
_LITERAL = re.compile(rb"\(((?:[^()\\]|\\.)*)\)")


@dataclass(slots=True)
class MarkedRegion:
    """One BDC/BMC ... EMC span."""

    tag: str
    mcid: int | None
    start: int
    end: int
    #: Enclosing regions, outermost first, as (tag, mcid) pairs.
    ancestors: tuple[tuple[str, int | None], ...] = ()
    text: str = ""

    @property
    def is_suppressed(self) -> bool:
        """True when nothing in this region is read as prose.

        A region is suppressed if it, or any ancestor, is a Figure, Formula or
        Artifact -- i.e. an element announced by its /Alt or skipped entirely.
        """
        chain = [*(tag for tag, _ in self.ancestors), self.tag]
        return any(tag in SUPPRESSING_TAGS for tag in chain)


@dataclass(slots=True)
class PageContent:
    page_number: int
    regions: list[MarkedRegion] = field(default_factory=list)
    #: Text drawn outside any marked-content sequence. Under PDF/UA this is
    #: "untagged real content" (Matterhorn checkpoint 01) and always a defect.
    untagged_text: str = ""

    def readable_text(self) -> str:
        """Everything a screen reader would announce as prose, in stream order."""
        return normalise(
            " ".join(
                region.text
                for region in self.regions
                if region.text and not region.is_suppressed
            )
        )

    def find_text(self, needle: str) -> list[MarkedRegion]:
        """Regions whose own text contains ``needle``, whitespace-insensitively."""
        wanted = normalise(needle)
        return [region for region in self.regions if wanted in normalise(region.text)]


_PDF_ESCAPE = re.compile(rb"\\([0-7]{1,3}|[nrtbf()\\])")
_PDF_ESCAPE_SIMPLE = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}


def _unescape(raw: bytes) -> bytes:
    """Resolve PDF string escapes, including octal forms such as ``\\050``."""

    def replace(match: re.Match[bytes]) -> bytes:
        body = match.group(1)
        if body in _PDF_ESCAPE_SIMPLE:
            return _PDF_ESCAPE_SIMPLE[body]
        return bytes([int(body, 8) & 0xFF])

    return _PDF_ESCAPE.sub(replace, raw)


def normalise(text: str) -> str:
    """Collapse whitespace for comparison.

    TeX emits kerned text as many short literal strings, so a single rendered
    word arrives as several fragments and a phrase gains runs of spaces. Callers
    asking "does this region say X" should not have to know that.
    """
    return " ".join(text.split())


def _visible_text(chunk: bytes) -> str:
    """Literal strings drawn in a chunk, concatenated.

    Deliberately encoding-naive: TeX's 8-bit font encodings do not map to
    Unicode without the font's ToUnicode CMap, and for the checks here we only
    need to know *whether* glyphs were drawn and roughly which ones.
    """
    pieces = [_unescape(match.group(1)) for match in _LITERAL.finditer(chunk)]
    return " ".join(piece.decode("latin-1", "replace") for piece in pieces).strip()


def read_page_content(path: Path | str, page_number: int = 0) -> PageContent:
    """Parse one page's marked-content structure."""
    try:
        import pikepdf
    except ImportError as exc:  # pragma: no cover
        raise MissingDependency("pikepdf", "pdf", "reading PDF page content") from exc

    with pikepdf.open(Path(path)) as pdf:
        page = pdf.pages[page_number]
        contents = page.obj.get("/Contents")
        if contents is None:
            return PageContent(page_number=page_number)
        if isinstance(contents, pikepdf.Array):
            raw = b"\n".join(bytes(stream.read_bytes()) for stream in contents)
        else:
            raw = bytes(contents.read_bytes())

    result = PageContent(page_number=page_number)
    stack: list[MarkedRegion] = []
    cursor = 0
    untagged: list[str] = []

    for match in _MC_OPERATOR.finditer(raw):
        # Text between two operators belongs to the innermost region currently
        # open. Slicing a region's whole span instead would attribute every
        # child's glyphs to the parent too, which both double-counts the
        # readable text and hides *which* element actually owns a text run.
        gap = raw[cursor : match.start()]
        if gap:
            text = _visible_text(gap)
            if text:
                if stack:
                    stack[-1].text = f"{stack[-1].text} {text}".strip()
                else:
                    untagged.append(text)
        if match.group("op"):
            props = match.group("props") or b""
            mcid_match = _MCID.search(props)
            region = MarkedRegion(
                tag=match.group("tag").decode("latin-1"),
                mcid=int(mcid_match.group(1)) if mcid_match else None,
                start=match.end(),
                end=-1,
                ancestors=tuple((r.tag, r.mcid) for r in stack),
            )
            stack.append(region)
        else:
            if stack:
                region = stack.pop()
                region.end = match.start()
                result.regions.append(region)
        cursor = match.end()

    # Unbalanced BDC at end of stream: record what we have rather than drop it.
    while stack:  # pragma: no cover - malformed stream
        region = stack.pop()
        region.end = len(raw)
        result.regions.append(region)

    result.regions.sort(key=lambda region: region.start)
    result.untagged_text = " ".join(untagged).strip()
    return result

"""What a screen reader actually says, in the order it says it.

The two existing readers each answer half the question and neither answers this
one. ``structure`` walks the tag tree but holds no text; ``content`` holds the
text but in *content-stream* order, which is the order glyphs were painted, not
the order a reader announces them. A document can be perfectly tagged, contain
every word, draw nothing outside a marked region -- and still be read out in the
wrong order, or stop early, because the defect lives in how the two are joined.

That join is what a screen reader performs: walk the structure tree depth first,
in ``/K`` order, and for each element either announce its substitute text or
descend into it. Reproducing it here turns "a blind student says it stops after
the question number" into an assertion.

Announcement rules, which are the whole point and are not obvious:

* ``/Alt`` **replaces** the element and its entire subtree. This is what makes a
  described figure work, and it is also how a wrongly-placed ``Formula`` can
  swallow a paragraph -- the text is present, tagged, and never spoken.
* ``/ActualText`` replaces the characters but is unreliable across readers, so
  it is announced and separately reported.
* An ``Artifact`` is skipped entirely: running heads, rules, page numbers.
* Everything else contributes the text of its own marked-content ids, then its
  children, in tree order.

One limitation worth knowing before reading the output aloud: the text comes
from ``content``, which reads the raw show-text operators, so kerning inside a
word survives as a space -- "pixel" appears as "pix el". The glyphs and the
extracted text are both correct (**[verified]** against an independent
extractor); it is this reader that does not reconstruct word boundaries from
positioning. Compare on a space-squeezed form, as ``tests/test_speech.py`` does,
and never treat the spacing here as what a reader announces.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .content import SUPPRESSING_TAGS, normalise, read_page_content
from .structure import PdfStructure, StructNode, read_structure

__all__ = ["Utterance", "spoken_utterances", "unreachable_text"]


@dataclass(slots=True)
class Utterance:
    """One thing a reader says, and why it said it."""

    text: str
    tag: str
    #: Index into the structure node list, so a failure can name the element.
    node: int
    #: "alt", "actualtext" or "content" -- which rule produced this text.
    source: str = "content"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.text


def _mcid_text(pdf_path: Path, pages: int) -> dict[tuple[int, int], str]:
    """``(page, mcid) -> text``.

    Keyed by the pair, never by the id alone. An MCID is unique only within its
    page, so a flat map silently concatenates page 1's mcid 6 with page 3's --
    which reads as a document whose first list item swallows the whole paper.
    """
    text: dict[tuple[int, int], str] = {}
    for page in range(pages):
        for region in read_page_content(pdf_path, page).regions:
            if region.mcid is None or not region.text:
                continue
            key = (page, region.mcid)
            existing = text.get(key)
            text[key] = f"{existing} {region.text}" if existing else region.text
    return text


def _mc_text(node: StructNode, position: int, text_of: dict[tuple[int, int], str]) -> str:
    """Text of one marked-content reference of this element."""
    if position >= len(node.mcids):
        return ""
    page = node.pages[position] if position < len(node.pages) else None
    if page is None:
        return ""
    return text_of.get((page, node.mcids[position]), "")


def _node_text(node: StructNode, text_of: dict[tuple[int, int], str]) -> str:
    """All of the element's own text, ignoring where its children sit."""
    return " ".join(
        found for i in range(len(node.mcids)) if (found := _mc_text(node, i, text_of))
    )


def spoken_utterances(
    pdf_path: Path | str, *, structure: PdfStructure | None = None
) -> list[Utterance]:
    """Walk the structure tree and return what a reader announces, in order."""
    pdf_path = Path(pdf_path)
    structure = structure or read_structure(pdf_path)
    if not structure.tagged:
        return []

    children: dict[int | None, list[int]] = {}
    for index, node in enumerate(structure.nodes):
        children.setdefault(node.parent, []).append(index)
    text_of = _mcid_text(pdf_path, structure.page_count)

    said: list[Utterance] = []

    def visit(index: int) -> None:
        node: StructNode = structure.nodes[index]
        if node.tag == "Artifact":
            return
        if node.alt:
            # Replaces the subtree. Descending anyway would announce content the
            # reader never reaches, which is the opposite of the bug we hunt.
            said.append(Utterance(normalise(node.alt), node.tag, index, "alt"))
            return
        if node.actual_text:
            said.append(Utterance(normalise(node.actual_text), node.tag, index, "actualtext"))
            return
        # Walk /K in order rather than "own text, then children": the two are
        # interleaved, and flattening them silently reorders the document.
        for kind, position in node.order:
            if kind == "el":
                visit(position)
                continue
            found = _mc_text(node, position, text_of)
            if found.strip():
                said.append(Utterance(normalise(found), node.tag, index))

    for root in children.get(None, []):
        visit(root)
    return said


def unreachable_text(pdf_path: Path | str) -> list[tuple[str, str]]:
    """Text that is tagged, present, and never announced.

    The failure this exists to catch: an element carrying ``/Alt`` has readable
    descendants, so the words are in the file and in the tag tree, and a reader
    still skips them. ``ALLY-PDF-031`` catches the content-stream form of this;
    this catches the structure-tree form, which is the one that survives a clean
    content stream.
    """
    pdf_path = Path(pdf_path)
    structure = read_structure(pdf_path)
    if not structure.tagged:
        return []
    children: dict[int | None, list[int]] = {}
    for index, node in enumerate(structure.nodes):
        children.setdefault(node.parent, []).append(index)
    text_of = _mcid_text(pdf_path, structure.page_count)

    lost: list[tuple[str, str]] = []

    def buried(index: int) -> str:
        node = structure.nodes[index]
        parts = [_node_text(node, text_of)]
        for child in children.get(index, []):
            if structure.nodes[child].tag not in SUPPRESSING_TAGS:
                parts.append(buried(child))
        return " ".join(p for p in parts if p)

    for index, node in enumerate(structure.nodes):
        if not node.alt or not children.get(index):
            continue
        inside = " ".join(buried(child) for child in children[index]).strip()
        if inside:
            lost.append((node.alt, normalise(inside)))
    return lost

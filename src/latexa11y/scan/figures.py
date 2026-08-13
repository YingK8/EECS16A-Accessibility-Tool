"""Locate every describable graphic in a corpus and give it a stable identity.

Identity is the design decision that everything else depends on. The previous
generation of this tooling identified a figure by
``sha1(relative_path + "#" + character_offset)`` and wrote that id into the
``.tex`` as a ``<<ALT:f-1a2b3c4d>>`` placeholder. Three consequences followed:

* inserting a word earlier in a file renumbered every later figure;
* the semester-rollover copy (``sp25`` -> ``sp26``) changed every path, so an
  entire term's descriptions orphaned in one commit;
* identical figures could not share a description, so ``map_only.pdf`` -- cited
  62 times -- needed 62 hand-written descriptions.

Here identity is **content-addressed**: the SHA-256 of the image bytes for a
raster, or of the comment-stripped, whitespace-normalised environment body for a
drawing. The id is therefore re-derivable at any time, never has to be written
into the source, survives edits, renames and rollovers, and makes deduplication
automatic. No placeholder tokens exist, which also removes the failure mode
where an unfilled placeholder shipped into the PDF as real ``/Alt`` text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Profile
from ..texlex import EnvSpan, TexSource

__all__ = ["FigureRef", "scan_file", "scan_corpus", "IMAGE_EXTENSIONS"]

IMAGE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".gif", ".PNG", ".JPG")

_CAPTION = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*\{")
_LABEL = re.compile(r"\\label\s*\{([^{}]*)\}")
_QUESTION = re.compile(r"\\(?:qns|question|q)\s*(?:\[[^\]]*\])?\s*\{")
_SOLUTION_ENV = ("solution", "answer", "guidance")
_SOLUTION_MACRO = re.compile(r"\\(?:sol|ans|solans)\s*\{")
_ALREADY = re.compile(r"\\begin\s*\{(?:Described|DescribedFigure|Decorative)\}")


@dataclass(slots=True)
class FigureRef:
    """One describable graphic at one call site."""

    id: str
    kind: str  # includegraphics | tikzpicture | circuitikz | axis | ...
    file: Path
    start: int
    end: int
    line: int
    source: str = ""
    image_path: str | None = None
    resolved_image: Path | None = None
    caption: str | None = None
    label: str | None = None
    question: str | None = None
    surrounding: str = ""
    inside_solution: bool = False
    already_described: bool = False
    #: Only meaningful for rasters that could not be located on disk.
    missing_image: bool = False

    @property
    def is_raster(self) -> bool:
        return self.kind == "includegraphics"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": str(self.file),
            "line": self.line,
            "image_path": self.image_path,
            "resolved_image": str(self.resolved_image) if self.resolved_image else None,
            "caption": self.caption,
            "label": self.label,
            "question": self.question,
            "inside_solution": self.inside_solution,
            "already_described": self.already_described,
            "missing_image": self.missing_image,
        }


# ---------------------------------------------------------------------- #
# identity
# ---------------------------------------------------------------------- #


def _digest(payload: bytes, prefix: str) -> str:
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:12]}"


def image_identity(resolved: Path | None, declared: str) -> tuple[str, bool]:
    """(id, missing) for a raster.

    Hashing the *bytes* means the same picture shares one description across
    every call site, every assignment and every semester, however it is named.
    When the file cannot be found we fall back to the declared path so the
    figure still gets a stable id and shows up in the worklog as missing.
    """
    if resolved is not None and resolved.is_file():
        try:
            return _digest(resolved.read_bytes(), "img"), False
        except OSError:  # pragma: no cover - unreadable file
            pass
    return _digest(declared.strip().encode("utf-8"), "img"), True


def drawing_identity(source: TexSource, span: EnvSpan) -> str:
    """Id for a TikZ/circuitikz/pgfplots drawing, from its normalised body."""
    body = source.normalised(span.body_start, span.body_end)
    return _digest(f"{span.name}:{body}".encode("utf-8"), "fig")


# ---------------------------------------------------------------------- #
# context capture
# ---------------------------------------------------------------------- #


def _enclosing_braced(source: TexSource, pattern: re.Pattern[str], before: int) -> str | None:
    """Text of the nearest preceding ``\\macro{...}`` whose group is balanced."""
    best: str | None = None
    for match in source.finditer(pattern):
        if match.start() > before:
            break
        group = source.match_group(match.end() - 1, skip_whitespace=False)
        if group is not None:
            best = source.text[group.inner].strip()
    return best


def _is_inside_solution(source: TexSource, position: int) -> bool:
    """Whether a figure sits in a solution-only block.

    This governs the disclosure rule: because problem and solution builds share
    one body, a figure visible to students must not give away the answer, while
    a solution-only figure may.
    """
    for name in _SOLUTION_ENV:
        for span in source.environments(name):
            if span.start <= position < span.end:
                return True
    for match in source.finditer(_SOLUTION_MACRO):
        group = source.match_group(match.end() - 1, skip_whitespace=False)
        if group is not None and group.start <= position < group.end:
            return True
    return False


def _surrounding(source: TexSource, start: int, end: int, width: int = 400) -> str:
    text = source.text
    before = text[max(0, start - width) : start]
    after = text[end : end + width]
    return re.sub(r"\s+", " ", f"{before} … {after}").strip()


def _resolve_image(declared: str, tex_file: Path, roots: list[Path]) -> Path | None:
    candidate = declared.strip()
    if not candidate:
        return None
    bases = [tex_file.parent, *roots]
    suffixes = [""] if Path(candidate).suffix else list(IMAGE_EXTENSIONS)
    for base in bases:
        for suffix in suffixes:
            target = base / f"{candidate}{suffix}"
            if target.is_file():
                return target.resolve()
    return None


# ---------------------------------------------------------------------- #
# scanning
# ---------------------------------------------------------------------- #


def scan_file(path: Path, profile: Profile) -> list[FigureRef]:
    """Every live figure in one file. Commented-out figures are never returned."""
    source = TexSource.from_path(path)
    roots = [profile.corpus.root.resolve()]
    found: list[FigureRef] = []

    # Drawings first, so an \includegraphics nested inside a tikzpicture can be
    # skipped: the enclosing drawing owns the description for the whole picture.
    drawing_spans: list[EnvSpan] = []
    for name in profile.figures.figure_environments:
        drawing_spans.extend(source.environments(name))
    drawing_spans.sort(key=lambda span: span.start)

    covered: list[tuple[int, int]] = []
    for span in drawing_spans:
        if any(start <= span.start < end for start, end in covered):
            continue
        covered.append((span.start, span.end))
        found.append(
            _build(
                source=source,
                path=path,
                identity=drawing_identity(source, span),
                kind=span.name,
                start=span.start,
                end=span.end,
            )
        )

    for call in source.macro_calls("includegraphics", optional=1, required=1):
        if any(start <= call.start < end for start, end in covered):
            continue
        declared = call.required[0] if call.required else ""
        resolved = _resolve_image(declared, path, roots)
        identity, missing = image_identity(resolved, declared)
        reference = _build(
            source=source,
            path=path,
            identity=identity,
            kind="includegraphics",
            start=call.start,
            end=call.end,
        )
        reference.image_path = declared
        reference.resolved_image = resolved
        reference.missing_image = missing
        found.append(reference)

    found.sort(key=lambda reference: reference.start)
    return found


def _build(
    *, source: TexSource, path: Path, identity: str, kind: str, start: int, end: int
) -> FigureRef:
    preceding = source.text[max(0, start - 300) : start]
    return FigureRef(
        id=identity,
        kind=kind,
        file=path,
        start=start,
        end=end,
        line=source.line_of(start),
        source=source.text[start:end][:2000],
        caption=_enclosing_braced(source, _CAPTION, end + 400),
        label=_nearest_label(source, start, end),
        question=_enclosing_braced(source, _QUESTION, start),
        surrounding=_surrounding(source, start, end),
        inside_solution=_is_inside_solution(source, start),
        already_described=bool(_ALREADY.search(preceding)),
    )


def _nearest_label(source: TexSource, start: int, end: int) -> str | None:
    window = source.masked[max(0, start - 200) : end + 300]
    match = _LABEL.search(window)
    return match.group(1) if match else None


def scan_corpus(profile: Profile, scope: str | None = None) -> list[FigureRef]:
    """Scan a whole scope. Results keep one entry per *call site*."""
    references: list[FigureRef] = []
    for path in profile.iter_files(scope):
        if path.suffix.lower() != ".tex":
            continue
        try:
            references.extend(scan_file(path, profile))
        except Exception:  # pragma: no cover - a single bad file must not stop a sweep
            continue
    return references

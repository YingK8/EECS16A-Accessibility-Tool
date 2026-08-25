"""Resolve the ``\\input``/``\\include`` graph and identify compilable documents.

Only a minority of files in a question-bank corpus are documents: in EECS 16A,
2,622 of 17,609 ``.tex`` files carry ``\\documentclass``; the rest are fragments
pulled in by ``\\input``. Almost every stage of the pipeline needs to know the
difference:

* **build** must compile documents, never fragments.
* **check** reports conformance per document, but a defect usually lives in a
  fragment shared by many documents.
* **alt text** needs the enclosing question prompt for context, and that prompt
  lives in the fragment while the assignment identity lives in the document.

The graph is therefore bidirectional: document -> fragments, and fragment ->
the documents that reach it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .scanner import TexSource

__all__ = [
    "DocumentSpec",
    "IncludeGraph",
    "resolve_include",
    "is_document",
    "is_driver",
]


# \input{x}, \include{x}, \subfile{x}, \import{dir}{x}, \subimport{dir}{x}
_INPUT_RE = re.compile(
    r"\\(?P<cmd>input|include|subfile|subfileinclude)\s*\{(?P<arg>[^{}]*)\}"
    r"|\\(?P<cmd2>import|subimport|includefrom|subincludefrom)\s*"
    r"\{(?P<dir>[^{}]*)\}\s*\{(?P<arg2>[^{}]*)\}"
    # TeX also allows \input without braces: \input foo.tex
    r"|\\input\s+(?P<bare>[^\s{}%\\]+)"
)

_DOCUMENTCLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{[^{}]+\}")
_DOCUMENT_ENV_RE = re.compile(r"\\begin\s*\{document\}")


def is_document(source: TexSource) -> bool:
    """True when the file can be compiled on its own.

    A driver file such as ``sol9.tex`` has ``\\documentclass`` but ends with
    ``\\input{body}``; the ``\\begin{document}`` lives in the *body*. So the
    presence of ``\\documentclass`` — not ``\\begin{document}`` — is what makes a
    file a compilation entry point in this corpus.
    """
    return source.search(_DOCUMENTCLASS_RE) is not None


def is_driver(path: Path, *, roots: list[Path] | None = None) -> bool:
    r"""True when ``path`` can be handed to pdflatex as it stands.

    A driver must do two things: **declare a class** and **open the document**.
    Either one alone is a fragment, and this corpus has both halves lying around
    separately:

    * ``exams/fa15/mt1/body_practice.tex`` opens with ``\begin{document}`` on
      line 1 and declares no class -- ``mt1.tex`` declares it and inputs this.
      Compiling it dies on line 2 with ``\usepackage before \documentclass``.
      Testing for ``\begin{document}``, as this code used to, picked exactly
      these files.
    * ``sp26/preambleFa25.tex`` declares a class and never opens a document.
      It is the shared preamble half a dozen drivers ``\input``. Testing for
      ``\documentclass`` alone picks these.
    * ``sp26/dis/06B/dis06B.tex`` has *neither*: it inputs ``../preambleFa24``
      for the class and ``body`` for the document. It is a real driver.

    So both halves are looked for, following ``\input`` in each direction:
    the class from the preamble (the text before ``\begin{document}``), the
    document from anywhere. Depth is bounded and revisits are pruned, because
    the corpus contains include cycles.
    """
    roots = roots or []
    return _declares_class(path, roots, set(), 3) and _opens_document(
        path, roots, set(), 3
    )


def _preamble_end(source: TexSource) -> int:
    r"""Offset of ``\begin{document}``, or end of file if it never opens one."""
    opened = source.search(_DOCUMENT_ENV_RE)
    return opened.start() if opened else len(source.text)


def _inputs_before(source: TexSource, path: Path, roots: list[Path], limit: int):
    r"""Resolved ``\input`` targets of ``source`` appearing before ``limit``."""
    for match in source.finditer(_INPUT_RE):
        if match.start() >= limit:
            return
        argument = match.group("arg") or match.group("arg2") or match.group("bare")
        if argument is None:
            continue
        target = resolve_include(
            argument, from_file=path, roots=roots, subdir=match.group("dir") or ""
        )
        if target is not None:
            yield target


def _read(path: Path) -> TexSource | None:
    try:
        return TexSource.from_path(path)
    except Exception:
        return None


def _declares_class(path: Path, roots: list[Path], seen: set, depth: int) -> bool:
    r"""``\documentclass`` here or in something this file's preamble inputs."""
    path = path.resolve()
    if path in seen or depth < 0 or (source := _read(path)) is None:
        return False
    seen.add(path)
    end = _preamble_end(source)
    match = source.search(_DOCUMENTCLASS_RE)
    if match is not None and match.start() < end:
        return True
    return any(
        _declares_class(target, roots, seen, depth - 1)
        for target in _inputs_before(source, path, roots, end)
    )


def _opens_document(path: Path, roots: list[Path], seen: set, depth: int) -> bool:
    r"""``\begin{document}`` here or in anything this file inputs."""
    path = path.resolve()
    if path in seen or depth < 0 or (source := _read(path)) is None:
        return False
    seen.add(path)
    if source.search(_DOCUMENT_ENV_RE) is not None:
        return True
    return any(
        _opens_document(target, roots, seen, depth - 1)
        for target in _inputs_before(source, path, roots, len(source.text))
    )


def resolve_include(
    argument: str,
    *,
    from_file: Path,
    roots: list[Path],
    subdir: str = "",
) -> Path | None:
    """Resolve one ``\\input`` argument to a real path, or ``None``.

    TeX's own search order is approximated: relative to the including file
    first, then each configured root. A missing extension means ``.tex``.
    """
    argument = argument.strip()
    if not argument:
        return None
    # \import takes a directory argument that prefixes the filename.
    relative = Path(subdir) / argument if subdir else Path(argument)
    candidates: list[Path] = []
    bases = [from_file.parent, *roots]
    for base in bases:
        target = (base / relative).resolve()
        if target.suffix:
            candidates.append(target)
        else:
            candidates.append(target.with_suffix(".tex"))
            candidates.append(target)  # extensionless file, rare but legal
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@dataclass(slots=True)
class DocumentSpec:
    """A compilable document and everything it transitively pulls in."""

    path: Path
    inputs: list[Path] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def all_sources(self) -> list[Path]:
        return [self.path, *self.inputs]


class IncludeGraph:
    """Bidirectional include graph over a corpus scope."""

    __slots__ = ("roots", "documents", "_fragment_to_documents", "_cache")

    def __init__(self, roots: list[Path]) -> None:
        self.roots = [Path(root).resolve() for root in roots]
        self.documents: dict[Path, DocumentSpec] = {}
        self._fragment_to_documents: dict[Path, set[Path]] = defaultdict(set)
        self._cache: dict[Path, tuple[list[Path], list[str]]] = {}

    # ------------------------------------------------------------------ #

    def direct_inputs(self, path: Path) -> tuple[list[Path], list[str]]:
        """Immediate ``\\input`` targets of one file: (resolved, unresolved)."""
        path = path.resolve()
        if path in self._cache:
            return self._cache[path]
        resolved: list[Path] = []
        unresolved: list[str] = []
        try:
            source = TexSource.from_path(path)
        except Exception:
            self._cache[path] = ([], [])
            return [], []
        for match in source.finditer(_INPUT_RE):
            argument = match.group("arg") or match.group("arg2") or match.group("bare")
            if argument is None:
                continue
            subdir = match.group("dir") or ""
            target = resolve_include(
                argument, from_file=path, roots=self.roots, subdir=subdir
            )
            if target is None:
                unresolved.append(argument.strip())
            else:
                resolved.append(target)
        self._cache[path] = (resolved, unresolved)
        return resolved, unresolved

    def transitive_inputs(self, path: Path) -> tuple[list[Path], list[str]]:
        """All files reachable from ``path``, cycle-safe."""
        seen: set[Path] = set()
        unresolved: list[str] = []
        stack = [path.resolve()]
        while stack:
            current = stack.pop()
            direct, missing = self.direct_inputs(current)
            unresolved.extend(missing)
            for target in direct:
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        seen.discard(path.resolve())
        return sorted(seen), unresolved

    # ------------------------------------------------------------------ #

    def build(self, files: list[Path]) -> "IncludeGraph":
        """Populate the graph from a candidate file list."""
        for path in files:
            path = path.resolve()
            try:
                source = TexSource.from_path(path)
            except Exception:
                continue
            if not is_document(source):
                continue
            inputs, unresolved = self.transitive_inputs(path)
            self.documents[path] = DocumentSpec(
                path=path, inputs=inputs, unresolved=unresolved
            )
            for fragment in inputs:
                self._fragment_to_documents[fragment].add(path)
        return self

    def documents_using(self, fragment: Path) -> list[Path]:
        """Which documents reach this fragment. Empty means orphaned."""
        return sorted(self._fragment_to_documents.get(fragment.resolve(), set()))

    def orphans(self, files: list[Path]) -> list[Path]:
        """Files reachable from no document — safe to skip, or genuinely dead."""
        return [
            path
            for path in files
            if path.resolve() not in self.documents
            and not self._fragment_to_documents.get(path.resolve())
        ]

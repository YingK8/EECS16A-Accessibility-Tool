"""Byte-faithful LaTeX source scanning and editing."""

from .edits import Edit, EditBuffer
from .scanner import (
    VERBATIM_ENVIRONMENTS,
    BraceGroup,
    EnvSpan,
    MacroCall,
    Region,
    TexSource,
)

__all__ = [
    "VERBATIM_ENVIRONMENTS",
    "BraceGroup",
    "Edit",
    "EditBuffer",
    "EnvSpan",
    "MacroCall",
    "Region",
    "TexSource",
]

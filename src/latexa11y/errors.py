"""Exception hierarchy.

Every error carries a `hint` describing the concrete next action, because the
main consumers of these messages are (a) course staff who are not LaTeX
internals experts and (b) LLM agents that need something actionable to react to.
"""

from __future__ import annotations


class LatexA11yError(Exception):
    """Base class for every error this package raises deliberately."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  hint: {self.hint}"
        return self.message


class ToolchainError(LatexA11yError):
    """The TeX/PDF toolchain cannot produce a conforming document."""


class ConfigError(LatexA11yError):
    """A course profile or config file is missing or malformed."""


class SourceError(LatexA11yError):
    """A .tex file could not be read or scanned."""


class EditConflictError(LatexA11yError):
    """Two edits target overlapping byte ranges of the same file."""


class CatalogError(LatexA11yError):
    """The description catalog is inconsistent or unparseable."""


class MissingDependency(LatexA11yError):
    """An optional dependency is required for the requested operation."""

    def __init__(self, package: str, extra: str, purpose: str) -> None:
        super().__init__(
            f"{purpose} requires the optional dependency {package!r}",
            hint=f"install it with:  pip install 'latexa11y[{extra}]'",
        )
        self.package = package
        self.extra = extra

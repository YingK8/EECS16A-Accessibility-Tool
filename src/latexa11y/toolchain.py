"""Toolchain probing — the ``latexa11y doctor`` gate.

Why this is the first thing that was built
------------------------------------------
The dominant failure mode of LaTeX accessibility work is **silent** failure. On
TeX Live 2025:

* ``\\DocumentMetadata{pdfstandard=ua-1}`` raises ``unknown-standard`` and the
  build carries on, producing a PDF that claims nothing.
* ``testphase=phase-IV`` and ``testphase=latest`` do not exist; the loader emits
  ``LaTeX-lab package 'phase-IV' not found`` and continues, producing an
  **untagged** PDF that looks fine and passes a superficial review.
* An unfilled alt placeholder ships as ``/Alt (<<ALT:f-1a2b3c4d>>)``, which
  satisfies a naive "does every Figure have /Alt" check *and* veraPDF.

Each of those yields a document that appears converted and is not. For material
under a legal accessibility obligation, a false pass is worse than a hard error.
So the pipeline refuses to run when the toolchain cannot deliver what the profile
asks for, and says exactly which capability is missing.

Every probe here is **read-only**: `kpsewhich` plus file reads, no compilation
and no temp files. That keeps `doctor` fast enough to run before every command.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

from .config import Profile

__all__ = [
    "Status",
    "Check",
    "TaggingMode",
    "ToolchainReport",
    "probe",
]


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"

    @property
    def is_blocking(self) -> bool:
        return self is Status.FAIL


class TaggingMode(str, Enum):
    #: LaTeX >= 2025-06-01: `tagging=on`, `pdfstandard=ua-1/ua-2`.
    MODERN = "modern"
    #: Older kernel: `testphase={...}` modules. Tags, but cannot *declare* PDF/UA.
    LEGACY_TESTPHASE = "legacy-testphase"
    #: No usable tagging support at all.
    UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class Check:
    id: str
    label: str
    status: Status
    detail: str = ""
    hint: str | None = None
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status.value,
            "detail": self.detail,
            "hint": self.hint,
            "data": self.data,
        }


@dataclass(slots=True)
class ToolchainReport:
    checks: list[Check] = field(default_factory=list)
    tagging_mode: TaggingMode = TaggingMode.UNAVAILABLE

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.status is Status.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status is Status.WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    def get(self, check_id: str) -> Check | None:
        return next((check for check in self.checks if check.id == check_id), None)

    def as_dict(self) -> dict:
        return {
            "tagging_mode": self.tagging_mode.value,
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
        }


# ---------------------------------------------------------------------- #
# low-level probes
# ---------------------------------------------------------------------- #


@lru_cache(maxsize=256)
def kpsewhich(name: str) -> Path | None:
    """Locate a TeX file, or ``None``. Cached: doctor asks for many files."""
    if shutil.which("kpsewhich") is None:
        return None
    try:
        result = subprocess.run(
            ["kpsewhich", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    path = result.stdout.strip().splitlines()
    return Path(path[0]) if path and Path(path[0]).is_file() else None


def _read(path: Path | None, limit: int = 400_000) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:  # pragma: no cover
        return ""


def _binary_version(binary: str, *args: str) -> str | None:
    if shutil.which(binary) is None:
        return None
    try:
        result = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip().splitlines()[0] if output.strip() else ""


def latex_format_version() -> tuple[str | None, str | None]:
    """(format date, patch level) read from ``latex.ltx``.

    ``\\edef\\fmtversion`` puts its value on the following line, so the pattern
    must span newlines. Reading the file beats compiling a probe document: no
    temp files, no side effects, ~1 ms.
    """
    text = _read(kpsewhich("latex.ltx"))
    if not text:
        return None, None
    version = re.search(r"\\edef\\fmtversion\s*\{\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*\}", text)
    patch = re.search(r"\\def\\patch@level\s*\{\s*(-?\d+)\s*\}", text)
    return (
        version.group(1) if version else None,
        patch.group(1) if patch else None,
    )


def package_version(sty: str) -> tuple[str | None, str | None]:
    """(date, version) from ``\\ProvidesExplPackage``/``\\ProvidesPackage``."""
    text = _read(kpsewhich(sty))
    if not text:
        return None, None
    expl = re.search(
        r"\\ProvidesExplPackage\s*\{[^}]*\}\s*\{\s*([^}]*?)\s*\}\s*\{\s*([^}]*?)\s*\}", text
    )
    if expl:
        return expl.group(1), expl.group(2)
    plain = re.search(
        r"\\ProvidesPackage\s*\{[^}]*\}\s*\[\s*([0-9/\-]+)\s+v?([^\s\]]+)", text
    )
    if plain:
        return plain.group(1), plain.group(2)
    return None, None


def document_metadata_capabilities() -> dict:
    """What ``\\DocumentMetadata`` actually accepts on this machine.

    Parses ``documentmetadata-support.ltx`` rather than trusting the release
    notes, because the installed file is the only thing that governs the build.
    """
    path = kpsewhich("documentmetadata-support.ltx")
    text = _read(path)
    if not text:
        return {"found": False}
    standards = re.search(r"_pdfstandard\s*\.choices:nn\s*=\s*\{([^}]*)\}", text)
    listed = (
        [item.strip().lower() for item in standards.group(1).split(",") if item.strip()]
        if standards
        else []
    )
    # A-4F / A-4E are declared separately as `_pdfstandard / A-4F .code:n`.
    # `unknown` is the error branch of the choice list, not a usable standard.
    listed += [
        match.group(1).strip().lower()
        for match in re.finditer(r"_pdfstandard\s*/\s*([A-Za-z0-9\-]+)\s*\.code:n", text)
    ]
    listed = [item for item in listed if item != "unknown"]
    # `tagging` as a top-level key, not the deprecated `activate / tagging`.
    has_tagging_key = re.search(r"^\s*,?\s*tagging\s*\.(?:code|choice)", text, re.M) is not None
    return {
        "found": True,
        "path": str(path),
        "pdfstandards": sorted(set(listed)),
        "supports_ua": any(item.startswith("ua") for item in listed),
        "supports_tagging_key": has_tagging_key,
        "supports_tagging_setup": ".tagging-setup" in text or "tagging-setup" in text,
    }


def available_testphase_modules() -> list[str]:
    """Testphase module names installed on this machine."""
    anchor = kpsewhich("phase-III-latex-lab-testphase.ltx") or kpsewhich(
        "latex-lab-testphase-graphic.sty"
    )
    if anchor is None:
        return []
    directory = anchor.parent
    modules: set[str] = set()
    for path in directory.iterdir():
        name = path.name
        phase = re.fullmatch(r"(phase-[IV]+)-latex-lab-testphase\.ltx", name)
        if phase:
            modules.add(phase.group(1))
            continue
        module = re.fullmatch(r"latex-lab-testphase-([a-z0-9\-]+)\.sty", name)
        if module:
            modules.add(module.group(1))
    if kpsewhich("tagpdf-latex-lab-testphase.ltx"):
        modules.add("tagpdf")
    return sorted(modules)


def python_dependency(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# ---------------------------------------------------------------------- #
# the doctor
# ---------------------------------------------------------------------- #


def probe(profile: Profile) -> ToolchainReport:
    """Run every toolchain check and decide the tagging mode."""
    report = ToolchainReport()
    add = report.checks.append
    engine = profile.engine

    # --- T001 engine ------------------------------------------------- #
    engine_version = _binary_version(engine.name, "--version")
    if engine_version is None:
        add(
            Check(
                "T001",
                f"TeX engine ({engine.name})",
                Status.FAIL,
                f"{engine.name} not found on PATH",
                "install TeX Live and ensure its bin directory is on PATH",
            )
        )
    else:
        add(Check("T001", f"TeX engine ({engine.name})", Status.OK, engine_version))

    # --- T002 LaTeX format date -------------------------------------- #
    fmt_date, patch = latex_format_version()
    if fmt_date is None:
        add(
            Check(
                "T002",
                "LaTeX kernel",
                Status.FAIL,
                "could not read latex.ltx",
                "check that kpsewhich works and TeX Live is complete",
            )
        )
    else:
        modern = fmt_date >= engine.min_format_date
        detail = f"LaTeX2e <{fmt_date}>" + (f" patch level {patch}" if patch else "")
        add(
            Check(
                "T002",
                "LaTeX kernel",
                Status.OK if modern else Status.WARN,
                detail
                + (
                    ""
                    if modern
                    else f" — older than {engine.min_format_date} required for `tagging=on`"
                ),
                None
                if modern
                else (
                    "install TeX Live 2026, or add latex-dev; on a frozen release "
                    "`tlmgr update --all` cannot help (cross-release update required)"
                ),
                {"format_date": fmt_date, "patch_level": patch, "modern": modern},
            )
        )

    # --- T003/T004 tagging packages ---------------------------------- #
    for check_id, sty, label in (
        ("T003", "tagpdf.sty", "tagpdf"),
        ("T004", "pdfmanagement-testphase.sty", "pdfmanagement-testphase"),
    ):
        date, version = package_version(sty)
        if version is None:
            add(
                Check(
                    check_id,
                    label,
                    Status.FAIL,
                    "not installed",
                    f"install it: tlmgr install {label}",
                )
            )
        else:
            add(
                Check(
                    check_id,
                    label,
                    Status.OK,
                    f"v{version} ({date})",
                    None,
                    {"version": version, "date": date},
                )
            )

    # --- T005 latex-lab modules -------------------------------------- #
    modules = available_testphase_modules()
    if not modules:
        add(
            Check(
                "T005",
                "latex-lab modules",
                Status.FAIL,
                "no testphase modules found",
                "install the latex-lab bundle: tlmgr install latex-lab",
            )
        )
    else:
        add(
            Check(
                "T005",
                "latex-lab modules",
                Status.OK,
                f"{len(modules)} available: {', '.join(modules)}",
                None,
                {"modules": modules},
            )
        )

    # --- T006 PDF/UA declarability ----------------------------------- #
    capabilities = document_metadata_capabilities()
    wants_ua = engine.pdf_standard.lower().startswith("ua")
    if not capabilities.get("found"):
        add(
            Check(
                "T006",
                "PDF/UA declaration",
                Status.FAIL,
                "documentmetadata-support.ltx not found",
                "install a complete latex-lab; without it nothing can be tagged",
            )
        )
    elif wants_ua and not capabilities["supports_ua"]:
        add(
            Check(
                "T006",
                "PDF/UA declaration",
                Status.WARN,
                (
                    f"\\DocumentMetadata{{pdfstandard={engine.pdf_standard}}} is NOT supported; "
                    f"this install accepts only: {', '.join(capabilities['pdfstandards'])}"
                ),
                (
                    "the build will still be tagged, but cannot declare PDF/UA "
                    "conformance in its metadata — upgrade to TeX Live 2026 before "
                    "claiming compliance"
                ),
                capabilities,
            )
        )
    else:
        add(
            Check(
                "T006",
                "PDF/UA declaration",
                Status.OK,
                f"pdfstandard={engine.pdf_standard} accepted",
                None,
                capabilities,
            )
        )

    # --- T007 tagging switch ----------------------------------------- #
    if capabilities.get("supports_tagging_key"):
        add(Check("T007", "`tagging=on` switch", Status.OK, "supported"))
    else:
        usable = [m for m in engine.legacy_testphase if m in modules]
        missing = [m for m in engine.legacy_testphase if m not in modules]
        add(
            Check(
                "T007",
                "`tagging=on` switch",
                Status.WARN if usable else Status.FAIL,
                (
                    "not supported; falling back to testphase modules "
                    f"[{', '.join(usable) or 'none available'}]"
                    + (f"; unavailable: {', '.join(missing)}" if missing else "")
                ),
                (
                    "requested testphase modules are missing and would fail SILENTLY, "
                    "producing an untagged PDF"
                    if not usable
                    else "upgrade to TeX Live 2026 to use the supported `tagging=on` switch"
                ),
                {"usable": usable, "missing": missing},
            )
        )

    # --- T008 TikZ tagging ------------------------------------------- #
    if "tikz" in modules:
        add(
            Check(
                "T008",
                "TikZ/circuitikz tagging",
                Status.OK,
                "latex-lab-testphase-tikz available; `alt=` on pictures is supported",
            )
        )
    else:
        add(
            Check(
                "T008",
                "TikZ/circuitikz tagging",
                Status.WARN,
                "latex-lab-testphase-tikz not installed",
                (
                    "TikZ and circuitikz figures will use the AltOnly wrapper instead "
                    "of the upstream `alt=` key; this is supported but must be retired "
                    "once the module ships"
                ),
            )
        )

    # --- T009..T012 surrounding tools -------------------------------- #
    latexmk = _binary_version("latexmk", "--version")
    add(
        Check(
            "T009",
            "latexmk",
            Status.OK if latexmk else Status.FAIL,
            latexmk or "not found",
            None if latexmk else "install latexmk: tlmgr install latexmk",
        )
    )

    verapdf = shutil.which("verapdf")
    add(
        Check(
            "T010",
            "veraPDF",
            Status.OK if verapdf else Status.WARN,
            verapdf or "not found",
            None
            if verapdf
            else (
                "install from https://docs.verapdf.org/install/ — without it the "
                "authoritative PDF/UA gate cannot run and `check` falls back to "
                "its own structure assertions"
            ),
        )
    )

    node = _binary_version("node", "--version")
    add(
        Check(
            "T011",
            "Node.js (math conversion)",
            Status.OK if node else Status.WARN,
            node or "not found",
            None if node else "install Node 20+ to enable the LaTeX -> MathML -> speech chain",
        )
    )

    optional = {
        "pikepdf": ("pdf", "PDF structure assertions"),
        "textual": ("tui", "the review TUI"),
        "fitz": ("tui", "figure previews in the TUI"),
        "pylatexenc": ("tex", "LaTeX -> plain text for alt strings"),
    }
    missing_optional = {
        module: meta for module, meta in optional.items() if not python_dependency(module)
    }
    if missing_optional:
        extras = sorted({meta[0] for meta in missing_optional.values()})
        add(
            Check(
                "T012",
                "Python extras",
                Status.WARN,
                "missing: "
                + ", ".join(f"{mod} ({meta[1]})" for mod, meta in missing_optional.items()),
                f"pip install 'latexa11y[{','.join(extras)}]'",
                {"missing": sorted(missing_optional)},
            )
        )
    else:
        add(Check("T012", "Python extras", Status.OK, "all optional dependencies present"))

    report.tagging_mode = _decide_mode(report, modules, profile)
    return report


def _decide_mode(
    report: ToolchainReport, modules: list[str], profile: Profile
) -> TaggingMode:
    """Pick the tagging mode the preamble emitter should target."""
    if report.get("T003") and report.get("T003").status is Status.FAIL:
        return TaggingMode.UNAVAILABLE
    capabilities = (report.get("T007") or Check("", "", Status.FAIL)).status
    if capabilities is Status.OK:
        return TaggingMode.MODERN
    usable = [module for module in profile.engine.legacy_testphase if module in modules]
    return TaggingMode.LEGACY_TESTPHASE if usable else TaggingMode.UNAVAILABLE

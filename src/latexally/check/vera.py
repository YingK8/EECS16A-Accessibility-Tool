"""The veraPDF gate: the authoritative PDF/UA-1 check, and its limits.

Why this is deferred to rather than reimplemented
-------------------------------------------------

``check_pdf_structure`` walks ``/K`` by hand and asserts the handful of things
this corpus gets wrong. veraPDF implements the *machine-checkable* 87 of
Matterhorn 1.1's 136 failure conditions, which is a different and much larger
job, and it is the reference implementation the PDF Association maintains. A
second hand-rolled Matterhorn engine would be wrong in ways nobody could audit.

What a clean run does and does not mean
---------------------------------------

It means 87 of 136 conditions of PDF/UA-1 hold. It does not mean the document
is accessible: 47 conditions require human judgment, 2 have no defined test,
and PDF/UA contains no colour-contrast requirement at all -- WCAG 1.4.3 is
among the most commonly failed criteria in this corpus and veraPDF is silent on
it. So this is one input to ``check``, not a verdict, and
``ALLY-PDF-002``/``003``/``004`` (an unfilled ``<<ALT:...>>`` placeholder is
still a ``/Alt``, and passes veraPDF) stay exactly as important as they were.

Mapping a failure back to source
--------------------------------

veraPDF reports object numbers. An object number is useless to someone holding
a ``.tex``, and guessing a source line from it is worse than useless. tagpdf
writes its structure-element labels into the ``.aux`` as
``\\tag@struct@label`` entries, so where the run leaves an ``.aux`` beside the
PDF, the label is used to name the construct that failed. Where it does not,
the finding says "object N" and says so plainly rather than inventing a line
number.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .rules import Finding, Severity

__all__ = ["available", "check_verapdf"]

#: tagpdf writes one of these per labelled structure element. The label is the
#: only thing in the chain that a human wrote, so it is the only thing worth
#: putting in a finding.
_AUX_LABEL = re.compile(r"\\tag@struct@label\s*\{([^}]*)\}\s*\{(\d+)\}")

#: veraPDF's own clause numbering, e.g. "7.1" plus test number 3. Kept as the
#: rule id so a finding is traceable straight into Matterhorn 1.1 rather than
#: into a naming scheme invented here.
_TIMEOUT = 300


def available() -> str | None:
    """Path to the ``verapdf`` binary, or ``None``."""
    return shutil.which("verapdf")


def _labels(pdf: Path) -> dict[str, str]:
    """``object number -> tagpdf label``, from the ``.aux`` beside the PDF."""
    aux = pdf.with_suffix(".aux")
    if not aux.is_file():
        return {}
    try:
        text = aux.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - unreadable aux is not worth failing on
        return {}
    return {number: label for label, number in _AUX_LABEL.findall(text)}


def _findings_from_report(report: dict, pdf: Path) -> list[Finding]:
    """Translate one veraPDF report into findings.

    The shape is nested three deep and every level is easy to get wrong in a
    way that returns an empty list -- which reads as a clean PDF. It was in
    fact wrong once here: the top level is ``report``, not ``jobs``, and
    ``validationResult`` is a *list* (one entry per profile), not a dict. Both
    mistakes produced "0 findings" against a PDF veraPDF had just failed on
    four rules. Hence :func:`check_verapdf` treating a report it cannot read as
    a warning rather than as silence.
    """
    labels = _labels(pdf)
    findings: list[Finding] = []
    for job in report.get("report", report).get("jobs", []):
        for result in job.get("validationResult") or []:
            for rule in result.get("details", {}).get("ruleSummaries", []):
                if rule.get("ruleStatus") == "PASSED":
                    continue
                clause = rule.get("clause", "?")
                test = rule.get("testNumber", "?")
                description = (rule.get("description") or "").strip()
                checks = rule.get("checks") or []
                # veraPDF caps the checks it lists per rule; `failedChecks` is
                # the real count. Reporting one finding per listed check and
                # losing the rest would understate a systematic failure.
                failed = rule.get("failedChecks", len(checks))
                failing = [c for c in checks if c.get("status") == "failed"]
                findings.append(
                    Finding(
                        rule=f"ALLY-VERA-{clause}-{test}",
                        severity=Severity.ERROR,
                        # veraPDF's errorMessage names what is actually wrong
                        # with this file; `description` only restates the
                        # clause. Prefer the former and keep the latter as the
                        # standard reference.
                        message=(
                            (failing[0].get("errorMessage") or "").strip()
                            or description
                            or f"PDF/UA-1 clause {clause} test {test} failed"
                        ),
                        file=str(pdf),
                        standard=f"ISO 14289-1 clause {clause} test {test}; Matterhorn 1.1",
                        hint=_hint(failed, failing, labels, description),
                        data={"clause": clause, "test": test, "failed": failed},
                    )
                )
    return findings


def _hint(failed: int, failing: list[dict], labels: dict[str, str], clause: str) -> str:
    where = _describe([c.get("context", "") for c in failing], labels)
    parts = [f"{failed} occurrence{'s' if failed != 1 else ''}"]
    if where:
        parts.append(f"first at {where}")
    if clause:
        parts.append(clause)
    return "; ".join(parts)


#: veraPDF writes a context as e.g.
#: `root/document[0]/metadata[0](41 0 obj PDMetadata)/XMPPackage[0]`.
#: The object number is the only part tagpdf's `.aux` can be joined on.
_CONTEXT_OBJECT = re.compile(r"\((\d+)\s+\d+\s+obj\b")


def _describe(contexts: list[str], labels: dict[str, str]) -> str:
    """Name the first failing object the way a person can act on."""
    if not contexts:
        return ""
    first = contexts[0]
    match = _CONTEXT_OBJECT.search(first)
    number = match.group(1) if match else None
    if number and number in labels:
        return f"{labels[number]} (object {number})"
    return first[:120]


def check_verapdf(pdf: Path, *, flavour: str = "ua1") -> list[Finding]:
    """Run veraPDF over ``pdf`` and translate its report into findings.

    A missing binary is reported as one INFO finding rather than raised on.
    ``doctor`` T010 is where "you have not installed the authoritative
    validator" belongs; failing a whole ``check`` run over it would make the
    other tiers unreachable, which is the opposite of useful.
    """
    binary = available()
    if binary is None:
        return [
            Finding(
                rule="ALLY-VERA-000",
                severity=Severity.INFO,
                message="veraPDF is not installed; the PDF/UA-1 gate did not run",
                file=str(pdf),
                standard="ISO 14289-1",
                hint=(
                    "install from https://docs.verapdf.org/install/ -- `check` "
                    "falls back to its own structure assertions, which cover far "
                    "less than Matterhorn's 87 machine-checkable conditions"
                ),
            )
        ]
    try:
        result = subprocess.run(
            [binary, "--format", "json", "--flavour", flavour, str(pdf)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [
            Finding(
                rule="ALLY-VERA-000",
                severity=Severity.WARNING,
                message=f"veraPDF could not be run: {error}",
                file=str(pdf),
                standard="ISO 14289-1",
            )
        ]
    # Exit 1 means "the file is invalid", which is a normal outcome here and
    # not an error. Anything else means veraPDF itself failed, and reporting a
    # crash as "no findings" would read as a pass.
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [
            Finding(
                rule="ALLY-VERA-000",
                severity=Severity.WARNING,
                message="veraPDF produced no readable report",
                file=str(pdf),
                standard="ISO 14289-1",
                hint=(result.stderr or result.stdout or "").strip()[:300] or "no output",
            )
        ]
    return _findings_from_report(report, pdf)

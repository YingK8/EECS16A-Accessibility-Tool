r"""Measure the whole questionBank without running LaTeX.

Why this exists
---------------
The corpus *is* the specification. A repair rule that fixes the exam in front of
you and breaks four discussions is not a fix, and nothing short of running every
assignment can tell you which one you wrote. But 1,500 assignments times
pdflatex is an hour, so the check that has to run on every edit cannot compile
anything.

Almost everything that breaks an old document breaks it before TeX typesets a
single box: the wrong file was handed to pdflatex, or a file it ``\input``s is
not on disk. Both are answerable by reading the source, in seconds. That is what
this measures.

What it does not measure is what happens *after* the preamble loads -- a macro
the era's ``ee16.sty`` no longer defines, a package that changed its interface.
Those need :mod:`tests.test_corpus`'s opt-in compile tier.

Deliberately not part of the package
------------------------------------
This is test-owned. ``src/`` must not grow a dependency on a diagnostic, and the
AI iteration harness under ``tools/`` imports *this* rather than the other way
round -- see :mod:`tools.repair_lab`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from latexally.config import Profile, load_profile
from latexally.discover import discover_assignments
from latexally.repair import find_replacements, unresolved_references

#: Written by ``python -m tools.repair_lab.sweep --write``, read by the test.
#: A checked-in file rather than a threshold, so a change in *which* documents
#: resolve shows up as a diff even when the count happens to stay the same.
BASELINE = Path(__file__).with_name("corpus_baseline.json")


@dataclass(slots=True)
class DocumentResult:
    """One (assignment, variant) pair, as the sweep sees it."""

    assignment: str
    variant: str
    driver: str
    #: References still missing after the full repair ladder has been applied.
    #: Each is the target exactly as the source spells it.
    unresolved: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved

    @property
    def key(self) -> str:
        return f"{self.assignment}::{self.variant}"


def sweep_document(
    corpus_root: Path, assignment: str, variant: str, driver: str
) -> DocumentResult:
    """Resolve one document's include graph, repairs included.

    Runs the same :func:`~latexally.repair.unresolved_references` the build runs
    and then the same :func:`~latexally.repair.find_replacements`, so a document
    the sweep calls clean is one the build can actually assemble. The mirror root
    is a path that is never written to -- ``find_replacements`` only computes
    destinations, and this asks it for the choice, not the copy.
    """
    driver_path = corpus_root / assignment / driver
    missing = unresolved_references(driver_path, corpus_root)
    if not missing:
        return DocumentResult(assignment, variant, driver)

    substitutions = find_replacements(
        missing,
        corpus_root=corpus_root,
        mirror_root=corpus_root / "__sweep__",
        semester=assignment.split("/", 1)[0],
        build_dir=assignment,
    )
    # A placeholder is not a repair. The file exists nowhere in the corpus, so
    # the build states the gap instead of dying -- but the reference is still
    # unresolved, and counting it as fixed would quietly retire 68 documents
    # from the only list that tracks them.
    repaired = {
        (str(item.referenced_by), item.wanted)
        for item in substitutions
        if not item.placeholder
    }
    return DocumentResult(
        assignment,
        variant,
        driver,
        sorted(
            {target for source, target in missing if (str(source), target.strip()) not in repaired}
        ),
    )


def sweep(profile: Profile | None = None) -> list[DocumentResult]:
    """Every buildable document in the corpus, in discovery order."""
    profile = profile or load_profile("eecs16a")
    root = profile.corpus.root.resolve()
    return [
        sweep_document(root, item.path, variant, driver)
        for item in discover_assignments(profile)
        for variant, driver in sorted(item.drivers.items())
    ]


def as_baseline(results: list[DocumentResult]) -> dict[str, dict]:
    """The sweep as the checked-in JSON: only what a regression would change.

    Documents that resolve cleanly are recorded as their driver alone. Documents
    that do not also carry the list of what is missing, because "still 3
    unresolved" hides a swap of one broken reference for another.
    """
    out: dict[str, dict] = {}
    for result in sorted(results, key=lambda r: r.key):
        entry: dict = {"driver": result.driver}
        if result.unresolved:
            entry["unresolved"] = result.unresolved
        out[result.key] = entry
    return out


def read_baseline() -> dict[str, dict]:
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def write_baseline(results: list[DocumentResult]) -> None:
    BASELINE.write_text(
        json.dumps(as_baseline(results), indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def compare(results: list[DocumentResult], baseline: dict[str, dict]) -> dict[str, list]:
    """``{regressed, fixed, appeared, vanished}`` against a recorded baseline."""
    current = as_baseline(results)
    report: dict[str, list] = {
        "regressed": [],
        "fixed": [],
        "appeared": sorted(set(current) - set(baseline)),
        "vanished": sorted(set(baseline) - set(current)),
    }
    for key in sorted(set(current) & set(baseline)):
        before = baseline[key].get("unresolved", [])
        after = current[key].get("unresolved", [])
        if set(after) - set(before) or current[key]["driver"] != baseline[key]["driver"]:
            report["regressed"].append(key)
        elif set(before) - set(after):
            report["fixed"].append(key)
    return report

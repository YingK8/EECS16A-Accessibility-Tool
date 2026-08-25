r"""Actually build documents, and say who broke them.

The static sweep in :mod:`tests.corpus_sweep` answers "can this document find
its files". This answers the only question that finally matters -- "does a
tagged PDF come out" -- and it costs a minute per document, so it is opt-in
(``pytest -m corpus``) and normally driven over a sample.

The verdicts are three, not two, because "failed" collapses two situations that
need opposite responses:

``ok``          a PDF came out with a clean log
``inherited``   the untouched source fails the same way; the corpus is rotted,
                not the conversion. Nothing here can fix it and nothing should
                pretend to.
``regression``  the source built and the converted copy did not, or failed in a
                new way. This is a bug in the tool, and the only bucket worth
                acting on.

Every build writes into its own output root and reads the corpus read-only, so
this can run against the live questionBank without touching it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from latexally.build import build_assignment
from latexally.config import Profile, load_profile
from latexally.discover import Assignment, discover_assignments
from latexally.errors import LatexAllyError
from latexally.run import Output, RunConfig
from speech_audit import audit_speech


#: Checker rules that report missing human-written description rather than a
#: broken build: a filename left in /Alt, a formula with none. The document
#: compiled; someone has to write a sentence.
_AUTHORING_GAPS = ("ALLY-PDF-004", "ALLY-PDF-040")


def _is_authoring_gap(message: str) -> bool:
    return any(rule in message for rule in _AUTHORING_GAPS)


@dataclass(slots=True)
class CompileResult:
    """One built document and the verdict on it."""

    assignment: str
    variant: str
    driver: str
    verdict: str
    errors: list[str]

    @property
    def key(self) -> str:
        return f"{self.assignment}::{self.variant}"


def sample(profile: Profile, count: int, seed: int) -> list[Assignment]:
    """``count`` assignments drawn reproducibly from the whole corpus.

    Seeded, because a sample that changes between runs cannot tell a fix from a
    lucky draw. Sorted before sampling so the population does not depend on
    filesystem order either.
    """
    population = sorted(discover_assignments(profile), key=lambda item: item.path)
    picker = random.Random(seed)
    return picker.sample(population, min(count, len(population)))


def compile_one(
    assignment: Assignment, variant: str, driver: str, out: Path, profile: Profile
) -> CompileResult:
    """Build one variant into ``out`` and classify the outcome."""
    config = RunConfig(
        assignments=[assignment.path],
        write=True,
        output=Output(root=out, write_mode="mirror"),
    )
    try:
        report = build_assignment(assignment, config, profile, variant=variant, driver=driver)
    except LatexAllyError as exc:
        return CompileResult(assignment.path, variant, driver, "error", [str(exc)])

    #: Findings that ask a HUMAN for something, on a document that built. They
    #: are not conversion failures and counting them as such buried 20 of 55
    #: "regressions" that were really "this figure still needs alt text".
    if report.ok:
        verdict = "ok"
    elif all(_is_authoring_gap(item) for item in (report.regression or report.errors)):
        verdict = "needs-alt"
    elif report.regression:
        verdict = "regression"
    elif report.inherited:
        verdict = "inherited"
    else:
        # No PDF, no baseline errors: the engine never ran, or died with a log
        # nothing could parse. Its own bucket, so it cannot hide inside either
        # of the two that have a diagnosis attached. Almost always the toolchain
        # rather than the document: a TeX Live upgrade mid-run puts every
        # document here at once, which is the signal to stop measuring.
        verdict = "unexplained"
    # Reading the artifact, not the log. Every verdict above comes from
    # pdflatex's own account of the run, and both page-corruption bugs found
    # this session were invisible there and obvious the moment someone read the
    # page. A document that builds cleanly and reads as gibberish is not ok.
    # Run on any document that produced a PDF, not only a clean one. Gating on
    # `ok` meant a document with both a build regression and a speech defect
    # reported only the regression, so the sweep tally said unspeakable=0 while
    # a direct audit of the same PDFs found two.
    if report.pdf is not None:
        try:
            audit = audit_speech(report.pdf)
        except Exception:
            audit = None
        if audit is not None and audit.defects:
            # A build regression is the more actionable finding, so it keeps
            # the verdict; the speech defect is still surfaced in the errors.
            verdict = "unspeakable" if verdict == "ok" else verdict
            return CompileResult(
                assignment.path,
                variant,
                driver,
                verdict,
                [str(defect) for defect in audit.defects[:5]],
            )

    return CompileResult(
        assignment.path,
        variant,
        driver,
        verdict,
        report.regression or report.errors or ([report.note] if report.note else []),
    )


def compile_sample(
    count: int = 12,
    seed: int = 16,
    out: Path | None = None,
    profile: Profile | None = None,
) -> list[CompileResult]:
    """Build every variant of ``count`` sampled assignments."""
    profile = profile or load_profile("eecs16a")
    out = out or Path(".lab-out/compile")
    return [
        compile_one(item, variant, driver, out, profile)
        for item in sample(profile, count, seed)
        for variant, driver in sorted(item.drivers.items())
    ]

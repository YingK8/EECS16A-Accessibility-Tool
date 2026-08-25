"""The veraPDF gate.

The report shape is parsed from a captured real report rather than a
hand-written one. Both mistakes these tests pin were made against a
hand-reasoned shape and both returned an empty list, which is indistinguishable
from a clean PDF: the top level is `report`, not `jobs`, and `validationResult`
is a list, not a dict.
"""

from __future__ import annotations

import pytest

from latexally.check.rules import Severity
from latexally.check.vera import _findings_from_report, check_verapdf

#: One PDF/UA-1 run of veraPDF 1.30.2 over a tagged pdflatex build, trimmed to
#: two of its four failing rules -- one whose context is `root` and one whose
#: context carries an object number, because those take different paths.
REPORT = {
    "report": {
        "jobs": [
            {
                "validationResult": [
                    {
                        "profileName": "PDF/UA-1 validation profile",
                        "compliant": False,
                        "details": {
                            "passedRules": 102,
                            "failedRules": 2,
                            "ruleSummaries": [
                                {
                                    "ruleStatus": "FAILED",
                                    "clause": "7.1",
                                    "testNumber": 9,
                                    "failedChecks": 1,
                                    "description": (
                                        "The Metadata stream in the document's catalog "
                                        "dictionary shall contain a dc:title entry"
                                    ),
                                    "checks": [
                                        {
                                            "status": "failed",
                                            "context": (
                                                "root/document[0]/metadata[0]"
                                                "(41 0 obj PDMetadata)/XMPPackage[0]"
                                            ),
                                            "errorMessage": (
                                                "Metadata stream does not contain dc:title"
                                            ),
                                        }
                                    ],
                                },
                                {
                                    "ruleStatus": "PASSED",
                                    "clause": "7.2",
                                    "testNumber": 1,
                                    "failedChecks": 0,
                                    "description": "a rule that held",
                                    "checks": [],
                                },
                            ],
                        },
                    }
                ]
            }
        ]
    }
}


def test_a_failing_rule_becomes_a_finding_keyed_by_its_matterhorn_clause(tmp_path):
    findings = _findings_from_report(REPORT, tmp_path / "doc.pdf")

    assert [f.rule for f in findings] == ["ALLY-VERA-7.1-9"]
    assert findings[0].severity == Severity.ERROR
    # veraPDF's own errorMessage, not the clause text -- the clause restates the
    # standard, the message says what is wrong with this file.
    assert findings[0].message == "Metadata stream does not contain dc:title"
    assert "ISO 14289-1 clause 7.1 test 9" in findings[0].standard


def test_a_passing_rule_is_not_reported(tmp_path):
    """`ruleSummaries` carries every rule, not only the failures."""
    findings = _findings_from_report(REPORT, tmp_path / "doc.pdf")

    assert all(f.rule != "ALLY-VERA-7.2-1" for f in findings)


def test_an_object_number_is_joined_back_to_its_tagpdf_label(tmp_path):
    """An object number is useless to someone holding a `.tex`.

    tagpdf writes its structure-element labels into the `.aux`, so where one is
    beside the PDF the finding names the construct rather than the object.
    """
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    (tmp_path / "doc.aux").write_text(r"\tag@struct@label{fig:mask}{41}" + "\n")

    findings = _findings_from_report(REPORT, pdf)

    assert "fig:mask (object 41)" in findings[0].hint


def test_a_missing_aux_names_the_object_rather_than_inventing_a_line(tmp_path):
    findings = _findings_from_report(REPORT, tmp_path / "doc.pdf")

    assert "41 0 obj" in findings[0].hint
    assert findings[0].line is None


def test_an_unreadable_report_is_a_warning_and_never_silence(tmp_path, monkeypatch):
    """The failure mode that matters: 0 findings reads as a clean PDF."""
    import subprocess

    from latexally.check import vera

    monkeypatch.setattr(vera, "available", lambda: "/usr/bin/true")
    monkeypatch.setattr(
        vera.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "not json at all", "boom"),
    )

    findings = check_verapdf(tmp_path / "doc.pdf")

    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert "no readable report" in findings[0].message


def test_a_missing_binary_reports_that_the_gate_did_not_run(tmp_path, monkeypatch):
    from latexally.check import vera

    monkeypatch.setattr(vera, "available", lambda: None)

    findings = check_verapdf(tmp_path / "doc.pdf")

    assert [f.severity for f in findings] == [Severity.INFO]
    assert "did not run" in findings[0].message


def test_real_verapdf_agrees_with_the_captured_shape(tmp_path):
    """Guards against veraPDF changing its report shape under us.

    The fixture above is a *captured* report, which means it goes stale
    silently. This is the test that notices, so it uses a real PDF from a real
    pdflatex and a real veraPDF: an untagged document, which cannot pass
    PDF/UA-1 under any version of the profile.
    """
    import shutil
    import subprocess

    from latexally.check.vera import available

    if available() is None:
        pytest.skip("veraPDF is not installed")
    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex is not installed")

    (tmp_path / "plain.tex").write_text(
        "\\documentclass{article}\n\\begin{document}untagged\\end{document}\n"
    )
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "plain.tex"],
        cwd=tmp_path,
        capture_output=True,
        timeout=180,
        check=False,
    )
    pdf = tmp_path / "plain.pdf"
    if not pdf.is_file():
        pytest.skip("pdflatex produced no PDF")

    findings = check_verapdf(pdf)

    assert findings, "an untagged PDF must not pass the PDF/UA-1 gate"
    assert all(f.rule.startswith("ALLY-VERA-") for f in findings)
    # The shape is being read, not just the presence of output: a clause and a
    # test number came back for every finding.
    assert all(f.data.get("clause") and f.data.get("test") for f in findings)


# ---------------------------------------------------------------------- #
# ALLY-PDF-033 -- macro internals drawn on the page
# ---------------------------------------------------------------------- #


def test_typeset_macro_internals_are_reported(tmp_path):
    r"""The check that exists because every other check missed ulem.

    sp26/hw/7 passed the structure tier with 353 Formula elements, correct
    nesting and 56 bookmarks, while page 10 read "bold cap a is
    1000cmd/emph/after0block upper triangular". The tiers read the structure
    tree and the /Alt coverage; none of them read the page.
    """
    import shutil
    import subprocess

    from latexally.check.rules import _typeset_internals

    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex is not installed")
    pytest.importorskip("pymupdf", reason="reading the page needs PyMuPDF")

    # Six lines reproduce it: \DocumentMetadata alone is the trigger, not
    # tagging -- ulem redefines \emph as a zero-argument macro and LaTeX's
    # command hooks then separate \uline from its argument.
    (tmp_path / "u.tex").write_text(
        "\\DocumentMetadata{lang=en-US,pdfversion=1.7}\n"
        "\\documentclass{article}\n"
        "\\usepackage{ulem}\n"
        "\\begin{document}\nText is \\emph{emphasised here}, done.\n\\end{document}\n"
    )
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "u.tex"],
        cwd=tmp_path, capture_output=True, timeout=180, check=False,
    )
    pdf = tmp_path / "u.pdf"
    if not pdf.is_file():
        pytest.skip("pdflatex produced no PDF")

    findings = _typeset_internals(pdf, "u.tex")

    assert findings, "ulem's internals on the page must be reported"
    assert findings[0].rule == "ALLY-PDF-033"
    assert "cmd/emph/after" in findings[0].message


def test_a_clean_page_reports_nothing(tmp_path):
    """No false positives on ordinary prose, including a real backslash word."""
    import shutil
    import subprocess

    from latexally.check.rules import _typeset_internals

    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex is not installed")
    pytest.importorskip("pymupdf", reason="reading the page needs PyMuPDF")

    (tmp_path / "c.tex").write_text(
        "\\DocumentMetadata{lang=en-US,pdfversion=1.7}\n"
        "\\documentclass{article}\n\\usepackage{ulem}\\normalem\n"
        "\\renewcommand{\\emph}[1]{\\uline{#1}}\n"
        "\\begin{document}\nText is \\emph{emphasised here}, done.\n\\end{document}\n"
    )
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "c.tex"],
        cwd=tmp_path, capture_output=True, timeout=180, check=False,
    )
    pdf = tmp_path / "c.pdf"
    if not pdf.is_file():
        pytest.skip("pdflatex produced no PDF")

    assert _typeset_internals(pdf, "c.tex") == []

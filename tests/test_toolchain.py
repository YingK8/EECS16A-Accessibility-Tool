r"""The `doctor` probes, and the one that was silently wrong.

`document_metadata_capabilities` parses the installed sources rather than
trusting release notes, because the installed file is the only thing that
governs the build. That is the right instinct and it still went wrong: the
`pdfstandard` key moved out of `documentmetadata-support.ltx` and into
`pdfmanagement.ltx`, so the probe found no standards at all and reported
"PDF/UA is NOT supported" on an install that accepts `ua-1` perfectly well.

Measured consequence: the build then omitted `pdfstandard=ua-1` from
`\DocumentMetadata`, and veraPDF failed the PDF on 5-1 (no PDF/UA
identification schema) and 7.1-10 (no DisplayDocTitle). With the declaration
restored, veraPDF reports zero findings.
"""

from __future__ import annotations

import pytest

from latexally.toolchain import document_metadata_capabilities

CHOICES = r"""
\keys_define:nn { document / metadata }
 {
   _pdfstandard .choices:nn =
      {A-1B,A-2A,A-2B}
      { \AddToDocumentProperties [document]{pdfstandard}{#1} },
   _pdfstandard / unknown .code:n = { \msg_warning:nn {} {} },
   _pdfstandard / UA-1 .code:n = { \pdfmeta_standard_family:nn{UA}{} },
   _pdfstandard / UA-2 .code:n = { \pdfmeta_standard_family:nn{UA}{} },
 }
"""

TAGGING_ONLY = r"""
\keys_define:nn { document / metadata }
 {
    tagging .choice:
   ,pdfstandard .groups:n = { pdf }
 }
"""


def _fake_kpsewhich(monkeypatch, files: dict[str, str], tmp_path):
    """Point the probe at files we control, by name."""
    from latexally import toolchain

    written = {}
    for name, body in files.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        written[name] = path
    monkeypatch.setattr(toolchain, "kpsewhich", lambda name: written.get(name))


def test_the_standards_are_found_when_the_key_lives_in_pdfmanagement(monkeypatch, tmp_path):
    """The regression. `documentmetadata-support.ltx` no longer declares them."""
    _fake_kpsewhich(
        monkeypatch,
        {
            "documentmetadata-support.ltx": TAGGING_ONLY,
            "pdfmanagement.ltx": CHOICES,
        },
        tmp_path,
    )

    capabilities = document_metadata_capabilities()

    assert capabilities["supports_ua"] is True
    assert "ua-1" in capabilities["pdfstandards"]
    assert "ua-2" in capabilities["pdfstandards"]
    # The choice list is read too, not only the separate `.code:n` branches.
    assert "a-2b" in capabilities["pdfstandards"]
    # And the error branch of the choice list is not a usable standard.
    assert "unknown" not in capabilities["pdfstandards"]


def test_the_standards_are_still_found_in_the_older_location(monkeypatch, tmp_path):
    """An install that has not moved the key must keep working."""
    _fake_kpsewhich(monkeypatch, {"documentmetadata-support.ltx": CHOICES}, tmp_path)

    capabilities = document_metadata_capabilities()

    assert capabilities["supports_ua"] is True
    assert "ua-1" in capabilities["pdfstandards"]


def test_the_tagging_key_is_seen_in_either_file(monkeypatch, tmp_path):
    _fake_kpsewhich(
        monkeypatch,
        {"documentmetadata-support.ltx": TAGGING_ONLY, "pdfmanagement.ltx": CHOICES},
        tmp_path,
    )

    assert document_metadata_capabilities()["supports_tagging_key"] is True


def test_an_install_with_neither_file_reports_not_found(monkeypatch, tmp_path):
    _fake_kpsewhich(monkeypatch, {}, tmp_path)

    assert document_metadata_capabilities() == {"found": False}


def test_no_standard_is_claimed_when_none_is_declared(monkeypatch, tmp_path):
    """The probe must not become optimistic to make the warning go away."""
    _fake_kpsewhich(monkeypatch, {"documentmetadata-support.ltx": TAGGING_ONLY}, tmp_path)

    capabilities = document_metadata_capabilities()

    assert capabilities["found"] is True
    assert capabilities["supports_ua"] is False
    assert capabilities["pdfstandards"] == []


@pytest.mark.parametrize("standard", ["ua-1", "ua-2"])
def test_the_real_install_is_probed_consistently(standard):
    """Whatever this machine says, it must say it the same way twice."""
    first = document_metadata_capabilities()
    second = document_metadata_capabilities()

    assert first == second
    if first.get("supports_ua"):
        assert any(item.startswith("ua") for item in first["pdfstandards"])

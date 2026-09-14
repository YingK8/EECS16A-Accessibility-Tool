r"""Course notation rules, read from the shipped profile and applied to maths."""

from __future__ import annotations

from pathlib import Path

import pytest

from latexally.config import load_profile
from latexally.errors import ConfigError
from latexally.notation import apply_notation, check_notation
from latexally.texlex import TexSource

RULES = load_profile("eecs16a", corpus_root=".").notation


def fix(text: str) -> str:
    return apply_notation(text, RULES)[0]


@pytest.mark.parametrize(
    "before, after",
    [
        (r"$\vec{x} + \vec v$", r"$\mathbf{x} + \mathbf{v}$"),
        (r"\[\overrightarrow{AB}\]", r"\[\mathbf{AB}\]"),
        (r"$\overline{z}^2$", r"${z^{*}}^2$"),
        (r"$\overline{x+y}$", r"${(x+y)^{*}}$"),
        (r"$\overline{\hat{\psi}}$", r"${\hat{\psi}^{*}}$"),
        (r"$\overline{\vec{x}}$", r"${\mathbf{x}^{*}}$"),
        (r"$x \in \ell^2$, $\ell^{\infty}$", r"$x \in \ell_2$, $\ell_{\infty}$"),
        (r"$\sum_n x(n) h(n-k)$", r"$\sum_n x[n] h[n-k]$"),
        (r"\begin{align*} y(n) &= 0 \end{align*}", r"\begin{align*} y[n] &= 0 \end{align*}"),
        (r"$\ell^1$ and $\ell^p$", r"$\ell_1$ and $\ell_p$"),
    ],
)
def test_each_rule_rewrites_maths(before, after):
    assert fix(before) == after


@pytest.mark.parametrize(
    "text",
    [
        r"$\frac{1}{i(k-m)\omega_0}$",     # a product, not a signal
        r"$e^{i\omega_k(n-2)}$",           # likewise
        r"$(\bar{x}, \bar{y})$",           # a mean, not a conjugate
        r"$x(t)$ and $x(nT)$",             # continuous time
        r"use a(n) matrix",                # prose, not maths
        "% $x(n)$ and $\\vec{v}$\n",       # a comment
        r"$\ell^2_x$",                     # would become a double subscript
        r"$\ell^\top x$",                  # a transposed vector named ell
        r"$c_n(n - 1)t^{n-2}$",            # a coefficient times (n-1)
    ],
)
def test_lookalikes_are_left_alone(text):
    assert fix(text) == text


def test_check_reports_line_and_fix():
    source = TexSource("text\n$\\ell^2$\n")
    [finding] = check_notation(source, RULES, "q.tex")
    assert finding.rule == "ALLY-FMT-ell-subscript"
    assert finding.line == 2
    assert finding.data["fix"] == r"\ell_2"


def test_report_only_rules_are_not_applied(tmp_path: Path):
    profile = tmp_path / "p.yaml"
    profile.write_text(
        "notation:\n"
        "  - {id: v, macro: vec, to: '\\mathbf{{arg}}', fix: false}\n"
    )
    rules = load_profile(profile).notation
    assert apply_notation(r"$\vec{x}$", rules) == (r"$\vec{x}$", {})
    assert check_notation(TexSource(r"$\vec{x}$"), rules, "q.tex")


def test_a_plan_carries_a_diff_and_writes_only_what_changed(tmp_path: Path):
    """`notation --write` and the TUI both write through a plan, so the diff a
    reader is shown is the edit that lands."""
    from latexally.notation import plan_files

    changes = tmp_path / "q.tex"
    changes.write_text(r"$\vec{x}$", encoding="utf-8")
    steady = tmp_path / "clean.tex"
    steady.write_text(r"$\mathbf{x}$", encoding="utf-8")

    [plan] = plan_files([changes, steady], RULES)

    assert plan.path == changes
    assert plan.counts == {"ALLY-FMT-bold-vector": 1}
    assert r"+$\mathbf{x}$" in plan.diff()
    assert changes.read_text() == r"$\vec{x}$", "planning must not write"
    assert plan.write() is True
    assert changes.read_text() == r"$\mathbf{x}$"


@pytest.mark.parametrize(
    "rule",
    [
        "{id: a, pattern: '(', to: x}",                   # bad regex
        "{id: a, pattern: '(x)', to: '\\2'}",             # group out of range
        "{id: a, macro: vec, pattern: 'x', to: y}",       # both shapes
        "{macro: vec, to: y}",                            # no id
    ],
)
def test_a_bad_rule_fails_at_load(tmp_path: Path, rule: str):
    profile = tmp_path / "p.yaml"
    profile.write_text(f"notation:\n  - {rule}\n")
    with pytest.raises(ConfigError):
        load_profile(profile)

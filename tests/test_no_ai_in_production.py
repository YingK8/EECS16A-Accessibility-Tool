r"""The wall between the shipped pipeline and the harness that improves it.

``src/latexally/`` decides where a missing file comes from. Those decisions land
in course material that carries a legal obligation, so they have to be the same
on every machine, every run, forever -- which rules out asking a model. The
model's place is one level up: it reads what still fails and proposes a *rule*,
a person implements the rule, and the corpus sweep decides whether it stays.

Nothing enforces that by convention alone, so it is enforced here.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "latexally"

#: Import names that mean a model is being called at runtime. Substring match on
#: the top-level module, so `anthropic.resources` and `openai` both trip it.
_MODEL_PACKAGES = (
    "anthropic",
    "openai",
    "google.generativeai",
    "langchain",
    "litellm",
    "transformers",
    "ollama",
    "cohere",
    "mistralai",
)


def _imports(path: Path) -> set[str]:
    """Every module name imported by one file, as written."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken file fails its own tests
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_no_module_imports_the_lab_or_a_model(path: Path):
    """No file under ``src/`` reaches for the harness or for a model provider."""
    for name in _imports(path):
        top = name.split(".")[0]
        assert top != "tools", f"{path.relative_to(ROOT)} imports the iteration harness"
        assert not any(name.startswith(pkg) for pkg in _MODEL_PACKAGES), (
            f"{path.relative_to(ROOT)} imports {name}: the shipped pipeline must "
            "make the same choice on every machine, which a model cannot promise"
        )


def test_no_model_provider_is_a_dependency():
    """Not in the runtime dependencies, and not in the dev group either.

    A dev-group dependency is still one `uv sync` away from being imported by
    something under ``src/`` without any test noticing.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(data["project"].get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        declared.extend(item for item in group if isinstance(item, str))
    for extra in data["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)

    for requirement in declared:
        name = requirement.split()[0].split(">")[0].split("=")[0].split("[")[0].lower()
        assert name not in _MODEL_PACKAGES, f"{requirement} is a model provider"


def test_the_harness_is_not_shipped():
    """``tools/`` is outside the packaged tree, so a wheel cannot carry it."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    where = data["tool"]["setuptools"]["packages"]["find"]["where"]
    assert where == ["src"], "packages are found outside src/, which would ship tools/"
    assert not (ROOT / "src" / "tools").exists()

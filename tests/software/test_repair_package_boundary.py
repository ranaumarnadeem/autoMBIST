"""Mechanically enforce the one-way dependencies that keep autoMBIST's pure
packages copy-paste-extractable: a package under ``src/autombist/`` listed in
``PURE_PACKAGES`` may import only the Python standard library, its own modules,
and the sibling packages it declares. Nothing else from autoMBIST (generator,
runner, reporting, cli, ...) and no third-party module.

This is an AST import-graph guard, an allow-list rather than a deny-list, so it
catches a new coupling the moment it is written, whatever its name, including
``from .. import generator`` (a relative import with no module name) and dynamic
imports through ``importlib`` or ``__import__``.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "autombist"

#: package -> the sibling autombist packages it may import (one-way).
PURE_PACKAGES: dict[str, frozenset[str]] = {
    "repair": frozenset(),
    "yieldsim": frozenset({"repair"}),
}

#: Modules that sit in each package and must exist, so an empty glob cannot pass.
EXPECTED_MODULES: dict[str, set[str]] = {
    "repair": {"__init__.py", "types.py", "bira.py", "bisr.py"},
    "yieldsim": {"__init__.py", "stats.py", "sweep.py", "model.py"},
}

_STDLIB = frozenset(sys.stdlib_module_names)
_DYNAMIC = frozenset({"importlib"})


def _targets(node: ast.AST, package: str) -> list[str]:
    """The fully qualified modules an import statement in ``autombist.<package>``
    reaches, e.g. ``["autombist.repair.types"]``, ``["autombist.generator"]`` or
    ``["math"]``."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level == 0:
        return [node.module or ""]
    # Relative: level 1 is the package itself, level 2 is autombist, deeper climbs out of it.
    base = ["autombist", package][: max(0, 3 - node.level)] if node.level <= 2 else ["<outside autombist>"]
    if node.module:
        return [".".join(base + [node.module])]
    return [".".join(base + [alias.name]) for alias in node.names]


def _violations(package: str) -> list[tuple[str, str]]:
    allowed = {package, *PURE_PACKAGES[package]}
    offenders: list[tuple[str, str]] = []
    for path in sorted((SRC / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                offenders.append((path.name, "__import__(...)"))
            for target in _targets(node, package):
                parts = target.split(".")
                if parts[0] == "autombist":
                    if len(parts) < 2 or parts[1] not in allowed:
                        offenders.append((path.name, target))
                elif parts[0] in _DYNAMIC or parts[0] not in _STDLIB:
                    offenders.append((path.name, target))
    return offenders


@pytest.mark.parametrize("package", sorted(PURE_PACKAGES))
def test_pure_package_imports_only_stdlib_itself_and_declared_siblings(package: str) -> None:
    offenders = _violations(package)
    assert not offenders, (
        f"autombist.{package} may import only the standard library, itself and "
        f"{sorted(PURE_PACKAGES[package]) or 'no sibling package'} (the extractability boundary); "
        f"found: {offenders}"
    )


@pytest.mark.parametrize("package", sorted(PURE_PACKAGES))
def test_pure_package_dir_has_the_expected_modules(package: str) -> None:
    files = {p.name for p in (SRC / package).rglob("*.py")}
    assert EXPECTED_MODULES[package] <= files


@pytest.mark.parametrize(("source", "reached"), [
    ("from .types import X", "autombist.repair.types"),
    ("from . import bira", "autombist.repair.bira"),
    ("from .. import generator", "autombist.generator"),
    ("from ..runner import run", "autombist.runner"),
    ("import autombist.cli", "autombist.cli"),
    ("from ... import x", "<outside autombist>.x"),
    ("import math", "math"),
])
def test_the_guard_resolves_every_import_form(source: str, reached: str) -> None:
    node = ast.parse(source).body[0]
    assert _targets(node, "repair") == [reached]


@pytest.mark.parametrize(("source", "bad"), [
    ("from .. import generator\n", True),
    ("import autombist.reporting\n", True),
    ("import numpy\n", True),
    ("import importlib\n", True),
    ("__import__('autombist.cli')\n", True),
    ("from .types import SpareGeometry\nimport math\nfrom dataclasses import dataclass\n", False),
])
def test_the_guard_flags_couplings_and_passes_clean_code(tmp_path: Path, monkeypatch, source: str, bad: bool) -> None:
    pkg = tmp_path / "probe"
    pkg.mkdir()
    (pkg / "mod.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    monkeypatch.setitem(PURE_PACKAGES, "probe", frozenset())
    assert bool(_violations("probe")) is bad


@pytest.mark.parametrize(("source", "bad"), [
    ("from ..repair import bira\n", False),
    ("from ..repair.types import SpareGeometry\n", False),
    ("import autombist.repair.bira\n", False),
    ("from ..yield_sweep import sweep\n", True),
])
def test_a_declared_sibling_is_the_only_autombist_import_allowed(tmp_path: Path, monkeypatch, source: str, bad: bool) -> None:
    pkg = tmp_path / "probe"
    pkg.mkdir()
    (pkg / "mod.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    monkeypatch.setitem(PURE_PACKAGES, "probe", frozenset({"repair"}))
    assert bool(_violations("probe")) is bad

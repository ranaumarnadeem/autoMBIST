"""Mechanically enforce the one-way dependency that keeps `autombist.repair`
copy-paste-extractable: nothing under `src/autombist/repair/` may import
`autombist.generator` or `autombist.runner` (or their submodules). This is an
AST import-graph guard, not a runtime check -- it catches a coupling the moment
it is written, not only when it happens to execute.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPAIR_DIR = REPO_ROOT / "src" / "autombist" / "repair"

FORBIDDEN = frozenset({"generator", "runner"})


def _referenced_modules(tree: ast.AST) -> set[str]:
    """Every module name referenced by an import statement, as written.

    `from .types import x` -> "types"; `from ..runner import x` -> "runner";
    `import autombist.generator` -> "autombist.generator"; `from . import x` ->
    (nothing -- node.module is None)."""
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_repair_package_never_imports_generator_or_runner() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted(REPAIR_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _referenced_modules(tree):
            if any(part in FORBIDDEN for part in module.split(".")):
                offenders.append((path.name, module))
    assert not offenders, (
        "autombist.repair must not import generator/runner (the extractability "
        f"boundary); found: {offenders}"
    )


def test_repair_dir_has_the_expected_modules() -> None:
    # Guard against a vacuous pass if the dir/glob is ever empty.
    files = {p.name for p in REPAIR_DIR.rglob("*.py")}
    assert {"__init__.py", "types.py", "bira.py"} <= files

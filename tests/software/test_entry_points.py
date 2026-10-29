"""Every way the CLI can be started prints the same version: the console
script's module (main.py), `python -m autombist`, `python -m autombist.cli`,
and running src/autombist/cli.py or main.py as a plain script. A plain script
has no package, so cli.py and tcl_shell.py carry a second, absolute copy of
their imports for that case; the two copies must name the same things.

The modules run in this process (runpy), so coverage sees them.
"""
from __future__ import annotations

import ast
import runpy
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist import __version__

PACKAGE_DIR = SRC_ROOT / "autombist"


@pytest.fixture
def argv(monkeypatch):
    """Run with ``autombist --version`` as the command line, and undo the
    sys.path entry a plain-script start adds."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["autombist", "--version"])


def _assert_prints_the_version(start, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        start()
    assert exit_info.value.code in (0, None)
    assert capsys.readouterr().out == f"autombist {__version__}\n"


@pytest.mark.parametrize("script", ["cli.py", "main.py"])
def test_running_the_file_as_a_script_prints_the_version(argv, capsys, script: str) -> None:
    _assert_prints_the_version(lambda: runpy.run_path(str(PACKAGE_DIR / script), run_name="__main__"), capsys)


@pytest.mark.parametrize("module", ["autombist", "autombist.cli", "autombist.main"])
def test_running_the_module_with_python_m_prints_the_version(argv, capsys, module: str) -> None:
    """`python -m autombist.cli` once defined the app and exited 0 without
    running it; each of these must run the CLI."""
    with warnings.catch_warnings():
        # The modules are already imported by this test session.
        warnings.simplefilter("ignore", RuntimeWarning)
        _assert_prints_the_version(lambda: runpy.run_module(module, run_name="__main__"), capsys)


def test_tcl_shell_loads_as_a_plain_file(monkeypatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))

    names = runpy.run_path(str(PACKAGE_DIR / "tcl_shell.py"), run_name="tcl_shell_as_a_file")

    assert names["__package__"] == ""
    assert {"TclShell", "generate_from_config", "run_simulation", "normalize_lef_units"} <= names.keys()


def _imported_names(statements: list[ast.stmt]) -> set[tuple[str, str]]:
    """(module within autombist, bound name) for each from-import, written
    either way: ``from autombist.x import y`` or ``from .x import y``."""
    names = set()
    for statement in statements:
        if isinstance(statement, ast.ImportFrom):
            module = statement.module or ""
            if statement.level == 0:
                module = module.removeprefix("autombist").removeprefix(".")
            for alias in statement.names:
                names.add((module, alias.asname or alias.name))
    return names


@pytest.mark.parametrize("source", ["cli.py", "tcl_shell.py"])
def test_the_script_imports_match_the_package_imports(source: str) -> None:
    tree = ast.parse((PACKAGE_DIR / source).read_text(encoding="utf-8"))
    branch = next(
        node for node in tree.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name) and node.test.left.id == "__package__"
    )

    script_names = _imported_names(branch.body)
    package_names = _imported_names(branch.orelse)

    assert script_names and script_names == package_names

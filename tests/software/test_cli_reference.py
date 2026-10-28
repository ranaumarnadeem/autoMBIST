"""The CLI reference matches the CLI, the way OpenSTA's CI checks that every
command has its help: every command `autombist --help` lists has a section, every
option of a command appears in that section, and every flag a section's option
table names still exists. The Tcl shell (`autombist shell`) registers the commands
its section lists, with the options of their CLI counterparts.

The commands come from the Typer app itself (typer.main.get_command), the shell's
flags from tcl_shell.py's source (each `_cmd_<name>` reads its flags through
`_pop_*(flags, "-flag", ...)`), so neither needs Tcl installed. `test` and `algo`
are documented in algo-shell-guide.md, as the reference's overview says.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from typer.main import get_command

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.cli import app  # noqa: E402

DOCS = REPO_ROOT / "docs" / "source"
REFERENCE = (DOCS / "cli-reference.md").read_text(encoding="utf-8")
GUIDE = (DOCS / "algo-shell-guide.md").read_text(encoding="utf-8")
TCL_SHELL = SRC_ROOT / "autombist" / "tcl_shell.py"

#: Sections of cli-reference.md that document no command.
NON_COMMAND_SECTIONS = {"JSON report schema (`reports/latest.json` / `reports/results.json`)"}
#: CLI options the Tcl shell does not offer, and why.
SHELL_OMITS = {"--json": "a shell command returns its result as a Tcl value"}

GROUP = get_command(app)


def _options(command) -> list[tuple[list[str], list[str]]]:
    """(names, negated names) of every option; typer's own click and stock click
    both mark options with param_type_name."""
    return [(list(p.opts), list(p.secondary_opts)) for p in command.params
            if getattr(p, "param_type_name", "") == "option"]


COMMANDS: dict[str, list[tuple[list[str], list[str]]]] = {
    name: _options(cmd) for name, cmd in GROUP.commands.items() if not cmd.hidden
}


def _sections(text: str, heading: str) -> dict[str, str]:
    """{name: section text} for each level-2 heading matching ``heading``."""
    marks = [(m.start(), m.group(1)) for m in re.finditer(heading, text, re.M)]
    return {name: text[start:(marks[i + 1][0] if i + 1 < len(marks) else len(text))]
            for i, (start, name) in enumerate(marks)}


REFERENCE_SECTIONS = _sections(REFERENCE, r"^## (.+?)\s*$")
GUIDE_SECTIONS = _sections(GUIDE, r"^## \d+\. `autombist ([a-z-]+)`")
SECTIONS = {**{k: v for k, v in REFERENCE_SECTIONS.items() if k not in NON_COMMAND_SECTIONS}, **GUIDE_SECTIONS}
OVERVIEW = REFERENCE.split("\n## ", 1)[0]


def _mentions(text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


def _table_flags(section: str) -> set[str]:
    """Every -flag in the first column of the section's option tables."""
    flags: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| `-"):
            flags |= set(re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", line.split("|")[1]))
    return flags


def test_every_command_has_a_section() -> None:
    missing = sorted(set(COMMANDS) - set(SECTIONS))
    assert not missing, f"commands with no section in cli-reference.md or algo-shell-guide.md: {missing}"


def test_every_section_names_a_command() -> None:
    stale = sorted(set(SECTIONS) - set(COMMANDS))
    assert not stale, f"sections for commands the CLI no longer has: {stale}"


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_option_appears_in_its_commands_section(command: str) -> None:
    section = SECTIONS.get(command, "")
    missing = [names[-1] for names, _ in COMMANDS[command]
               if not any(_mentions(section, n) for n in names if n.startswith("--"))]
    assert not missing, f"`autombist {command}` options its section never mentions: {missing}"


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_flag_an_option_table_names_exists(command: str) -> None:
    real = {n for names, negated in COMMANDS[command] for n in (*names, *negated)} | {"--help"}
    stale = sorted(_table_flags(SECTIONS.get(command, "")) - real)
    assert not stale, f"`autombist {command}`'s option table names flags it does not have: {stale}"


def test_the_top_level_options_are_documented_in_the_overview() -> None:
    missing = [names[-1] for names, _ in _options(GROUP)
               if not all(_mentions(OVERVIEW, n) for n in names)]
    assert not missing, f"top-level options the reference's overview never mentions: {missing}"


def _shell_source() -> ast.Module:
    return ast.parse(TCL_SHELL.read_text(encoding="utf-8"))


def _shell_commands() -> dict[str, str]:
    """{Tcl command: method name} from TclShell._register_commands' dict."""
    for node in ast.walk(_shell_source()):
        if isinstance(node, ast.FunctionDef) and node.name == "_register_commands":
            for value in ast.walk(node):
                if isinstance(value, ast.Dict):
                    return {k.value: v.attr for k, v in zip(value.keys, value.values)
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Attribute)}
    raise AssertionError("no command table in TclShell._register_commands")


def _shell_flags(method: str) -> set[str]:
    for node in ast.walk(_shell_source()):
        if isinstance(node, ast.FunctionDef) and node.name == method:
            return {call.args[1].value for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id.startswith("_pop_") and len(call.args) >= 2
                    and isinstance(call.args[1], ast.Constant)}
    raise AssertionError(f"no {method} in tcl_shell.py")


def test_the_shell_registers_the_commands_its_section_lists() -> None:
    listing = re.search(r"([A-Z][a-z]+) commands are registered: (.*?) \S same options", SECTIONS["shell"], re.S)
    assert listing, "the shell section no longer says which commands are registered"
    listed = re.findall(r"`([a-z_]+)`", listing.group(2))
    registered = _shell_commands()
    assert sorted(listed) == sorted(registered)
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve"]
    assert listing.group(1).lower() == words[len(registered)], "the section's command count is stale"


@pytest.mark.parametrize("tcl_command", sorted(_shell_commands()))
def test_a_shell_command_has_the_options_of_its_cli_counterpart(tcl_command: str) -> None:
    cli_command = tcl_command.replace("_", "-")
    assert cli_command in COMMANDS, f"shell command {tcl_command!r} has no CLI counterpart"
    cli = {n for names, _ in COMMANDS[cli_command] for n in names if n.startswith("--")}
    shell = {"-" + flag for flag in _shell_flags(_shell_commands()[tcl_command])}
    assert shell <= cli, f"shell {tcl_command} flags with no CLI option: {sorted(shell - cli)}"
    lacking = sorted(cli - shell - set(SHELL_OMITS))
    assert not lacking, f"CLI options shell {tcl_command} does not offer: {lacking}"

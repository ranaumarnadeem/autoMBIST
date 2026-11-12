"""manifest.json's instance parameters are the ones the wrapper actually sets.

manifest.py lists the wrapper's instances by hand, mirroring
wrapper_template.j2. A consumer synthesizes each instance standalone with the
listed parameters, so one the template sets and the manifest leaves out builds
a different module from the one on the chip (#47: the controller lost
MAX_RETRIES and TWO_PASS, the 2D analyzer DEFERRED). This test reads every
golden case's rendered wrapper and checks each instance's `#(...)` overrides
against the manifest, so the two cannot drift apart unnoticed again.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import generate_from_config  # noqa: E402
from autombist.manifest import build_instances  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "golden"
CASES: dict[str, dict] = yaml.safe_load((GOLDEN / "cases.yml").read_text(encoding="utf-8"))
# The manifest describes the clean collar; it refuses a --test (saboteur) build.
CLEAN_CASES = sorted(case for case, spec in CASES.items() if not spec["generate"].get("use_saboteur"))

_HEADER_PARAM = re.compile(r"parameter\s+(?:integer\s+)?(\w+)\s*=\s*([^,\n)]+)")
_INSTANCE = re.compile(r"\b(\w+)\s*#\((.*?)\)\s*(\w+)\s*\(", re.DOTALL)
_OVERRIDE = re.compile(r"\.(\w+)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")


def _wrapper_parameters(text: str) -> dict[str, int]:
    """The wrapper module's own parameters, from its header."""
    header = text[: text.index(") (")]
    return {name: int(value.strip()) for name, value in _HEADER_PARAM.findall(header)}


def _overrides(text: str) -> dict[str, dict[str, int]]:
    """{instance name: {parameter: value}} for every `module #(...) name (` in the wrapper."""
    scope = _wrapper_parameters(text)
    found: dict[str, dict[str, int]] = {}
    for _module, body, instance in _INSTANCE.findall(text):
        found[instance] = {
            name: int(eval(value, {"__builtins__": {}}, scope))  # noqa: S307 -- our own generated RTL
            for name, value in _OVERRIDE.findall(body)
        }
    return found


@pytest.mark.parametrize("case", CLEAN_CASES)
def test_manifest_parameters_match_the_rendered_wrapper(case: str, tmp_path: Path) -> None:
    spec = CASES[case]
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(spec["config"], sort_keys=False), encoding="utf-8")
    wrapper = generate_from_config(config_path, tmp_path / "out", **spec["generate"])
    snapshot = yaml.safe_load((wrapper.parent / "config.yml").read_text(encoding="utf-8"))

    rendered = _overrides(wrapper.read_text(encoding="utf-8"))
    for instance in build_instances(snapshot):
        if instance["category"] == "memory":
            continue  # the memory's own parameters are the macro's, listed from the config
        name = instance["instance_name"]
        assert name in rendered or not instance["parameters"], (
            f"{case}: the manifest gives {instance['hierarchical_path']} parameters "
            f"{instance['parameters']}, but the wrapper sets none"
        )
        assert instance["parameters"] == rendered.get(name, {}), (
            f"{case}: {instance['hierarchical_path']} -- manifest {instance['parameters']}, "
            f"wrapper {rendered.get(name, {})}"
        )

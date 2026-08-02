"""End-to-end proof of Workstream 1.7: a persisted-repair-signature model on
top of Step E's on-chip self-repair. Mirrors test_onchip_selfrepair_e2e.py's
`_generate`/`_run` conventions, but drives the NEW cocotb module
(test_onchip_repair_persistence) which deliberately toggles rst_n mid-test --
see that module's own header for why it can't share test_onchip_selfrepair.py's
runner. Icarus + make gated, matching every other on-chip self-repair e2e test
in this area.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("make") is None,
    reason="needs Icarus Verilog + make on PATH (Linux/WSL only)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import generate_from_config  # noqa: E402
from autombist.runner import run_simulation  # noqa: E402

ADDR_WIDTH = 2
DATA_WIDTH = 4
BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}


def _config(memory_name: str, wrapper_module_name: str, num_spare_rows: int) -> dict:
    return {
        "memory_name": memory_name,
        "wrapper_module_name": wrapper_module_name,
        "addr_width": ADDR_WIDTH,
        "data_width": DATA_WIDTH,
        "we_active_low": True,
        "ports": BASE_PORTS,
        "redundancy": {
            "num_spare_rows": num_spare_rows, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_repair_persistence": True,
        },
    }


def _generate(tmp_path: Path, config: dict, subdir: str, algo: str = "march-c") -> Path:
    config_path = tmp_path / f"{subdir}.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return generate_from_config(config_path, tmp_path / subdir, algo=algo)


def _run(wrapper: Path):
    return run_simulation(
        wrapper.parent,
        extra_make_vars={"COCOTB_TEST_MODULES": "test_onchip_repair_persistence"},
    )


def test_repair_load_before_any_access_reads_immediately_clean(tmp_path: Path) -> None:
    """The core claim: a fuse-loaded repair signature is active BEFORE any
    BIST/functional access has occurred -- not just eventually, after some
    self-repair sequence runs. Uses the same sram_spares_tiny.v (1 baked-in
    defect, 2 spares) fixture as the plain on-chip self-repair e2e tests."""
    wrapper = _generate(
        tmp_path, _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2), "repair_load"
    )
    result = _run(wrapper)
    assert result.returncode == 0, result.stdout


def test_repair_load_generation_requires_onchip_selfrepair_flag(tmp_path: Path) -> None:
    """Config-level guardrail, exercised end-to-end (not just the unit-level
    ConfigError test): onchip_repair_persistence alone, without
    onchip_selfrepair, must fail generation before any simulation is attempted."""
    from autombist.generator import ConfigError

    config = _config("sram_spares_tiny", "sram_spares_tiny_mbist", 2)
    config["redundancy"].pop("onchip_selfrepair")
    config_path = tmp_path / "bad.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="onchip_selfrepair"):
        generate_from_config(config_path, tmp_path / "out")

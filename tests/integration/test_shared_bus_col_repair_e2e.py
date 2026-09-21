"""Real end-to-end proof for docs/shared-hierarchical-mbist-plan.md §9b step
4: shared-bus + on-chip 2D (row+column) self-repair, actually SIMULATED
(Icarus + cocotb) -- see test_shared_bus_generate_e2e.py for the structural
(Verilator lint) proof this builds on, and test_shared_bus_selfrepair_e2e.py
for the row-only precedent this mirrors.

Reuses test_shared_selfrepair.py's cocotb module UNCHANGED: it only ever
drives self_repair_start/self_repair_done/self_repair_fail and reads each
memory's own internal self_repair_fail_arr[i], none of which are row/column
specific -- the same test genuinely exercises both repair shapes.

sram_spares_col_tiny.v exposes SIX defect parameters per instance
(DEFECT_ADDR/_BIT/_SA1 and DEFECT2_ADDR/_BIT/_SA1 -- two independent faults,
so num_spare_rows=1/num_spare_cols=1 forces BIRA to use the column spare for
one of them, not just the row spare). Each bank's own instance gets its OWN
defparam override, exactly like the row-only proof's u_mem_mem_bank0/
u_mem_mem_bank1 mechanism -- no wrapper_template.j2 change needed for this
either.
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
WRAPPER_MODULE_NAME = "shared_col_repair_ctrl"

BASE_CONFIG = {
    "memory_name": "sram_spares_col_tiny",
    "wrapper_module_name": WRAPPER_MODULE_NAME,
    "topology": "shared-bus",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {
        "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
        "we": "web0", "csb": "csb0", "spare_wen": "spare_wen0",
    },
    "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
    "redundancy": {
        "num_spare_rows": 1, "num_spare_cols": 1,
        "onchip_selfrepair": True, "onchip_col_repair": True,
    },
}

# sram_spares_col_tiny.v's own defaults: two stuck-at-1 faults on the SAME
# bit (3) in different rows (1, 2) -- forces the column spare to be used
# (see the fixture's own header for why this specific pair is decisive).
DEFAULT_DEFECTS = {"DEFECT_ADDR": 1, "DEFECT_BIT": 3, "DEFECT_SA1": 1,
                    "DEFECT2_ADDR": 2, "DEFECT2_BIT": 3, "DEFECT2_SA1": 1}
NO_DEFECTS = {"DEFECT_ADDR": -1, "DEFECT2_ADDR": -1}
# A distinct fault pair for the "both banks, different bits" scenario --
# forces the column spare onto a DIFFERENT bit than the default, so a bug
# that hardcodes/reuses one memory's faulty_bit for the other would surface.
ALT_DEFECTS = {"DEFECT_ADDR": 0, "DEFECT_BIT": 1, "DEFECT_SA1": 1,
               "DEFECT2_ADDR": 1, "DEFECT2_BIT": 1, "DEFECT2_SA1": 1}


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(BASE_CONFIG, sort_keys=False), encoding="utf-8")
    return path


def _write_defparam_overrides(module_outdir: Path, overrides: dict[str, dict[str, int]]) -> Path:
    """overrides maps instance name (e.g. "u_mem_mem_bank0") to a dict of
    {parameter_name: value} defparam overrides for that instance."""
    lines = [f"module {WRAPPER_MODULE_NAME}_defect_overrides;"]
    for instance, params in overrides.items():
        path = f"{WRAPPER_MODULE_NAME}.{instance}"
        for name, value in params.items():
            lines.append(f"    defparam {path}.{name} = {value};")
    lines.append("endmodule")
    override_path = module_outdir / "defect_overrides.v"
    override_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return override_path


def _generate(tmp_path: Path) -> Path:
    config_path = _write_config(tmp_path)
    return generate_from_config(config_path, tmp_path / "out", algo="march-c")


def _run(module_outdir: Path, override_path: Path):
    return run_simulation(
        module_outdir,
        extra_make_vars={
            "COCOTB_TEST_MODULES": "test_shared_selfrepair",
            "EXTRA_SOURCES": str(override_path),
            "NUM_MEMORIES": "2",
        },
    )


def test_col_repair_defect_in_bank0_only_is_repaired_without_disturbing_bank1(tmp_path: Path) -> None:
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent, {"u_mem_mem_bank1": NO_DEFECTS}
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout


def test_col_repair_defect_in_bank1_only_is_repaired_without_disturbing_bank0(tmp_path: Path) -> None:
    # Bank order swapped from the bank0-only case above -- catches an
    # indexing/order-dependent bug the symmetric case alone could hide.
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent, {"u_mem_mem_bank0": NO_DEFECTS}
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout


def test_distinct_faulty_bits_in_both_banks_are_independently_repaired(tmp_path: Path) -> None:
    # Each bank forces its column spare onto a DIFFERENT bit -- proves
    # col_repair_en_arr[i]/faulty_bit_arr[i]/spare_wen_arr[i] are genuinely
    # independent per memory, not aliased or shared, which the row-only
    # proof set never exercised (it has no bit-level state at all).
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent,
        {"u_mem_mem_bank0": ALT_DEFECTS, "u_mem_mem_bank1": DEFAULT_DEFECTS},
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout

"""Real end-to-end proof for docs/shared-hierarchical-mbist-plan.md §9b:
shared-bus + on-chip row self-repair, actually SIMULATED (Icarus + cocotb),
not just elaborated -- see test_shared_bus_generate_e2e.py for the
structural (Verilator lint) proof this builds on.

The two memory banks share ONE memory_name module (sram_spares_tiny), whose
DEFECT_ADDR/_BIT/_SA1 are compile-time parameters baked identically into
every instance by default -- so proving "defects in only a subset of
memories get independently repaired, with zero cross-memory interference"
needs a way to give two instances of the SAME module different defects. A
defparam targeting each instance's own hierarchical path (u_mem_mem_bank0 /
u_mem_mem_bank1, both real instance names inside the generated wrapper) does
exactly that, compiled in via the Makefile's EXTRA_SOURCES hook -- no
wrapper_template.j2 change needed, and no saboteur (shared-bus rejects
use_saboteur: true; see test_shared_bus_rejects_use_saboteur).
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
WRAPPER_MODULE_NAME = "shared_selfrepair_ctrl"

BASE_CONFIG = {
    "memory_name": "sram_spares_tiny",
    "wrapper_module_name": WRAPPER_MODULE_NAME,
    "topology": "shared-bus",
    "addr_width": ADDR_WIDTH,
    "data_width": DATA_WIDTH,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
    "memories": [{"name": "mem_bank0"}, {"name": "mem_bank1"}],
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 0, "onchip_selfrepair": True},
}

# sram_spares_tiny.v's own defaults: a stuck-at-1 defect at (addr=3, bit=3).
DEFAULT_DEFECT = (3, 3)
# A distinct second defect location, used for the "both" scenario so the two
# banks are proven independent, not just symmetric copies of the same case.
ALT_DEFECT = (1, 1)


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(BASE_CONFIG, sort_keys=False), encoding="utf-8")
    return path


def _write_defparam_overrides(module_outdir: Path, overrides: dict[str, tuple[int, int, int] | None]) -> Path:
    """overrides maps instance name (e.g. "u_mem_mem_bank0") to either a
    (DEFECT_ADDR, DEFECT_BIT, DEFECT_SA1) triple to set, or None to disable
    that instance's defect (DEFECT_ADDR=-1, which sram_spares_tiny.v's own
    `if (DEFECT_ADDR >= 0 && ...)` guard treats as "no defect")."""
    lines = [f"module {WRAPPER_MODULE_NAME}_defect_overrides;"]
    for instance, defect in overrides.items():
        path = f"{WRAPPER_MODULE_NAME}.{instance}"
        if defect is None:
            lines.append(f"    defparam {path}.DEFECT_ADDR = -1;")
        else:
            addr, bit, sa1 = defect
            lines.append(f"    defparam {path}.DEFECT_ADDR = {addr};")
            lines.append(f"    defparam {path}.DEFECT_BIT = {bit};")
            lines.append(f"    defparam {path}.DEFECT_SA1 = {sa1};")
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


def test_defect_in_bank0_only_is_repaired_without_disturbing_bank1(tmp_path: Path) -> None:
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent, {"u_mem_mem_bank1": None}
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout


def test_defect_in_bank1_only_is_repaired_without_disturbing_bank0(tmp_path: Path) -> None:
    # Bank order swapped from the bank0-only case above -- catches an
    # indexing/order-dependent bug (e.g. a hardcoded mem_sel_q==0 somewhere)
    # that a single-direction test could hide.
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent, {"u_mem_mem_bank0": None}
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout


def test_distinct_defects_in_both_banks_are_independently_repaired(tmp_path: Path) -> None:
    # Each bank gets its OWN distinct defect location, both well within the
    # 2-spare-row budget -- proves the per-memory generate-loop's separate
    # analyzer/ctrl/remap instances each repair their own memory in the SAME
    # self-repair pass, not just whichever one happens to run first.
    wrapper_path = _generate(tmp_path)
    override_path = _write_defparam_overrides(
        wrapper_path.parent,
        {
            "u_mem_mem_bank0": (*ALT_DEFECT, 1),
            "u_mem_mem_bank1": (*DEFAULT_DEFECT, 1),
        },
    )
    result = _run(wrapper_path.parent, override_path)
    assert result.returncode == 0, result.stdout

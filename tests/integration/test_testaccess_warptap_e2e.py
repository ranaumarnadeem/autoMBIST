"""autombist.testaccess against a real generated mem_subsystem_mbist, proving the
INTEGRATION module itself, not warptap in isolation -- warptap's own test suite
(https://github.com/ranaumarnadeem/warptap, MIT) already proves the lower-level
insert_test_access() path end-to-end against this exact same design; this test's only
job is to confirm autombist.testaccess.wrap_test_access()'s port classification and
warptap call produce the identical, real, working result when driven from autoMBIST's
own side.

The scenario -- write self_repair_start=1, wait, read self_repair_busy expecting 1,
write self_repair_start=0 -- is warptap's own already-proven one, reused verbatim
rather than invented fresh, so a difference in outcome is attributable to this
module, not to picking a different (possibly easier) scenario.

Skips (not fails) when warptap is not installed: it is an optional capability,
`pip install warptap`, mirroring this project's iverilog/verilator skip-marker
convention for other optional external dependencies. Also needs iverilog (the real
simulator both this test and warptap's own cross-sim tests run the inserted RTL
through) and a real yosys (warptap's own ingest()/insert_sib_network() shell out to
it; no bundled fallback exists in warptap either).
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.testaccess import TestAccessUnavailable, wrap_test_access

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("warptap") is None
    or shutil.which("iverilog") is None
    or shutil.which("yosys") is None,
    reason="needs `pip install warptap` plus iverilog and yosys on PATH "
    "(Linux/WSL only) -- warptap shells out to both, no bundled fallback",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_PORTS = {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"}
_WRAPPERS = [
    ("sram_wrap_a", "selfrepair_a", 8, 32, "gen_a"),
    ("sram_wrap_b", "selfrepair_b", 9, 32, "gen_b"),
    ("sram_wrap_c", "selfrepair_c", 10, 8, "gen_c"),
]
# (tms, tdi, trst_n, rst_n, csb, we, mem_sel, addr, wdata) -- functional bus held idle
# throughout (csb=1); mirrors warptap's own test_mem_subsystem_mbist_pdl_verify.py
# _RESET_LEAD_IN exactly, since this reuses its same fixture/testbench.
_RESET_LEAD_IN = [
    (0, 0, 0, 0, 1, 0, 0, 0, 0),
    (0, 0, 0, 0, 1, 0, 0, 0, 0),
    (0, 0, 1, 1, 1, 0, 0, 0, 0),
]


def _generate_wrappers(gen_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for memory_name, wrapper_name, addr_width, data_width, subdir in _WRAPPERS:
        config = {
            "memory_name": memory_name, "wrapper_module_name": wrapper_name,
            "addr_width": addr_width, "data_width": data_width, "we_active_low": True,
            "ports": _BASE_PORTS,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0, "onchip_selfrepair": True},
            "read_latency": 0,
        }
        config_path = gen_dir / f"{subdir}.yml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        paths[subdir] = generate_from_config(config_path, gen_dir / subdir, algo="march-c")
    return paths


def _source_list(wrapper_paths: dict[str, Path]) -> list[Path]:
    multimem_dir = REPO_ROOT / "flow" / "multimem"
    mbist_dir = multimem_dir / "mbist"
    # Only gen_a's shared march_c/onchip files: they are parameterized Verilog
    # modules (one definition, instantiated per-memory with different width
    # parameters) -- confirmed by warptap's own spike that Yosys's `hierarchy`
    # auto-derives a distinct $paramod per instantiation's parameter set from
    # this single copy, so the other two generated dirs' copies are redundant.
    shared = wrapper_paths["gen_a"].parent
    return [
        shared / "march_c" / "march_c_algo.sv",
        shared / "march_c" / "march_c_fsm.sv",
        shared / "march_c" / "march_c_top.sv",
        shared / "onchip_row_repair_analyzer.sv",
        shared / "onchip_selfrepair_ctrl.sv",
        shared / "repair_remap_row.sv",
        wrapper_paths["gen_a"], wrapper_paths["gen_b"], wrapper_paths["gen_c"],
        mbist_dir / "sram_wrap_a.sv", mbist_dir / "sram_wrap_b.sv", mbist_dir / "sram_wrap_c.sv",
        multimem_dir / "sky130_sram_32b256w.v",
        multimem_dir / "sky130_sram_32b512w.v",
        multimem_dir / "sky130_sram_8b1024w.v",
        mbist_dir / "mem_subsystem_mbist.sv",
    ]


def test_wrap_test_access_classifies_the_real_eight_ports(tmp_path: Path) -> None:
    """The classifier alone, no warptap/simulation needed: confirms the exact 8-port,
    all-1-bit shape warptap's own test suite already independently proves works end to
    end -- pinned here so a change to wrapper_template.j2's port list is caught by a
    fast, non-simulator test rather than only by the slow one below."""
    from autombist.testaccess import classify_test_access_ports

    ports = classify_test_access_ports(onchip_selfrepair=True)
    assert [p.name for p in ports] == [
        "test_mode", "bist_start", "bist_done", "bist_fail",
        "self_repair_start", "self_repair_done", "self_repair_fail", "self_repair_busy",
    ]
    assert [p.role for p in ports] == [
        "control", "control", "status", "status",
        "control", "status", "status", "status",
    ]


def test_wrap_test_access_raises_clearly_without_warptap_stub(monkeypatch) -> None:
    """Negative control for the import guard itself: force the not-installed path and
    confirm it raises TestAccessUnavailable rather than some other error, so a future
    refactor of the try/except in testaccess.py can't silently turn this into an
    ImportError or an AttributeError a caller isn't expecting."""
    import autombist.testaccess as ta

    monkeypatch.setattr(ta, "_warptap_insert_test_access", None)
    with pytest.raises(TestAccessUnavailable):
        ta.wrap_test_access(["x.sv"], "top")


def test_self_repair_start_write_and_busy_read_through_real_jtag(tmp_path: Path) -> None:
    """The decisive test: real generated RTL, real warptap insertion via THIS project's
    own wrap_test_access(), real Icarus simulation of the inserted netlist, real PDL
    read-check against the real onchip_selfrepair_ctrl.sv FSM's actual behavior."""
    from warptap.pdl_interpreter import PDLInterpreter
    from warptap.pdl_verify import check_reads, correlate_observed
    from warptap.sim_io import run_verilog_testbench
    from warptap.tap_fsm import TapState
    from warptap.tap_ir import GotoState, ShiftIR, bits_to_int
    from warptap.tap_ir_play import to_cycles

    warptap_fixtures = Path.home() / "warptap" / "tests" / "fixtures"
    testbench = warptap_fixtures / "tb_mem_subsystem_mbist.v"
    if not testbench.is_file():
        pytest.skip(
            f"warptap's own test fixture not found at {testbench} -- this test reuses "
            "it rather than duplicating it; clone github.com/ranaumarnadeem/warptap "
            "to ~/warptap to enable this specific test"
        )

    wrapper_paths = _generate_wrappers(tmp_path)
    sources = _source_list(wrapper_paths)
    for src in sources:
        assert src.is_file(), f"expected real/generated openMBIST source missing: {src}"

    inserted_verilog, graph, root = wrap_test_access(
        sources, "mem_subsystem_mbist", onchip_selfrepair=True,
    )
    assert len(graph.chain) == 8

    pdl = PDLInterpreter(graph, root)
    pdl.iTarget("self_repair_start")
    pdl.iWrite(1)
    pdl.iApply()
    pdl.iRunLoop(10)  # >= 1 cycle for S_IDLE -> S_ANALYZE_KICK, generous margin
    pdl.iTarget("self_repair_busy")
    pdl.iRead(1)
    pdl.iApply()
    pdl.iTarget("self_repair_start")
    pdl.iWrite(0)
    pdl.iApply()

    ir_ops = [
        GotoState(TapState.SHIFT_IR),
        ShiftIR(4, tdi=bits_to_int([0, 0, 0, 0])),  # OPCODE_EXTEST = 0, IR_WIDTH = 4
        GotoState(TapState.RUN_TEST_IDLE),
    ] + pdl.program

    cycles = to_cycles(ir_ops)
    rows = _RESET_LEAD_IN + [(tms, tdi, 1, 1, 1, 0, 0, 0, 0) for tms, tdi in cycles]
    stimulus = "\n".join(" ".join(str(v) for v in row) for row in rows) + "\n"

    with tempfile.TemporaryDirectory(prefix="test-testaccess-warptap-") as tmpdir:
        v_path = Path(tmpdir) / "mem_subsystem_mbist_sib_inserted.v"
        v_path.write_text(inserted_verilog, encoding="utf-8")
        stdout = run_verilog_testbench(
            [v_path, testbench], extra_inputs={"stimulus.txt": stimulus},
        )

    tdo_by_cycle = [
        int(line.split(",")[6]) for line in stdout.splitlines() if line.startswith("TRACE,")
    ]
    tdo_by_cycle = tdo_by_cycle[len(_RESET_LEAD_IN):]

    observed = correlate_observed(ir_ops, tdo_by_cycle)
    results = check_reads(ir_ops, observed)

    assert len(results) == 1  # exactly the one iRead(self_repair_busy) queued above
    assert results[0].passed, (
        f"self_repair_busy expected 1 after writing self_repair_start=1 and settling, "
        f"observed through the JTAG scan path inserted by autombist.testaccess -- "
        f"got {results[0]}"
    )

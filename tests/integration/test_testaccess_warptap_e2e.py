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
import re
import shutil
import sys
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
# Every control (WRITE) and status (READ) port classify_test_access_ports can produce,
# matched against classify_test_access_ports()'s own role for each -- see
# test_control_ports_are_jtag_exclusive_status_ports_are_not below.
_CONTROL_PORTS = ("test_mode", "bist_start", "self_repair_start", "repair_load")
_STATUS_PORTS = (
    "bist_done", "bist_fail",
    "self_repair_done", "self_repair_fail", "self_repair_busy",
    "repair_load_done",
    "diag_overflow",
)
# Present once onchip_repair_persistence/onchip_diagnosis are on AND their geometry is
# given (num_spare_rows/num_diagnosis_entries/addr_width) -- checked alongside
# _CONTROL_PORTS/_STATUS_PORTS in test_control_ports_are_jtag_exclusive_status_ports_are_not.
# Widths match that test's own fixture (num_spare_rows=2, num_diagnosis_entries=4, addr_width=6).
_WIDE_PORTS = {
    "fuse_row_repair_en": ("control", 2),
    "fuse_faulty_row_addr": ("control", 12),
    "diag_valid": ("status", 4),
    "diag_addr": ("status", 24),
}
# Always present, on every config, never candidates for classify_test_access_ports at all
# (clk/rst_n are infrastructure; func_* is the functional read/write path, not a
# control/status instrument) -- see test_control_ports_are_jtag_exclusive_status_ports_are_not.
_INFRASTRUCTURE_PORTS = ("clk", "rst_n", "func_csb", "func_addr", "func_din", "func_we", "func_dout")


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


def test_control_ports_are_jtag_exclusive_status_ports_are_not(tmp_path: Path) -> None:
    """Pins down a real, non-obvious finding from tracing the actual generated netlist by
    hand: after wrapping, a CONTROL port's original top-level pin goes completely dead --
    zero fan-out anywhere in the design, only the inserted JTAG network can still set it --
    while a STATUS port's original pin keeps working exactly as before (the SIB only taps
    it, non-destructively). See cli-reference.md's wrap-test-access section for the
    user-facing statement of this; this test is what keeps it true.

    Two structural facts, both about warptap's own primitive library rather than about any
    Yosys-assigned internal wire name (which could shift with formatting/version and would
    make this test fragile for no real gain):

    1. `instrument_write` -- the primitive warptap uses for every control/WRITE port -- has,
       in its own module definition, no port at all that could carry an external signal in.
       `pin_out` is a pure shadow register: nothing but an Update-DR with that segment
       selected ever changes it. This is *why* control ports go JTAG-only; if a future
       warptap version adds a passthrough port to this primitive, that changes the finding
       above, and this assertion is what should catch it.
    2. Each port lands on the primitive its role predicts -- control ports get
       `instrument_write`, status ports get `bc1_shift_only` (a real observe cell, reading
       the live signal through its `pi` port) -- never the other way around. That pairing is
       autombist.testaccess's own responsibility (classify_test_access_ports /
       build_instrument_specs choosing WRITE vs READ), checked here against warptap's real
       output rather than mocked.

    A third fact, exercising this module's wide-port support (fuse_row_repair_en,
    fuse_faulty_row_addr -- present because this config turns on
    onchip_repair_persistence and passes num_spare_rows/addr_width; diag_valid,
    diag_addr -- onchip_diagnosis plus num_diagnosis_entries/addr_width): these get the
    SAME control/status treatment as any 1-bit port -- instrument_write/bc1_shift_only,
    a real warptap_sib_* instance, direction matching their role -- just at their real
    width, confirmed both via the rendered Verilog and directly against
    graph.chain[i].instrument's own width/direction/signal_bits (the exact width>1 path
    that used to hit warptap's vendored icl_parser AssertionError bug this whole feature
    was blocked on). The chain has 15 ports total: 4 base + 4 self-repair + 4 persistence
    (repair_load, fuse_row_repair_en, fuse_faulty_row_addr, repair_load_done) + 3
    diagnosis (diag_valid, diag_addr, diag_overflow).

    A fourth: fail_valid/fail_addr are not merely unwrapped, they are not wrapper ports at
    all under any config, checked against the top module's own port list directly (not
    file-wide substring presence -- march_c_fsm/march_c_top are separate submodules in this
    same flattened file and genuinely do have their own same-named ports).

    A fifth, closing out every port wrapper_template.j2 can ever declare: clk/rst_n and the
    functional data-path ports (func_csb/func_addr/func_din/func_we/func_dout) get no SIB
    treatment either. These are the highest-stakes exclusion of all -- wrapping the
    functional read/write path by mistake would break the chip, not just leave a DFT gap --
    and this also confirms the inserted TAP genuinely runs in its own clock domain (tap_core
    is clocked by tck/trst_n alone, never clk/rst_n, traced directly).
    """
    from warptap.icl_model import SignalBinding

    config = {
        "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 6, "data_width": 8, "we_active_low": True,
        "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
        "redundancy": {
            "num_spare_rows": 2, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_repair_persistence": True,
            "onchip_diagnosis": True, "num_diagnosis_entries": 4,
        },
    }
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "gen", algo="march-c")
    shared = wrapper_path.parent
    sources = [
        wrapper_path,
        shared / "march_c" / "march_c_algo.sv",
        shared / "march_c" / "march_c_fsm.sv",
        shared / "march_c" / "march_c_top.sv",
        shared / "onchip_row_repair_analyzer.sv",
        shared / "onchip_selfrepair_ctrl.sv",
        shared / "onchip_diagnosis_log.sv",
        shared / "repair_remap_row.sv",
        shared / "sram_model.sv",
    ]
    for src in sources:
        assert src.is_file(), f"expected generated openMBIST source missing: {src}"

    inserted_verilog, graph, _root = wrap_test_access(
        sources, "sram_1rw_mbist",
        onchip_selfrepair=True, onchip_repair_persistence=True, onchip_diagnosis=True,
        num_spare_rows=2, num_diagnosis_entries=4, addr_width=6,
    )
    assert len(graph.chain) == 15, (
        "expected exactly 15 wrapped ports in the chain (4 base + 4 self-repair + "
        "4 persistence incl. the 2 new wide ones + 3 diagnosis incl. the 2 new wide "
        "ones) -- a different count would mean a wide port's geometry gate didn't fire "
        "the way this fixture's config expects"
    )

    # fail_valid/fail_addr are never wrapper ports at all (confirmed directly against
    # wrapper_template.j2 -- they exist, when onchip_selfrepair is on, only as a single
    # unregistered wire from march_c_top's output straight into
    # onchip_row_repair_analyzer's input, never reaching the wrapper boundary). Checked
    # against the TOP module's own port list specifically, not file-wide substring
    # presence: march_c_fsm/march_c_top are separate submodules in this same flattened
    # file and genuinely do have their own ports by these names, so a naive whole-file
    # check would fail for the wrong reason.
    top_header = re.search(r"module sram_1rw_mbist\(([^)]*)\);", inserted_verilog)
    assert top_header, "top module not found in the inserted Verilog"
    top_ports = [p.strip() for p in top_header.group(1).split(",")]
    assert "fail_valid" not in top_ports and "fail_addr" not in top_ports, (
        "fail_valid/fail_addr appeared as real top-level ports -- if wrapper_template.j2 "
        "now exposes diagnosis readback, testaccess.py needs a decision about wrapping "
        "them, not silence"
    )

    # The highest-stakes exclusion: if func_csb/func_addr/func_din/func_we/func_dout ever
    # got swept into the SIB chain, that would not just be a DFT coverage gap, it would
    # break the chip's actual functional read/write path. clk/rst_n are the same category
    # of never-a-candidate infrastructure signal.
    for port in _INFRASTRUCTURE_PORTS:
        assert port in top_ports, f"{port} unexpectedly missing from the wrapper's own ports"
        assert f"warptap_sib_{port}" not in inserted_verilog, (
            f"{port} should never be a wrapping candidate at all -- found a "
            f"warptap_sib_{port}* instance anyway"
        )

    write_header = re.search(r"module instrument_write\(([^)]*)\);", inserted_verilog)
    assert write_header, "instrument_write primitive not found in the inserted Verilog"
    assert [p.strip() for p in write_header.group(1).split(",")] == [
        "si", "so", "pin_out", "select", "capture_dr", "shift_dr", "update_dr", "tck", "trst_n",
    ], "instrument_write gained (or lost) a port -- re-check whether control ports are still JTAG-exclusive"

    for port in _CONTROL_PORTS:
        assert f"instrument_write warptap_sib_{port}_inst_0 (" in inserted_verilog, (
            f"{port} (a control port) should be wrapped with instrument_write"
        )
        assert f"bc1_shift_only warptap_sib_{port}_inst_0 (" not in inserted_verilog, (
            f"{port} (a control port) was wrapped as a status/observe port instead"
        )

    for port in _STATUS_PORTS:
        assert f"bc1_shift_only warptap_sib_{port}_inst_0 (" in inserted_verilog, (
            f"{port} (a status port) should be wrapped with bc1_shift_only"
        )
        assert f"instrument_write warptap_sib_{port}_inst_0 (" not in inserted_verilog, (
            f"{port} (a status port) was wrapped as a control/write port instead"
        )
        assert f".pi({port})" in inserted_verilog, (
            f"{port}'s observe cell should tap the real signal directly by name"
        )

    chain_by_name = {n.instrument.name: n.instrument for n in graph.chain}
    for port, (role, width) in _WIDE_PORTS.items():
        assert f"warptap_sib_{port}" in inserted_verilog, (
            f"{port} (a wide port) should now get real SIB treatment -- found no "
            f"warptap_sib_{port}* instance"
        )
        instrument = chain_by_name.get(port)
        assert instrument is not None, f"{port} missing from the SIB chain entirely"
        assert instrument.width == width, f"{port} expected width {width}, got {instrument.width}"
        expected_direction = "WRITE" if role == "control" else "READ"
        assert instrument.direction.name == expected_direction, (
            f"{port} expected direction {expected_direction}, got {instrument.direction.name}"
        )
        # The exact bit-order convention wrap_test_access's build_instrument_specs relies
        # on: signal_bits[k] binds bit k of the real port -- checked directly against the
        # real InstrumentNode warptap constructed, not just inferred from width matching.
        assert instrument.signal_bits == tuple(SignalBinding(port, i) for i in range(width)), (
            f"{port}'s signal_bits do not follow the expected bit-order convention"
        )


_REPAIR_PORTS_CONFIG = {
    "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist",
    "addr_width": 4, "data_width": 8, "we_active_low": True,
    "ports": {
        "clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0",
        "we": "we0", "csb": "csb0", "spare_wen": "spare_wen0",
    },
    "redundancy": {"num_spare_rows": 2, "num_spare_cols": 1},
    # bit_index_width = ceil(log2(data_width=8)) = 3, so faulty_bit is 1 spare col * 3.
    "repair_ports": [
        {"name": "row_repair_en", "width": 2, "dir": "input"},
        {"name": "faulty_row_addr", "width": 8, "dir": "input"},
        {"name": "col_repair_en", "width": 1, "dir": "input"},
        {"name": "faulty_bit", "width": 3, "dir": "input"},
    ],
}


def _generate_repair_ports_sources(tmp_path: Path) -> list[Path]:
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(_REPAIR_PORTS_CONFIG, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "gen", algo="march-c")
    shared = wrapper_path.parent
    sources = [
        wrapper_path,
        shared / "march_c" / "march_c_algo.sv",
        shared / "march_c" / "march_c_fsm.sv",
        shared / "march_c" / "march_c_top.sv",
        shared / "repair_remap_col.sv",
        shared / "repair_remap_row.sv",
        shared / "sram_model.sv",
        shared / "sram_model_spares.sv",
    ]
    for src in sources:
        assert src.is_file(), f"expected generated openMBIST source missing: {src}"
    return sources


def test_tester_driven_repair_ports_are_not_wrapped_when_omitted(tmp_path: Path) -> None:
    """The opt-out-by-omission case: this config's generated RTL has real
    row_repair_en/faulty_row_addr/col_repair_en/faulty_bit boundary ports (via
    `repair_ports:`), but wrap_test_access is called WITHOUT `repair_ports=` -- the same
    call shape every pre-wide-port-support caller used. Confirms this stays exactly the
    backward-compatible no-op: each port keeps its pre-insertion passthrough connection
    untouched, none gets any warptap_sib_* instance, and the chain is exactly the base 4
    (test_mode/bist_start/bist_done/bist_fail) -- with no onchip_selfrepair here, that is
    everything wrap_test_access produces when repair_ports is omitted.

    Needs its own fixture rather than reusing
    test_control_ports_are_jtag_exclusive_status_ports_are_not's: generator.py rejects
    onchip_selfrepair together with column repair, so a design exercising
    col_repair_en/faulty_bit cannot also carry self_repair_*/fuse_* ports.
    """
    sources = _generate_repair_ports_sources(tmp_path)

    inserted_verilog, graph, _root = wrap_test_access(sources, "sram_1rw_mbist")
    assert len(graph.chain) == 4, (
        "expected only the base 4 ports -- a longer chain would mean a tester-driven "
        "repair port got swept in despite repair_ports not being passed"
    )

    for port in ("row_repair_en", "faulty_row_addr", "col_repair_en", "faulty_bit"):
        assert f".{port}({port})" in inserted_verilog, (
            f"{port}'s direct passthrough connection should survive insertion untouched -- "
            "repair_ports was not passed, so nothing here should be wrapped"
        )
        assert f"warptap_sib_{port}" not in inserted_verilog, (
            f"{port} should have no SIB treatment at all when repair_ports is omitted -- "
            f"found a warptap_sib_{port}* instance anyway"
        )


def test_tester_driven_repair_ports_are_wrapped_when_given(tmp_path: Path) -> None:
    """The other half: the SAME generated RTL as the omitted case above, but
    wrap_test_access is now given `repair_ports=` matching the config's own list --
    every one of the four tester-driven repair ports should get real, correctly-widthed
    SIB treatment (all four are dir='input' in this fixture -- control/WRITE)."""
    sources = _generate_repair_ports_sources(tmp_path)

    inserted_verilog, graph, _root = wrap_test_access(
        sources, "sram_1rw_mbist", repair_ports=_REPAIR_PORTS_CONFIG["repair_ports"],
    )
    assert len(graph.chain) == 8, (
        "expected the base 4 plus all 4 repair_ports entries"
    )

    chain_by_name = {n.instrument.name: n.instrument for n in graph.chain}
    for rp in _REPAIR_PORTS_CONFIG["repair_ports"]:
        port, width = rp["name"], rp["width"]
        assert f"warptap_sib_{port}" in inserted_verilog, (
            f"{port} should now get real SIB treatment -- found no warptap_sib_{port}* instance"
        )
        instrument = chain_by_name.get(port)
        assert instrument is not None, f"{port} missing from the SIB chain entirely"
        assert instrument.width == width, f"{port} expected width {width}, got {instrument.width}"
        assert instrument.direction.name == "WRITE", (
            f"{port} has dir='input' (tester writes it) -- expected WRITE direction, "
            f"got {instrument.direction.name}"
        )


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


def test_icl_round_trips_through_the_vendored_parser(tmp_path: Path) -> None:
    """Closes a real gap: nothing in this project's own test suite had ever exercised ICL at
    all before this -- `--emit-icl` works and had been eyeballed once (its TCKPort/TMSPort/
    ScanInPort/ScanOutPort/TRSTPort declarations checked against the Verilog port list, see
    cli-reference.md), but the "real ICL round-trip through the vendored icl_parser" claim in
    this module's own docstring was citing warptap's OWN test suite against warptap's OWN
    fixture -- never re-verified against anything this project actually generates.

    Emits ICL for the same 10-port design test_control_ports_are_jtag_exclusive_status_ports_are_not
    uses (a real mix of WRITE and READ instruments, at the width=1 this module always
    produces), parses it back through the vendored Honza255/icl_parser exactly as warptap's
    own tests do, and confirms chain order, instrument names, widths, and READ/WRITE
    direction all survive -- the exact guarantee icl_import.py's own module docstring
    documents. signal_bits/capture_value are deliberately NOT checked: icl_import.py
    documents, as a permanent limitation of the ICL format itself rather than a bug, that
    ANTLR discards the comments those values are recorded in, so every reimported instrument
    always comes back with signal_bits=()/capture_value=0 regardless of the original network
    -- asserting equality there would be asserting a guarantee that does not exist.

    Also passes num_spare_rows/addr_width, adding a real width=6 fuse_faulty_row_addr
    WRITE instrument -- the single most relevant regression test for the bug that
    blocked wide-port support in the first place: warptap v0.0.1's vendored icl_parser
    threw a bare AssertionError constructing an IclRegisterModel for ANY width>1
    instrument, and that construction happens inside import_icl below.
    """
    icl_parser_dir = Path.home() / "warptap" / "third_party" / "icl_parser"
    if not icl_parser_dir.is_dir() or not any(icl_parser_dir.iterdir()):
        pytest.skip(
            f"warptap's vendored icl_parser submodule not checked out at {icl_parser_dir} -- "
            "run `git submodule update --init third_party/icl_parser` inside ~/warptap"
        )
    src_dir = str(icl_parser_dir)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    try:
        from src.ijtag import Ijtag
    except ImportError as exc:
        pytest.skip(
            f"icl_parser not importable from {src_dir}: {exc} -- needs "
            "antlr4-python3-runtime==4.7.2, z3-solver, and networkx installed"
        )

    from warptap.icl_emit import to_icl
    from warptap.icl_import import import_icl

    config = {
        "memory_name": "sram_1rw", "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 6, "data_width": 8, "we_active_low": True,
        "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "we0", "csb": "csb0"},
        "redundancy": {
            "num_spare_rows": 1, "num_spare_cols": 0,
            "onchip_selfrepair": True, "onchip_repair_persistence": True,
        },
    }
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    wrapper_path = generate_from_config(config_path, tmp_path / "gen", algo="march-c")
    shared = wrapper_path.parent
    sources = [
        wrapper_path,
        shared / "march_c" / "march_c_algo.sv",
        shared / "march_c" / "march_c_fsm.sv",
        shared / "march_c" / "march_c_top.sv",
        shared / "onchip_row_repair_analyzer.sv",
        shared / "onchip_selfrepair_ctrl.sv",
        shared / "repair_remap_row.sv",
        shared / "sram_model.sv",
    ]
    for src in sources:
        assert src.is_file(), f"expected generated openMBIST source missing: {src}"

    _inserted_verilog, graph, root = wrap_test_access(
        sources, "sram_1rw_mbist", onchip_selfrepair=True, onchip_repair_persistence=True,
        num_spare_rows=1, addr_width=6,
    )
    assert len(graph.chain) == 12
    assert {n.instrument.direction.name for n in graph.chain} == {"WRITE", "READ"}, (
        "expected a real mix of both directions in this fixture -- a round-trip test with "
        "only one direction wouldn't actually exercise import_icl's direction detection"
    )
    widths_by_name = {n.instrument.name: n.instrument.width for n in graph.chain}
    assert widths_by_name["fuse_faulty_row_addr"] == 6, (
        "expected a real width>1 instrument in this fixture -- this is exactly the "
        "scenario warptap v0.0.1's vendored icl_parser AssertionError blocked"
    )

    icl_path = tmp_path / "sram_1rw_mbist_test_access.icl"
    icl_path.write_text(to_icl(graph, root, include_access_link=False), encoding="utf-8")

    reimported_graph, _reimported_root = import_icl(
        [icl_path], "sram_1rw_mbist", icl_parser_module=Ijtag,
    )

    assert [n.sib_name for n in reimported_graph.chain] == [n.sib_name for n in graph.chain], (
        "SIB chain order did not survive the ICL round-trip"
    )
    assert [n.instrument.name for n in reimported_graph.chain] == [n.instrument.name for n in graph.chain], (
        "instrument names did not survive the ICL round-trip"
    )
    assert [n.instrument.width for n in reimported_graph.chain] == [n.instrument.width for n in graph.chain], (
        "instrument widths did not survive the ICL round-trip"
    )
    assert (
        [n.instrument.direction for n in reimported_graph.chain]
        == [n.instrument.direction for n in graph.chain]
    ), "READ/WRITE direction did not survive the ICL round-trip"

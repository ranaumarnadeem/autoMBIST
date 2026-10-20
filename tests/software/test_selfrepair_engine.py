"""SelfRepairEngine without a simulator: the real generator writes the build and
the injectable model, a stand-in "chip" answers each trial, and the engine's
bookkeeping (masks, spare-fault split, signature decode, cause attribution) is
checked directly."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import autombist.yield_sweep as ys
from autombist.yieldsim.sweep import build_report, run_sweep

CONFIG = {
    "memory_name": "sram_sweep", "wrapper_module_name": "sram_sweep_mbist", "addr_width": 3, "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
OK_JUNIT = {"exists": True, "summary": {"failures": 0, "errors": 0}, "tests": []}


def _cells(entries):
    return [{"addr": a, "bit": b} for a, b in sorted(entries)]


class FakeChip:
    """Answers a trial from the engine's own masks. ``behave(base)`` returns the
    on-chip outcome dict and the post-repair residual cells."""

    def __init__(self, behave, observe_drop: int = 0, finish: bool = True) -> None:
        self.behave, self.observe_drop, self.finish = behave, observe_drop, finish
        self.runs = []

    def __call__(self, module, *, extra_make_vars=None, **kwargs):
        self.runs.append(extra_make_vars)
        engine = self.engine
        sa0 = [int(x, 16) for x in engine.mask_paths[0].read_text().split()]
        sa1 = [int(x, 16) for x in engine.mask_paths[1].read_text().split()]
        width = engine.geometry.mem_data_width
        stuck = {(r, c) for r in range(len(sa0)) for c in range(width) if not (sa0[r] >> c) & 1 or (sa1[r] >> c) & 1}
        base = {(r, c) for r, c in stuck if r < engine.depth and c < engine.width}
        outcome, residual = self.behave(base)
        observed = sorted(base)[self.observe_drop:]
        report = {"junit": OK_JUNIT, "march_log": {"events": len(observed)}, "march_fail_bitmap": _cells(observed),
                  "fail_bitmap": _cells(residual), "fail_scan_unknown_addrs": []}
        if self.finish:
            report["self_repair"] = outcome
        return SimpleNamespace(report=report)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    return path


def _engine(config_path, monkeypatch, chip=None, **kwargs) -> ys.SelfRepairEngine:
    kwargs.setdefault("num_spare_rows", 2)
    engine = ys.SelfRepairEngine(config_path, config_path.parent / "work", **kwargs)
    if chip is not None:
        chip.engine = engine
        monkeypatch.setattr(ys, "run_simulation", chip)
    return engine


def _pass(base):
    return {"self_repair_fail": 0, "unrepairable": 0, "row_repair_en": 0, "faulty_row_addr": 0}, set()


def test_the_build_is_a_self_repair_wrapper_on_an_injectable_model(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, num_spare_cols=1, diagnosis_entries=3)
    module = engine.module_dir
    snapshot = yaml.safe_load((module / "config.yml").read_text(encoding="utf-8"))
    redundancy = snapshot["redundancy"]
    assert redundancy["onchip_selfrepair"] and redundancy["onchip_col_repair"]
    assert redundancy["num_diagnosis_entries"] == 3 and snapshot["ports"]["spare_wen"] == "spare_wen0"
    model = engine.model_path.read_text(encoding="utf-8")
    assert "module sram_sweep" in model and "FAULT-INJECTABLE" in model
    sa0, sa1 = (str(p.resolve()) for p in engine.mask_paths)
    assert f'$readmemh("{sa0}", sa0_mask);' in model and f'$readmemh("{sa1}", sa1_mask);' in model
    assert "reg [4:0] sa0_mask" in model  # 4 bits plus the spare lane


def test_masks_cover_the_physical_array_spare_rows_and_lanes_included(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, num_spare_cols=1)
    engine._write_masks({(8, 2): 0, (1, 4): 1, (9, 4): 1})  # spare row 0, spare lane 0, corner
    sa0 = engine.mask_paths[0].read_text().split()
    sa1 = engine.mask_paths[1].read_text().split()
    assert len(sa0) == len(sa1) == 1 << engine.geometry.mem_addr_width == 16
    assert sa0[8] == "1B" and sa1[1] == "10" and sa1[9] == "10" and sa0[0] == "1F"


def test_stuck_cells_split_into_base_cells_and_spare_faults(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, num_spare_cols=1)
    base, faults, spare = engine._split({(2, 1): 1, (8, 3): 0, (5, 4): 1, (9, 4): 0})
    assert base == {(2, 1)} and spare == 3
    assert faults.row_cells == {0: frozenset({3})} and faults.col_cells == {0: frozenset({5})}
    assert faults.corners == frozenset({(1, 0)})


def test_the_latched_signature_is_decoded_lsb_first(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, num_spare_rows=2, num_spare_cols=1)
    solution = engine._signature({"row_repair_en": 0b10, "faulty_row_addr": (5 << 3) | 2,
                                  "col_repair_en": 1, "faulty_bit": 3})
    assert solution.row_map == {5: 1} and solution.col_map == {3: 0}


def test_a_clean_repair_counts_and_the_build_is_reused(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 0, "unrepairable": 0, "row_repair_en": 1,
                                   "faulty_row_addr": min(base)[0]}, set()))
    engine = _engine(config_path, monkeypatch, chip)
    record = engine.run_stuck({(3, 1): 1})
    assert record.repaired and record.extras["cause"] is None and record.spares_used == 1
    assert record.min_spares == 1
    engine.run_stuck({(4, 0): 0})
    assert all(run["COCOTB_TEST_MODULES"] == "test_selfrepair_sweep" for run in chip.runs)
    assert all(run["SRAM_SOURCE"] == str(engine.model_path) for run in chip.runs)


def test_a_gave_up_analyzer_on_a_solvable_map_is_the_analyzers_fault(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 1, "unrepairable": 1}, base))
    record = _engine(config_path, monkeypatch, chip).run_stuck({(3, 1): 1})
    assert record.status == "unrepairable" and record.extras["cause"] == "analyzer"


def test_an_allocated_faulty_spare_is_named(config_path, monkeypatch) -> None:
    # row 3 is faulty and so is spare row 0 at bit 0 (physical row 8); the chip puts row 3 on spare 0
    chip = FakeChip(lambda base: ({"self_repair_fail": 1, "unrepairable": 0, "row_repair_en": 1,
                                   "faulty_row_addr": 3}, {(3, 0)}))
    record = _engine(config_path, monkeypatch, chip).run_stuck({(3, 1): 1, (8, 0): 0})
    assert record.extras["truth_repairable"] and record.extras["cause"] == "faulty_spare"
    assert record.extras["spare_cells"] == 1


def test_an_analyze_pass_that_missed_a_cell_is_an_observation_escape(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 1, "unrepairable": 0, "row_repair_en": 1,
                                   "faulty_row_addr": 6}, {(1, 1)}), observe_drop=1)
    record = _engine(config_path, monkeypatch, chip).run_stuck({(1, 1): 1, (6, 2): 1})
    assert record.extras["cause"] == "observation_escape" and record.extras["fail_cell_count"] == 1


def test_a_pass_with_a_dirty_scan_is_a_false_pass(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 0, "unrepairable": 0}, base))
    record = _engine(config_path, monkeypatch, chip).run_stuck({(2, 2): 0})
    assert record.status == "unrepairable" and record.extras["cause"] == "false_pass"
    assert record.extras["escapes"] == 1


def test_an_exactly_unrepairable_trial_has_no_cause(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 1, "unrepairable": 1}, base))
    record = _engine(config_path, monkeypatch, chip, num_spare_rows=1).run_stuck({(0, 0): 1, (1, 1): 1})
    assert not record.extras["truth_repairable"] and record.extras["cause"] is None


def test_an_unfinished_trial_is_invalid(config_path, monkeypatch) -> None:
    record = _engine(config_path, monkeypatch, FakeChip(_pass, finish=False)).run_stuck({(1, 1): 1})
    assert record.status == "invalid" and "SELF_REPAIR_RESULT" in record.invalid_reason


def test_the_report_gives_the_normalized_rate_and_the_causes(config_path, monkeypatch) -> None:
    chip = FakeChip(lambda base: ({"self_repair_fail": 1, "unrepairable": 1}, base) if len(base) > 1
                    else ({"self_repair_fail": 0, "unrepairable": 0, "row_repair_en": 1,
                           "faulty_row_addr": min(base)[0] if base else 0}, set()))
    engine = _engine(config_path, monkeypatch, chip, expose_spares=True, num_spare_cols=1)
    report = build_report(run_sweep(engine, [1, 2], trials=8, seed_scheme="linear"), trial_detail="none")
    assert report["geometry"]["spares_exposed"] and report["geometry"]["area_ratio"] == pytest.approx(10 * 5 / 32)
    assert report["observation"]["mode"] == "onchip-selfrepair"
    two = report["points"][1]["self_repair"]
    assert set(two) == {"exact_repair_rate", "onchip_repair_rate", "normalized_repair_rate", "causes",
                        "diagnosis_overflows"}
    assert two["onchip_repair_rate"] <= two["exact_repair_rate"]


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"num_spare_rows": 0}, "at least one spare row"),
    ({"diagnosis_entries": -1}, "non-negative"),
    ({"defects": "blobs"}, "unknown defect preset"),
])
def test_impossible_setups_are_refused(config_path, monkeypatch, kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        _engine(config_path, monkeypatch, **kwargs)


def test_a_config_that_declares_redundancy_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump({**CONFIG, "redundancy": {"num_spare_rows": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="must not declare redundancy"):
        ys.SelfRepairEngine(path, tmp_path / "work", num_spare_rows=1)

"""RtlEngine without a simulator: generation is faked and the "simulation" reads
back the masks the engine wrote, so compile-once, truth bookkeeping and the
observed-versus-truth verdicts can be checked directly."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import autombist.yield_sweep as ys
from autombist.fault_gen import FaultType, generate_fault_masks
from autombist.yieldsim.sweep import build_report, run_sweep

CONFIG = {
    "memory_name": "sram_tiny", "wrapper_module_name": "sram_tiny_mbist", "addr_width": 3, "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
OK_JUNIT = {"exists": True, "summary": {"failures": 0, "errors": 0}, "tests": []}


def _read_masks(module: Path) -> tuple[list[int], list[int]]:
    sa0 = [int(line, 16) for line in (module / "faults" / "sa0_faults.hex").read_text().split()]
    sa1 = [int(line, 16) for line in (module / "faults" / "sa1_faults.hex").read_text().split()]
    return sa0, sa1


class FakeRtl:
    """generate_from_config and run_simulation stand-ins. ``drop`` removes
    observed cells (an escape), ``add`` adds some (overkill), ``complete=False``
    leaves out the completion marker."""

    def __init__(self, drop: int = 0, add: frozenset = frozenset(), complete: bool = True) -> None:
        self.generated = 0
        self.runs = []
        self.drop, self.add, self.complete = drop, add, complete

    def generate(self, config_path, outdir, **kwargs):
        self.generated += 1
        outdir.mkdir(parents=True, exist_ok=True)
        return outdir / "sram_tiny_mbist.v"

    def simulate(self, module, *, fail_scan=False, march_log=False):
        self.runs.append((fail_scan, march_log))
        tf_path = module / "faults" / "tf_up_faults.hex"
        if tf_path.exists():
            words = [int(line, 16) for line in tf_path.read_text().split()]
            truth = {(a, b) for a, w in enumerate(words) for b in range(CONFIG["data_width"]) if (w >> b) & 1}
        else:
            truth = ys._stuck_cells(*_read_masks(module), CONFIG["data_width"])
        cells = sorted(truth)[self.drop:]
        cells = sorted(set(cells) | self.add)
        bitmap = [{"addr": a, "bit": b} for a, b in cells]
        report = {"junit": OK_JUNIT}
        if self.complete and fail_scan:
            report.update({"fail_bitmap": bitmap, "fail_scan_unknown_addrs": []})
        if self.complete and march_log:
            report.update({"march_fail_bitmap": bitmap, "march_log": {"events": len(cells)}})
        return SimpleNamespace(report=report)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    return path


def _engine(config_path: Path, monkeypatch, fake: FakeRtl, **kwargs) -> ys.RtlEngine:
    monkeypatch.setattr(ys, "generate_from_config", fake.generate)
    monkeypatch.setattr(ys, "run_simulation", fake.simulate)
    return ys.RtlEngine(config_path, config_path.parent / "dut", num_spare_rows=kwargs.pop("num_spare_rows", 2),
                        **kwargs)


def test_the_wrapper_is_generated_once_for_the_whole_sweep(config_path, monkeypatch) -> None:
    fake = FakeRtl()
    engine = _engine(config_path, monkeypatch, fake)
    run_sweep(engine, [1, 2], trials=3, seed_scheme="linear")
    assert fake.generated == 1 and len(fake.runs) == 6
    assert all(run == (True, False) for run in fake.runs)


def test_random_cells_are_the_same_masks_the_per_trial_sweep_injects(config_path, monkeypatch) -> None:
    fake = FakeRtl()
    engine = _engine(config_path, monkeypatch, fake)
    record = engine.run_trial(3, seed=21)
    sa0, sa1 = generate_fault_masks(addr_width=3, data_width=4, fault_type=FaultType.STUCK_AT, faults=3, seed=21)
    assert _read_masks(engine.module_dir) == (sa0, sa1)
    assert record.extras["truth_cells"] == 3 and record.cells == 3


def test_an_observation_that_misses_an_injected_cell_is_an_escape(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, FakeRtl(drop=1), observe="march")
    record = engine.run_trial(2, seed=4)
    assert record.status == "unrepairable"
    assert record.extras["escapes"] >= 1 and record.extras["observed_repairable"]
    assert record.extras["truth_repairable"] and record.extras["overkill"] == 0


def test_observed_cells_that_were_never_injected_are_overkill(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, FakeRtl(add=frozenset({(7, 3)})), num_spare_rows=4)
    record = engine.run_trial(1, seed=0)
    assert record.extras["overkill"] == 1 and record.repaired


@pytest.mark.parametrize(("observe", "reason"), [
    ("functional", "fail scan did not complete"),
    ("march", "march log did not complete"),
])
def test_an_incomplete_observation_is_invalid(config_path, monkeypatch, observe: str, reason: str) -> None:
    engine = _engine(config_path, monkeypatch, FakeRtl(complete=False), observe=observe)
    record = engine.run_trial(1, seed=0)
    assert record.status == "invalid" and reason in record.invalid_reason


def test_a_defect_preset_writes_each_defect_stuck_at_one_value(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, FakeRtl(), defects="ohler-d2")
    polarities = set()
    for seed in range(30):
        record = engine.run_trial(2, seed)
        assert sum(record.extras["defects"].values()) == 2
        sa0, sa1 = _read_masks(engine.module_dir)
        for addr in range(8):
            for bit in range(4):
                low, high = (sa0[addr] >> bit) & 1, (sa1[addr] >> bit) & 1
                assert (low, high) in ((1, 0), (0, 0), (1, 1))  # normal, stuck at 0, stuck at 1
                if (low, high) != (1, 0):
                    polarities.add(high)
    assert polarities == {0, 1}
    assert engine.unit == "defects"


def test_the_report_carries_the_engine_blocks_and_observed_versus_truth(config_path, monkeypatch) -> None:
    engine = _engine(config_path, monkeypatch, FakeRtl(drop=1), observe="march", fault_type="transition-up")
    report = build_report(run_sweep(engine, [2], trials=4, seed_scheme="linear"), trial_detail="all")
    assert report["engine"] == {"name": "rtl-march-log"}
    assert report["observation"] == {"mode": "march-log", "uses_algo": True}
    assert report["spare_budget"] == {"num_spare_rows": 2, "num_spare_cols": 0}
    assert report["config"]["fault_type"] == "transition-up"
    truth = report["points"][0]["observed_vs_truth"]
    assert truth["trials"] == 4 and truth["escape_trials"] >= 1


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"observe": "telepathy"}, "unknown observation"),
    ({"fault_type": "transition-up"}, "stuck-at faults only"),
    ({"observe": "march", "fault_type": "transition-up", "defects": "singles"}, "needs --fault-type stuck-at"),
    ({"defects": "blobs"}, "unknown defect preset"),
])
def test_impossible_setups_are_refused(config_path, monkeypatch, kwargs, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        _engine(config_path, monkeypatch, FakeRtl(), **kwargs)

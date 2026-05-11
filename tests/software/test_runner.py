from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import generate_from_config
from autombist.runner import run_simulation


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def _base_config() -> dict[str, object]:
    return {
        "memory_name": "sram_1rw",
        "wrapper_module_name": "sram_1rw_mbist",
        "addr_width": 10,
        "data_width": 32,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "we0",
            "csb": "csb0",
        },
    }


def test_run_simulation_uses_clean_path_when_config_disables_saboteur(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yml"
    outdir = tmp_path / "out"
    _write_yaml(config_path, _base_config())
    wrapper_path = generate_from_config(config_path, outdir, use_saboteur=False)
    module_outdir = wrapper_path.parent

    # Simulate stale artifacts from a previous fault generation.
    (module_outdir / "Makefile").write_text("all:\n\t@echo stale\n", encoding="utf-8")
    (module_outdir / "sram_1rw_saboteur.v").write_text("// stale saboteur\n", encoding="utf-8")

    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    result = run_simulation(module_outdir, verbose=False)
    assert result.returncode == 0
    assert "fault-test" not in captured["command"]
    assert "USE_SABOTEUR=0" in captured["command"]


def test_run_simulation_legacy_outputs_still_use_fault_makefile(
    tmp_path: Path, monkeypatch
) -> None:
    module_outdir = tmp_path / "legacy"
    module_outdir.mkdir(parents=True)
    (module_outdir / "sram_1rw_mbist.v").write_text("module sram_1rw_mbist; endmodule\n", encoding="utf-8")
    (module_outdir / "sram_1rw_saboteur.v").write_text("module sram_1rw_saboteur; endmodule\n", encoding="utf-8")
    (module_outdir / "Makefile").write_text("fault-test:\n\t@echo legacy\n", encoding="utf-8")

    # Legacy config without autombist_* metadata.
    (module_outdir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "memory_name": "sram_1rw",
                "wrapper_module_name": "sram_1rw_mbist",
                "addr_width": 10,
                "data_width": 32,
                "we_active_low": True,
                "ports": {
                    "clk": "clk0",
                    "addr": "addr0",
                    "din": "din0",
                    "dout": "dout0",
                    "we": "we0",
                    "csb": "csb0",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("autombist.runner.subprocess.run", fake_run)

    result = run_simulation(module_outdir, verbose=False)
    assert result.returncode == 0
    assert captured["command"][-1] == "sim"
    assert "USE_SABOTEUR=1" in captured["command"]
    assert "FAULT_MODE=faults" in captured["command"]
    assert any(token.startswith("PYTHON_BIN=") for token in captured["command"])

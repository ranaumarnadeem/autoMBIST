"""`safety-report` on the JSON a real `autombist test` campaign writes, so the two
cannot drift apart. mats_plus against march_ss is the control: the same fault list
must give the weaker test a lower figure.

Skips without Verilator, which the fault engine needs.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autombist.cli import app

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator 5.x on PATH")

runner = CliRunner()
FAULTS = """\
SA0 1 0 0 0 0 0
SA1 2 0 0 0 0 0
TF0 3 1 0 0 0 0
TF1 4 1 0 0 0 0
RDF0 5 0 0 0 0 0
DRDF1 6 0 0 0 0 0
AF_NOACC 7 0 0 0 0 0
CFIN 8 0 9 0 0 0
"""


def _report(tmp_path: Path, algo: str, rates: dict[str, float]) -> dict:
    faults = tmp_path / "faults.txt"
    faults.write_text(FAULTS, encoding="utf-8")
    campaign = tmp_path / f"{algo}.json"
    result = runner.invoke(app, ["test", "-aw", "4", "-dw", "4", "--algo", algo, "--faults", str(faults),
                                 "--report", str(campaign), "--fmt", "json"])
    assert result.exit_code == 0, result.output
    rates_path = tmp_path / "rates.json"
    rates_path.write_text(json.dumps(rates), encoding="utf-8")
    out = tmp_path / f"{algo}_safety"
    result = runner.invoke(app, ["safety-report", "--campaign", str(campaign), "--failure-rates", str(rates_path),
                                 "--out", str(out)])
    assert result.exit_code == 0, result.output
    return json.loads((out / "safety_report.json").read_text(encoding="utf-8"))


def test_safety_report_on_a_real_campaign(tmp_path: Path) -> None:
    rates = {"stuck_at": 40, "transition": 20, "read_write": 20, "addressing": 10, "coupling": 10}

    strong = _report(tmp_path, "march_ss", rates)
    weak = _report(tmp_path, "mats_plus", rates)

    assert strong["fault_list"]["records"] == 8
    assert strong["fault_list"]["sha256"] == weak["fault_list"]["sha256"]
    assert strong["campaign"]["tool_versions"]["simulator"].startswith("Verilator")
    assert strong["unmapped"] == []
    by_class = {row["class"]: row for row in strong["classes"]}
    assert (by_class["stuck_at"]["total"], by_class["coupling"]["total"]) == (2, 1)
    assert by_class["retention"]["coverage_percent"] is None
    assert strong["diagnostic_coverage_percent"] > weak["diagnostic_coverage_percent"]

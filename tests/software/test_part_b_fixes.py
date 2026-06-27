from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.generator import generate_from_config
from autombist.reporting import coverage_meets_threshold, parse_junit_xml


def test_coverage_meets_threshold_gate() -> None:
    # Part B #3: optional coverage gate.
    rep = {"fault_metrics": {"coverage_percent": 100.0}}
    assert coverage_meets_threshold(rep, None) == (True, None)        # no gate
    assert coverage_meets_threshold(rep, 90.0) == (True, 100.0)       # above
    ok, cov = coverage_meets_threshold({"fault_metrics": {"coverage_percent": 75.0}}, 90.0)
    assert ok is False and cov == 75.0                                # below
    assert coverage_meets_threshold({"fault_metrics": {}}, 90.0) == (True, None)  # not reported


def test_parse_junit_xml_handles_malformed(tmp_path: Path) -> None:
    # Part B #8: a truncated results.xml must not raise an uncaught ET.ParseError.
    bad = tmp_path / "results.xml"
    bad.write_text("<testsuite><testcase ...", encoding="utf-8")
    result = parse_junit_xml(bad)
    assert result["exists"] is True
    assert result.get("parse_error") is True
    assert result["summary"]["tests"] == 0


def test_generate_rejects_invalid_fault_type(tmp_path: Path) -> None:
    # Part B #9: invalid --fault-type is rejected early, even without --test.
    config = {
        "memory_name": "m",
        "wrapper_module_name": "m_mbist",
        "addr_width": 4,
        "data_width": 8,
        "we_active_low": True,
        "ports": {
            "clk": "clk0",
            "addr": "addr0",
            "din": "din0",
            "dout": "dout0",
            "we": "web0",
            "csb": "csb0",
        },
    }
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid fault_type"):
        generate_from_config(config_path, tmp_path / "out", fault_type="bogus")

from __future__ import annotations

from pathlib import Path
import json

import pytest

from autombist.reporting import build_simulation_report, parse_fault_metrics, render_text_report


def test_parse_fault_metrics_recognizes_fault_count() -> None:
    metrics = parse_fault_metrics(
        stdout="[autombist] Result: PASS\n[autombist] Fault count: 100\n[autombist] Seed: 42\n",
        stderr="",
    )

    assert metrics.injected_faults == 100
    assert metrics.detected_faults is None
    assert metrics.total_fault_sites is None
    assert metrics.coverage_percent is None


def test_build_simulation_report_falls_back_to_requested_faults(tmp_path: Path) -> None:
    report = build_simulation_report(
        tool_version="0.3.1",
        config={
            "memory_name": "input_demo_8x16_scn4m",
            "wrapper_module_name": "input_demo_8x16_scn4m_mbist",
            "addr_width": 4,
            "data_width": 8,
            "we_active_low": True,
        },
        command=["make", "-C", "out/input_demo_8x16_scn4m", "fault-test"],
        cwd=Path("/mnt/c/Users/Potato/Desktop/openMBIST"),
        log_path=tmp_path / "simulate.log",
        report_path=tmp_path / "reports" / "latest.json",
        returncode=0,
        runtime_seconds=1.234,
        stdout="[autombist] Result: PASS\n",
        stderr="",
        use_saboteur=True,
        faults=100,
        fault_seed=42,
        fault_type="transition-up",
        pulse_width_ns=2,
        algo="march-raw",
        results_xml_path=tmp_path / "results.xml",
    )

    assert report["fault_metrics"]["injected_faults"] == 100
    assert report["summary"].startswith("autombist: simulation PASS")
    assert "coverage: not reported by the simulator" in report["summary"]


def test_parse_fault_metrics_from_junit_xml(tmp_path: Path) -> None:
        # Simulate a results.xml with system-out containing coverage lines
        xml = """
        <testsuites>
            <testsuite>
                <testcase classname="test_mbist" name="test_faults">
                    <system-out>
Fault summary
Fault coverage: 5/10 (50.00%)
Injected faults: 10
                    </system-out>
                </testcase>
            </testsuite>
        </testsuites>
        """

        # write to disk and let the runner/readers pick it up via parse
        results_path = tmp_path / "results.xml"
        results_path.write_text(xml, encoding="utf-8")

        # use the parser directly on the XML's system-out contents
        from autombist.reporting import parse_fault_metrics

        # extract the system-out we just wrote (replicating runner behavior)
        import xml.etree.ElementTree as ET

        tree = ET.parse(str(results_path))
        outs = []
        for elem in tree.getroot().iter():
                if elem.tag.endswith("system-out") and elem.text:
                        outs.append(elem.text)
        combined = "\n".join(outs)

        metrics = parse_fault_metrics(combined, "")
        assert metrics.injected_faults == 10
        assert metrics.detected_faults == 5
        assert metrics.total_fault_sites == 10
        assert abs(metrics.coverage_percent - 50.0) < 1e-6


def test_render_text_report_matches_sample_artifact() -> None:
    workspace = Path(__file__).resolve().parents[2]
    report_json = workspace / "out" / "input_demo_8x16_scn4m" / "reports" / "latest.json"
    fault_log = workspace / "out" / "input_demo_8x16_scn4m" / "fault_sim.log"
    report_txt = workspace / "out" / "input_demo_8x16_scn4m" / "reports" / "report.txt"

    if not all(p.exists() for p in (report_json, fault_log, report_txt)):
        pytest.skip("Local simulation artifacts not present (run autombist simulate first)")

    report = json.loads(report_json.read_text(encoding="utf-8"))
    rendered = render_text_report(report, fault_log.read_text(encoding="utf-8"))

    assert rendered == report_txt.read_text(encoding="utf-8")

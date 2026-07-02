from __future__ import annotations

from pathlib import Path
import json

import pytest

from autombist.reporting import (
    build_simulation_report,
    format_simulation_summary,
    parse_fault_metrics,
    parse_junit_xml,
    parse_transition_metrics,
    render_text_report,
    write_simulation_report,
    write_text_report,
    _extract_fault_summary_block,
    _float_or_none,
    _parse_addr_bits_map,
)


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


# ---------------------------------------------------------------------------
# _float_or_none (lines 45-47)
# ---------------------------------------------------------------------------


def test_float_or_none_returns_none_for_none() -> None:
    assert _float_or_none(None) is None


def test_float_or_none_returns_none_for_empty_string() -> None:
    assert _float_or_none("") is None


def test_float_or_none_parses_real_value() -> None:
    assert _float_or_none("3.5") == 3.5


# ---------------------------------------------------------------------------
# _parse_addr_bits_map (lines 72-85)
# ---------------------------------------------------------------------------


def test_parse_addr_bits_map_empty_string() -> None:
    assert _parse_addr_bits_map("") == {}


def test_parse_addr_bits_map_lone_dash() -> None:
    assert _parse_addr_bits_map("-") == {}


def test_parse_addr_bits_map_well_formed() -> None:
    assert _parse_addr_bits_map("addr1:3,addr2:5") == {"addr1": 3, "addr2": 5}


def test_parse_addr_bits_map_skips_malformed_entries() -> None:
    # "no_colon" has no ":" -> skipped entirely.
    # "addr3:oops" has a non-numeric count -> skipped via ValueError.
    value = "addr1:3,no_colon,addr3:oops,addr2:5"
    assert _parse_addr_bits_map(value) == {"addr1": 3, "addr2": 5}


# ---------------------------------------------------------------------------
# parse_transition_metrics (lines 109-110, 114, 117)
# ---------------------------------------------------------------------------


def test_parse_transition_metrics_full_fixture() -> None:
    stdout = """
Transition model: slow-to-rise
Algorithm: march-raw
Fault direction: rise
Writes with transition attempts: 12
Read verifications: 11
Detected delayed bits: 4
Resolved-before-read bits: 2
Pending overwrites before read: 1
Unverified pending writes at done: 0
Max pending depth: 3
Top blocked addresses (addr:bits): addr1:3,addr2:5
Top detected addresses (addr:bits): addr3:1,addr4:2
"""
    metrics = parse_transition_metrics(stdout, "")

    assert metrics["model"] == "slow-to-rise"
    assert metrics["algo"] == "march-raw"
    assert metrics["direction"] == "rise"
    assert metrics["blocked_write_cycles"] == 12
    assert metrics["read_verifications"] == 11
    assert metrics["detected_bit_events"] == 4
    assert metrics["resolved_before_read_bits"] == 2
    assert metrics["pending_overwrites"] == 1
    assert metrics["unverified_pending_writes"] == 0
    assert metrics["max_pending_depth"] == 3
    assert metrics["top_blocked_addresses"] == {"addr1": 3, "addr2": 5}
    assert metrics["top_detected_addresses"] == {"addr3": 1, "addr4": 2}


def test_parse_transition_metrics_empty_input_has_no_keys() -> None:
    metrics = parse_transition_metrics("", "")
    assert metrics == {}


# ---------------------------------------------------------------------------
# parse_junit_xml (lines 143-209)
# ---------------------------------------------------------------------------


def test_parse_junit_xml_missing_file_returns_default(tmp_path: Path) -> None:
    result = parse_junit_xml(tmp_path / "does_not_exist.xml")
    assert result["exists"] is False
    assert result["summary"] == {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_seconds": 0.0,
    }
    assert result["tests"] == []
    assert result["system_out"] == []


def test_parse_junit_xml_malformed_xml_returns_parse_error(tmp_path: Path) -> None:
    xml_path = tmp_path / "broken.xml"
    xml_path.write_text("<testsuite><unterminated>", encoding="utf-8")

    result = parse_junit_xml(xml_path)

    assert result["exists"] is True
    assert result["parse_error"] is True
    assert result["tests"] == []


def test_parse_junit_xml_single_passing_testcase(tmp_path: Path) -> None:
    xml_path = tmp_path / "pass.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="0" skipped="0" time="1.5">
    <testcase classname="pkg.mod" name="test_ok" time="0.1" />
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    assert result["exists"] is True
    assert result["summary"]["tests"] == 1
    assert result["summary"]["time_seconds"] == 1.5
    assert len(result["tests"]) == 1
    entry = result["tests"][0]
    assert entry["status"] == "passed"
    assert "details" not in entry
    assert "system_out" not in entry
    assert result["system_out"] == []


def test_parse_junit_xml_failure_case(tmp_path: Path) -> None:
    xml_path = tmp_path / "fail.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="1" errors="0" skipped="0" time="0.2">
    <testcase classname="pkg.mod" name="test_fail">
        <failure message="assertion failed" type="AssertionError">traceback text</failure>
    </testcase>
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    entry = result["tests"][0]
    assert entry["status"] == "failed"
    assert entry["details"] == {
        "message": "assertion failed",
        "type": "AssertionError",
        "text": "traceback text",
    }


def test_parse_junit_xml_error_case(tmp_path: Path) -> None:
    xml_path = tmp_path / "error.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="1" skipped="0" time="0.2">
    <testcase classname="pkg.mod" name="test_error">
        <error message="boom" type="RuntimeError">stack trace</error>
    </testcase>
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    entry = result["tests"][0]
    assert entry["status"] == "error"
    assert entry["details"] == {
        "message": "boom",
        "type": "RuntimeError",
        "text": "stack trace",
    }


def test_parse_junit_xml_skipped_case(tmp_path: Path) -> None:
    xml_path = tmp_path / "skipped.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="0" skipped="1" time="0.0">
    <testcase classname="pkg.mod" name="test_skip">
        <skipped message="not applicable">skip body text</skipped>
    </testcase>
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    entry = result["tests"][0]
    assert entry["status"] == "skipped"
    assert entry["details"] == {"message": "not applicable", "text": "skip body text"}


def test_parse_junit_xml_testcase_system_out_captured(tmp_path: Path) -> None:
    xml_path = tmp_path / "case_stdout.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="0" skipped="0" time="0.1">
    <testcase classname="pkg.mod" name="test_out">
        <system-out>hello from testcase</system-out>
    </testcase>
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    entry = result["tests"][0]
    assert entry["system_out"] == "hello from testcase"
    assert "hello from testcase" in result["system_out"]


def test_parse_junit_xml_suite_level_system_out_captured(tmp_path: Path) -> None:
    xml_path = tmp_path / "suite_stdout.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="0" skipped="0" time="0.1">
    <system-out>hello from suite</system-out>
    <testcase classname="pkg.mod" name="test_out" />
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    assert "hello from suite" in result["system_out"]


def test_parse_junit_xml_testsuites_root_sums_multiple_suites(tmp_path: Path) -> None:
    xml_path = tmp_path / "multi_suite.xml"
    xml_path.write_text(
        """<testsuites>
    <testsuite name="suiteA" tests="2" failures="1" errors="0" skipped="0" time="1.0">
        <testcase classname="pkg.a" name="test_a1" />
        <testcase classname="pkg.a" name="test_a2">
            <failure message="fail a2" type="AssertionError">trace a2</failure>
        </testcase>
    </testsuite>
    <testsuite name="suiteB" tests="1" failures="0" errors="1" skipped="1" time="2.5">
        <testcase classname="pkg.b" name="test_b1">
            <error message="err b1" type="ValueError">trace b1</error>
        </testcase>
    </testsuite>
</testsuites>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    assert result["summary"] == {
        "tests": 3,
        "failures": 1,
        "errors": 1,
        "skipped": 1,
        "time_seconds": 3.5,
    }
    assert len(result["tests"]) == 3


def test_parse_junit_xml_missing_tests_attribute_falls_back_to_case_count(
    tmp_path: Path,
) -> None:
    xml_path = tmp_path / "no_tests_attr.xml"
    xml_path.write_text(
        """<testsuite name="suite1" failures="0" errors="0" skipped="0" time="0.5">
    <testcase classname="pkg.mod" name="test_one" />
    <testcase classname="pkg.mod" name="test_two" />
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    assert result["summary"]["tests"] == 2


def test_parse_junit_xml_missing_time_attribute_defaults_to_zero(tmp_path: Path) -> None:
    xml_path = tmp_path / "no_time_attr.xml"
    xml_path.write_text(
        """<testsuite name="suite1" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="pkg.mod" name="test_one" />
</testsuite>""",
        encoding="utf-8",
    )

    result = parse_junit_xml(xml_path)

    assert result["summary"]["time_seconds"] == 0.0


# ---------------------------------------------------------------------------
# _extract_fault_summary_block (lines 299-300, 305-315)
# ---------------------------------------------------------------------------


def test_extract_fault_summary_block_no_marker_returns_empty() -> None:
    assert _extract_fault_summary_block("no relevant lines here\njust noise\n") == ""


def test_extract_fault_summary_block_no_terminator_takes_to_end() -> None:
    text = "preamble\nFault summary\nline one\nline two\n"
    block = _extract_fault_summary_block(text)
    assert block == "Fault summary\nline one\nline two"


def test_extract_fault_summary_block_bounded_by_both_markers() -> None:
    text = (
        "preamble\n"
        "Fault summary\n"
        "Fault coverage: 5/10 (50.00%)\n"
        "Injected faults: 10\n"
        "trailing noise that should be excluded\n"
    )
    block = _extract_fault_summary_block(text)
    assert block == "Fault summary\nFault coverage: 5/10 (50.00%)\nInjected faults: 10"


# ---------------------------------------------------------------------------
# Shared helper for report-shape tests
# ---------------------------------------------------------------------------


def _base_config() -> dict:
    return {
        "memory_name": "input_demo_8x16_scn4m",
        "wrapper_module_name": "input_demo_8x16_scn4m_mbist",
        "addr_width": 4,
        "data_width": 8,
        "we_active_low": True,
    }


def _build_report(tmp_path: Path, *, fault_type: str, stdout: str = "") -> dict:
    return build_simulation_report(
        tool_version="0.3.1",
        config=_base_config(),
        command=["make", "fault-test"],
        cwd=tmp_path,
        log_path=tmp_path / "simulate.log",
        report_path=tmp_path / "reports" / "latest.json",
        returncode=0,
        runtime_seconds=1.234,
        stdout=stdout,
        stderr="",
        use_saboteur=True,
        faults=100,
        fault_seed=42,
        fault_type=fault_type,
        pulse_width_ns=2,
        algo="march-raw",
        results_xml_path=tmp_path / "results.xml",
    )


# ---------------------------------------------------------------------------
# render_text_report transition-fault branch (lines 335-342)
# ---------------------------------------------------------------------------


def test_render_text_report_transition_fault_branch(tmp_path: Path) -> None:
    transition_stdout = """
Transition model: slow-to-rise
Algorithm: march-raw
Fault direction: rise
Writes with transition attempts: 12
Read verifications: 11
Detected delayed bits: 4
Resolved-before-read bits: 2
Pending overwrites before read: 1
Unverified pending writes at done: 0
Max pending depth: 3
Top blocked addresses (addr:bits): addr1:3
Top detected addresses (addr:bits): addr2:5
"""
    report = _build_report(tmp_path, fault_type="transition-up", stdout=transition_stdout)

    rendered = render_text_report(report, "")

    assert "TRANSITION LIFECYCLE METRICS" in rendered
    assert "TABLE SEMANTICS (TRANSITION FAULTS)" in rendered
    assert "TABLE SEMANTICS (STUCK-AT FAULTS)" not in rendered


def test_render_text_report_stuck_at_branch_omits_transition_headings(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")

    rendered = render_text_report(report, "")

    assert "TABLE SEMANTICS (STUCK-AT FAULTS)" in rendered
    assert "TRANSITION LIFECYCLE METRICS" not in rendered
    assert "TABLE SEMANTICS (TRANSITION FAULTS)" not in rendered


# ---------------------------------------------------------------------------
# format_simulation_summary (lines 438-439, 446, 462)
# ---------------------------------------------------------------------------


def test_format_simulation_summary_includes_transition_line(tmp_path: Path) -> None:
    transition_stdout = "Detected delayed bits: 4\nResolved-before-read bits: 2\nPending overwrites before read: 1\n"
    report = _build_report(tmp_path, fault_type="transition-down", stdout=transition_stdout)

    summary = format_simulation_summary(report)

    assert "transition: detected-bits=4, resolved-before-read=2, overwrites=1" in summary


def test_format_simulation_summary_includes_coverage_line_when_detected_and_total_present(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path, fault_type="stuck-at", stdout="Fault coverage: 5/10 (50.00%)\n"
    )

    summary = format_simulation_summary(report)

    assert "coverage: 5/10 (50.00%)" in summary


def test_format_simulation_summary_includes_injected_faults_line(tmp_path: Path) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at", stdout="Fault count: 100\n")

    summary = format_simulation_summary(report)

    assert "injected faults: 100" in summary


def test_format_simulation_summary_controller_grading_with_coverage(tmp_path: Path) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")
    report["controller_grading"] = {
        "coverage_percent": 87.5,
        "detected": 35,
        "denominator": 40,
        "excluded_blackbox": 2,
    }

    summary = format_simulation_summary(report)

    assert "controller (FaultFlow scan SA): 35/40 (87.50%), excluded-blackbox=2" in summary


def test_format_simulation_summary_controller_grading_none_coverage_not_reported(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")
    report["controller_grading"] = {"coverage_percent": None}

    summary = format_simulation_summary(report)

    assert "controller (FaultFlow scan SA): not reported" in summary


def test_format_simulation_summary_controller_grading_non_numeric_coverage(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")
    report["controller_grading"] = {"coverage_percent": "n/a"}

    summary = format_simulation_summary(report)

    assert "controller (FaultFlow scan SA): not reported" in summary


# ---------------------------------------------------------------------------
# write_simulation_report / write_text_report
# ---------------------------------------------------------------------------


def test_write_simulation_report_creates_results_and_latest_json(tmp_path: Path) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")
    report_dir = tmp_path / "reports"

    latest_path = write_simulation_report(report, report_dir)

    results_path = report_dir / "results.json"
    latest_json_path = report_dir / "latest.json"
    assert latest_path == latest_json_path
    assert results_path.exists()
    assert latest_json_path.exists()

    results_payload = results_path.read_text(encoding="utf-8")
    latest_payload = latest_json_path.read_text(encoding="utf-8")
    assert results_payload == latest_payload

    parsed = json.loads(latest_payload)
    assert parsed["results_path"] == str(results_path)
    assert parsed["report_path"] == str(latest_json_path)
    assert report["results_path"] == str(results_path)
    assert report["report_path"] == str(latest_json_path)


def test_write_text_report_matches_render_text_report(tmp_path: Path) -> None:
    report = _build_report(tmp_path, fault_type="stuck-at")
    report_dir = tmp_path / "reports"
    fault_log_text = "Fault summary\nFault coverage: 5/10 (50.00%)\nInjected faults: 10\n"

    report_path = write_text_report(report, report_dir, fault_log_text)

    assert report_path == report_dir / "report.txt"
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8") == render_text_report(report, fault_log_text)

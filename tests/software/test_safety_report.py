"""The safety report: measured coverage per fault class, and a DC figure only when
the user gives failure rates, since the coverage autoMBIST measures counts fault
types equally and DC is weighted by failure rate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from cli_errors import assert_one_error_line
from typer.testing import CliRunner

import autombist.cli as cli_mod
from autombist.safety.report import SafetyReportError, build_safety_report, render_safety_report_md

runner = CliRunner()


def _fault(fault_type: str, detected: bool, vaddr: int = 0) -> dict:
    return {
        "index": 0, "type": fault_type, "vaddr": vaddr, "vbit": 0, "aaddr": 0, "abit": 0, "p0": 0, "p1": 0,
        "vport": 0, "aport": 0, "weight": None, "detected": detected,
    }


def _campaign(*faults: dict, **overrides) -> dict:
    return {
        "schema_version": "1.0.0",
        "tool_versions": {"autombist": "9.9.9", "simulator": "Verilator 5.048"},
        "algo_name": "march_c",
        "mem": {"addr_width": 8, "data_width": 8, "init_val": 1, "num_ports": 1},
        "sim": "verilator",
        "golden_clean": True,
        "detected": sum(f.get("detected") is True for f in faults),
        "total": len(faults),
        "faults": list(faults),
        **overrides,
    }


def _class(report: dict, name: str) -> dict:
    return next(row for row in report["classes"] if row["class"] == name)


def test_coverage_is_counted_per_class() -> None:
    report = build_safety_report(_campaign(
        _fault("SA0", True), _fault("SA1", True), _fault("CFIN", True), _fault("CFID", False),
    ))

    assert (_class(report, "stuck_at")["detected"], _class(report, "stuck_at")["total"]) == (2, 2)
    assert _class(report, "stuck_at")["coverage_percent"] == 100.0
    assert (_class(report, "coupling")["detected"], _class(report, "coupling")["total"]) == (1, 2)
    assert _class(report, "coupling")["coverage_percent"] == 50.0
    assert report["unweighted_coverage_percent"] == 75.0


def test_a_class_with_no_measurement_is_not_measured_not_zero_or_full() -> None:
    row = _class(build_safety_report(_campaign(_fault("SA0", True))), "retention")

    assert row["total"] == 0
    assert row["coverage_percent"] is None
    assert "not measured" in render_safety_report_md(build_safety_report(_campaign(_fault("SA0", True))))


def test_without_failure_rates_there_is_no_dc_and_the_output_says_so() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True)))

    assert report["diagnostic_coverage_percent"] is None
    markdown = render_safety_report_md(report)
    assert "not diagnostic coverage" in markdown
    assert "Diagnostic coverage:" not in markdown


def test_dc_is_weighted_by_the_failure_rates() -> None:
    report = build_safety_report(
        _campaign(_fault("SA0", True), _fault("CFIN", True), _fault("CFID", False)),
        {"stuck_at": 30.0, "coupling": 10.0},
    )

    # (30 * 1.0 + 10 * 0.5) / 40; unweighted it would be 2/3.
    assert report["diagnostic_coverage_percent"] == pytest.approx(87.5)
    assert report["unweighted_coverage_percent"] == pytest.approx(200 / 3)
    assert "Diagnostic coverage: 87.50%" in render_safety_report_md(report)


def test_a_failure_rate_for_a_class_not_measured_counts_as_undetected() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True)), {"stuck_at": 90.0, "retention": 10.0})

    assert report["diagnostic_coverage_percent"] == pytest.approx(90.0)


def test_a_rate_of_zero_leaves_a_measured_class_out_of_dc() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True), _fault("CFIN", False)),
                                 {"stuck_at": 1.0, "coupling": 0})

    assert report["diagnostic_coverage_percent"] == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("rates", "fragment"),
    [
        ({"stuck_at": 1.0}, "no failure rate for measured class coupling"),
        ({"stuck_at": 1.0, "coupling": 1.0, "stuck_att": 1.0}, "unknown fault class 'stuck_att'"),
        ({"stuck_at": -1.0, "coupling": 1.0}, "failure rate for stuck_at must be a finite number >= 0"),
        ({"stuck_at": float("inf"), "coupling": 1.0}, "failure rate for stuck_at must be a finite number >= 0"),
        ({"stuck_at": True, "coupling": 1.0}, "failure rate for stuck_at must be a finite number >= 0"),
        ({"stuck_at": "ten", "coupling": 1.0}, "failure rate for stuck_at must be a finite number >= 0"),
        ({"stuck_at": 0, "coupling": 0}, "failure rates add up to zero"),
        (["stuck_at"], "failure rates must be a mapping"),
    ],
)
def test_bad_failure_rates_are_refused(rates, fragment: str) -> None:
    with pytest.raises(SafetyReportError, match=fragment):
        build_safety_report(_campaign(_fault("SA0", True), _fault("CFIN", True)), rates)


def test_a_user_defined_fault_type_is_listed_as_unmapped_and_stays_out_of_dc() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True), _fault("MYFAULT", False)), {"stuck_at": 1.0})

    assert report["unmapped"] == [{"fault_type": "MYFAULT", "detected": 0, "total": 1}]
    assert report["diagnostic_coverage_percent"] == pytest.approx(100.0)
    assert report["unweighted_coverage_percent"] == pytest.approx(50.0)
    assert "MYFAULT" in render_safety_report_md(report)


def test_it_records_what_the_numbers_depend_on() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True)))

    assert report["campaign"]["algo_name"] == "march_c"
    assert report["campaign"]["mem"] == {"addr_width": 8, "data_width": 8, "init_val": 1, "num_ports": 1}
    assert report["campaign"]["tool_versions"] == {"autombist": "9.9.9", "simulator": "Verilator 5.048"}
    assert report["fault_list"]["records"] == 1
    markdown = render_safety_report_md(report)
    for text in ("march_c", "8x8", "Verilator 5.048", "9.9.9", report["fault_list"]["sha256"]):
        assert text in markdown


def test_the_fault_list_hash_follows_the_list_and_not_its_order_or_results() -> None:
    def digest(*faults: dict) -> str:
        return build_safety_report(_campaign(*faults))["fault_list"]["sha256"]

    one, two = _fault("SA0", True, vaddr=1), _fault("SA1", True, vaddr=2)

    assert digest(one, two) == digest(two, one)
    assert digest(one, two) == digest(_fault("SA0", False, vaddr=1), two)
    assert digest(one, two) != digest(one, _fault("SA1", True, vaddr=3))


def test_an_older_campaign_report_shows_versions_as_not_recorded() -> None:
    campaign = _campaign(_fault("SA0", True))
    del campaign["tool_versions"]

    report = build_safety_report(campaign)

    assert report["campaign"]["tool_versions"] is None
    assert "not recorded" in render_safety_report_md(report)


def test_it_says_what_it_does_not_claim() -> None:
    markdown = render_safety_report_md(build_safety_report(_campaign(_fault("SA0", True))))

    for text in ("not Annex D", "soft errors", "ASIL"):
        assert text in markdown


def test_it_prints_no_rating() -> None:
    report = build_safety_report(_campaign(_fault("SA0", True)), {"stuck_at": 1.0})

    assert not {"low", "medium", "high"} & set(render_safety_report_md(report).lower().split())
    assert "rating" not in report


def test_a_percentage_just_under_full_does_not_print_as_full() -> None:
    faults = [_fault("SA0", True, vaddr=i) for i in range(99999)] + [_fault("SA0", False, vaddr=99999)]

    markdown = render_safety_report_md(build_safety_report(_campaign(*faults), {"stuck_at": 1.0}))

    assert "100.00%" not in markdown
    assert "Diagnostic coverage: 99.99%" in markdown


@pytest.mark.parametrize(
    ("campaign", "fragment"),
    [
        (["not", "a", "mapping"], "not a campaign report"),
        ({"cells": []}, "not a campaign report"),
        (_campaign(), "no faults"),
        (_campaign(_fault("SA0", True), golden_clean=False), "golden run"),
        (_campaign(_fault("SA0", True), sequence={"matches": False}), "does not implement"),
        (_campaign(_fault("SA0", True), total=2), "do not match its 1 fault records"),
        (_campaign(_fault("SA0", True), detected=0), "do not match its 1 fault records"),
        (_campaign({"type": "SA0"}), "fault record 0"),
        (_campaign({"type": "SA0", "detected": "yes"}), "fault record 0"),
    ],
)
def test_a_campaign_that_is_no_evidence_is_refused(campaign, fragment: str) -> None:
    with pytest.raises(SafetyReportError, match=fragment):
        build_safety_report(campaign)


def _write_campaign(tmp_path: Path, campaign: dict) -> Path:
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(campaign), encoding="utf-8")
    return path


def test_the_command_writes_json_and_markdown(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path, _campaign(_fault("SA0", True), _fault("CFIN", False)))
    rates = tmp_path / "rates.yml"
    rates.write_text(yaml.safe_dump({"stuck_at": 3, "coupling": 1}), encoding="utf-8")
    out = tmp_path / "safety"

    result = runner.invoke(cli_mod.app, ["safety-report", "--campaign", str(campaign),
                                         "--failure-rates", str(rates), "--out", str(out)])

    assert result.exit_code == 0, result.output
    written = json.loads((out / "safety_report.json").read_text(encoding="utf-8"))
    assert written["diagnostic_coverage_percent"] == pytest.approx(75.0)
    assert "Diagnostic coverage: 75.00%" in (out / "safety_report.md").read_text(encoding="utf-8")
    assert "diagnostic coverage: 75.00%" in result.output


def test_the_command_without_rates_labels_the_figure_as_not_dc(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path, _campaign(_fault("SA0", True)))

    result = runner.invoke(cli_mod.app, ["safety-report", "--campaign", str(campaign), "--out", str(tmp_path / "s")])

    assert result.exit_code == 0, result.output
    assert "unweighted coverage: 100.00% (not diagnostic coverage)" in result.output


@pytest.mark.parametrize(
    ("campaign_text", "rates_text", "fragment"),
    [
        (None, None, "campaign report not found"),
        ("{not json", None, "is not JSON"),
        ('{"cells": []}', None, "not a campaign report"),
        ("CAMPAIGN", "a: [", "is not YAML"),
        ("CAMPAIGN", "- stuck_at", "failure rates must be a mapping"),
        ("CAMPAIGN", "", "no failure rate for measured class stuck_at"),
    ],
)
def test_the_command_fails_with_one_error_line_and_writes_nothing(
    tmp_path: Path, campaign_text: str | None, rates_text: str | None, fragment: str
) -> None:
    good = json.dumps(_campaign(_fault("SA0", True)))
    campaign = tmp_path / "campaign.json"
    if campaign_text is not None:
        campaign.write_text(good if campaign_text == "CAMPAIGN" else campaign_text, encoding="utf-8")
    args = ["safety-report", "--campaign", str(campaign), "--out", str(tmp_path / "s")]
    if rates_text is not None:
        rates = tmp_path / "rates.yml"
        rates.write_text(rates_text, encoding="utf-8")
        args += ["--failure-rates", str(rates)]

    result = runner.invoke(cli_mod.app, args)

    assert_one_error_line(result, fragment)
    assert not (tmp_path / "s").exists()


def test_the_command_names_a_missing_failure_rates_file(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path, _campaign(_fault("SA0", True)))
    missing = tmp_path / "missing.yml"

    result = runner.invoke(cli_mod.app, ["safety-report", "--campaign", str(campaign),
                                         "--failure-rates", str(missing), "--out", str(tmp_path / "s")])

    assert_one_error_line(result, f"failure rates not found: {missing}")


def test_the_command_reports_an_output_it_cannot_write(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path, _campaign(_fault("SA0", True)))
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory", encoding="utf-8")

    result = runner.invoke(cli_mod.app, ["safety-report", "--campaign", str(campaign), "--out", str(blocked)])

    assert_one_error_line(result, str(blocked))


def test_record_weights_are_named_as_ignored() -> None:
    weighted = {**_fault("SA0", True), "weight": 0.5}

    report = build_safety_report(_campaign(weighted))

    assert report["record_weights_ignored"] is True
    assert "ignores" in render_safety_report_md(report)
    assert "ignores" not in render_safety_report_md(build_safety_report(_campaign(_fault("SA0", True))))

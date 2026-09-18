"""Tests for console, markdown, JSON, and JUnit report generators."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from ananke.plexus.evals.models.report import (
    BaselineComparison,
    EvalGateDecision,
    EvalReport,
    GateVerdict,
)
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.reports.console import generate_console_report
from ananke.plexus.evals.reports.json import generate_json_report, write_json_report
from ananke.plexus.evals.reports.junit import generate_junit_xml, write_junit_report
from ananke.plexus.evals.reports.markdown import generate_markdown_report

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_score(
    evaluator_id: str = "ev",
    dimension: str = "outcome",
    metric: str = "exact_match",
    status: EvalStatus = EvalStatus.PASS,
    normalized: float | None = 0.9,
    reason: str | None = None,
    threshold: float | None = None,
) -> EvalScore:
    return EvalScore(
        evaluator_id=evaluator_id,
        dimension=dimension,
        metric=metric,
        status=status,
        normalized_score=normalized,
        reason=reason,
        threshold=threshold,
    )


def make_report(
    scores: list[EvalScore] | None = None,
    run_id: str = "run-001",
    suite_id: str = "suite-test",
    gate_decision: EvalGateDecision | None = None,
    baseline_comparison: BaselineComparison | None = None,
) -> EvalReport:
    # Use explicit None check (not `or`) so that an empty list is preserved.
    if scores is None:
        scores = [
            make_score("a", "outcome", "exact_match", EvalStatus.PASS, 0.9),
            make_score(
                "b", "safety", "secret_leakage", EvalStatus.FAIL, 0.0, reason="secrets found"
            ),
        ]
    return EvalReport(
        run_id=run_id,
        suite_id=suite_id,
        scores=scores,
        gate_decision=gate_decision,
        baseline_comparison=baseline_comparison,
    )


def make_gate(verdict: GateVerdict = GateVerdict.PASS, blocking=None) -> EvalGateDecision:
    return EvalGateDecision(
        verdict=verdict,
        suite_id="suite-test",
        run_id="run-001",
        blocking_dimensions=blocking or [],
    )


# ---------------------------------------------------------------------------
# generate_console_report
# ---------------------------------------------------------------------------


class TestConsoleReport:
    def test_returns_string(self):
        report = make_report()
        result = generate_console_report(report)
        assert isinstance(result, str)

    def test_non_empty(self):
        report = make_report()
        result = generate_console_report(report)
        assert len(result) > 0

    def test_contains_suite_id(self):
        report = make_report()
        result = generate_console_report(report)
        assert "suite-test" in result

    def test_contains_run_id(self):
        report = make_report()
        result = generate_console_report(report)
        assert "run-001" in result

    def test_contains_evaluator_dimension_and_metric(self):
        report = make_report()
        result = generate_console_report(report)
        assert "outcome" in result
        assert "exact_match" in result

    def test_contains_pass_status(self):
        report = make_report(scores=[make_score(status=EvalStatus.PASS)])
        result = generate_console_report(report)
        assert "PASS" in result

    def test_contains_fail_status(self):
        report = make_report(scores=[make_score(status=EvalStatus.FAIL)])
        result = generate_console_report(report)
        assert "FAIL" in result

    def test_contains_reason_when_fail(self):
        report = make_report(scores=[make_score(status=EvalStatus.FAIL, reason="bad output")])
        result = generate_console_report(report)
        assert "bad output" in result

    def test_contains_gate_verdict_when_present(self):
        gate = make_gate(GateVerdict.BLOCK)
        report = make_report(gate_decision=gate)
        result = generate_console_report(report)
        assert "BLOCK" in result

    def test_pass_count_in_output(self):
        scores = [
            make_score(status=EvalStatus.PASS),
            make_score(status=EvalStatus.FAIL),
        ]
        report = make_report(scores=scores)
        result = generate_console_report(report)
        assert "Passed: 1" in result
        assert "Failed: 1" in result

    def test_empty_scores(self):
        report = make_report(scores=[])
        result = generate_console_report(report)
        assert "Total: 0" in result


# ---------------------------------------------------------------------------
# generate_markdown_report
# ---------------------------------------------------------------------------


class TestMarkdownReport:
    def test_returns_string(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert isinstance(result, str)

    def test_contains_markdown_table_header(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert "|---" in result

    def test_contains_suite_id(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert "suite-test" in result

    def test_contains_run_id(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert "run-001" in result

    def test_contains_dimension_and_metric(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert "outcome" in result
        assert "exact_match" in result

    def test_contains_header(self):
        report = make_report()
        result = generate_markdown_report(report)
        assert "## " in result

    def test_contains_gate_verdict_when_present(self):
        gate = make_gate(GateVerdict.WARN, blocking=[])
        report = make_report(gate_decision=gate)
        result = generate_markdown_report(report)
        assert "WARN" in result

    def test_contains_baseline_section_when_present(self):
        bc = BaselineComparison(
            baseline_id="b1",
            regressions={"outcome": -0.05},
        )
        report = make_report(baseline_comparison=bc)
        result = generate_markdown_report(report)
        assert "b1" in result

    def test_threshold_displayed_when_set(self):
        report = make_report(scores=[make_score(threshold=0.8)])
        result = generate_markdown_report(report)
        assert "0.80" in result

    def test_no_threshold_shows_dash(self):
        report = make_report(scores=[make_score(threshold=None)])
        result = generate_markdown_report(report)
        assert "—" in result


# ---------------------------------------------------------------------------
# generate_json_report
# ---------------------------------------------------------------------------


class TestJsonReport:
    def test_returns_string(self):
        report = make_report()
        result = generate_json_report(report)
        assert isinstance(result, str)

    def test_valid_json(self):
        report = make_report()
        result = generate_json_report(report)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_run_id(self):
        report = make_report()
        result = generate_json_report(report)
        parsed = json.loads(result)
        assert parsed["run_id"] == "run-001"

    def test_contains_suite_id(self):
        report = make_report()
        result = generate_json_report(report)
        parsed = json.loads(result)
        assert parsed["suite_id"] == "suite-test"

    def test_contains_scores(self):
        report = make_report()
        result = generate_json_report(report)
        parsed = json.loads(result)
        assert isinstance(parsed["scores"], list)

    def test_write_json_report_creates_file(self, tmp_path):
        report = make_report()
        out = tmp_path / "report.json"
        result = write_json_report(report, out)
        assert result == out
        assert out.exists()

    def test_write_json_report_valid_content(self, tmp_path):
        report = make_report()
        out = tmp_path / "report.json"
        write_json_report(report, out)
        parsed = json.loads(out.read_text())
        assert parsed["run_id"] == "run-001"


# ---------------------------------------------------------------------------
# generate_junit_xml
# ---------------------------------------------------------------------------


class TestJunitXmlReport:
    def test_returns_string(self):
        report = make_report()
        result = generate_junit_xml(report)
        assert isinstance(result, str)

    def test_is_valid_xml(self):
        report = make_report()
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        assert root is not None

    def test_root_is_testsuite(self):
        report = make_report()
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        assert root.tag == "testsuite"

    def test_testsuite_name_contains_suite_id(self):
        report = make_report()
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        assert "suite-test" in root.get("name", "")

    def test_testcase_elements_exist(self):
        report = make_report()
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        testcases = root.findall("testcase")
        assert len(testcases) == len(report.scores)

    def test_failure_element_for_fail_score(self):
        scores = [make_score(status=EvalStatus.FAIL, reason="bad")]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        failures = root.findall(".//failure")
        assert len(failures) == 1

    def test_error_element_for_error_score(self):
        scores = [make_score(status=EvalStatus.ERROR, reason="crash")]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        errors = root.findall(".//error")
        assert len(errors) == 1

    def test_skipped_element_for_skipped_score(self):
        scores = [make_score(status=EvalStatus.SKIPPED, reason="no expected")]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        skipped = root.findall(".//skipped")
        assert len(skipped) == 1

    def test_pass_score_no_child_element(self):
        scores = [make_score(status=EvalStatus.PASS)]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        tc = root.findall("testcase")[0]
        # No failure/error/skipped child
        assert len(list(tc)) == 0

    def test_failures_attribute_correct(self):
        scores = [
            make_score(status=EvalStatus.FAIL),
            make_score(status=EvalStatus.PASS),
        ]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        assert root.get("failures") == "1"

    def test_tests_attribute_correct(self):
        scores = [make_score() for _ in range(3)]
        report = make_report(scores=scores)
        result = generate_junit_xml(report)
        root = ET.fromstring(result)
        assert root.get("tests") == "3"

    def test_write_junit_report_creates_file(self, tmp_path):
        report = make_report()
        out = tmp_path / "junit.xml"
        result = write_junit_report(report, out)
        assert result == out
        assert out.exists()
        # Verify it is valid XML
        ET.fromstring(out.read_text())

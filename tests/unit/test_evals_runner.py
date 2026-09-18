"""Tests for EvalRunner, save_eval_evidence, and run_suite_on_trace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.suite import EvalSuite, GatePolicy
from ananke.plexus.evals.models.trace import AgentTrace, Usage
from ananke.plexus.evals.runner import EvalRunner, run_suite_on_trace, save_eval_evidence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trace(output=None, run_id="run-001") -> AgentTrace:
    return AgentTrace(
        trace_id=run_id,
        run_id=run_id,
        runtime="test",
        spans=[],
        usage=Usage(),
        final_output=output,
    )


def make_case() -> EvalCase:
    return EvalCase(id="c1", input="hello")


def make_suite() -> EvalSuite:
    return EvalSuite(id="test-suite", policy=GatePolicy())


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


class _PassEvaluator:
    id = "test.pass"
    version = "1"
    dimension = "outcome"
    deterministic = True

    def evaluate(self, *, case, trace, context):
        return [
            EvalScore(
                evaluator_id=self.id,
                dimension=self.dimension,
                metric="always_pass",
                status=EvalStatus.PASS,
            )
        ]


class _FailEvaluator:
    id = "test.fail"
    version = "1"
    dimension = "safety"
    deterministic = True

    def evaluate(self, *, case, trace, context):
        return [
            EvalScore(
                evaluator_id=self.id,
                dimension=self.dimension,
                metric="always_fail",
                status=EvalStatus.FAIL,
                reason="intentional failure",
            )
        ]


class _ExplodingEvaluator:
    id = "test.exploding"
    version = "1"
    dimension = "outcome"
    deterministic = True

    def evaluate(self, *, case, trace, context):
        raise RuntimeError("This evaluator always crashes")


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------


class TestEvalRunner:
    def test_empty_evaluators_returns_report(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert isinstance(report, EvalReport)

    def test_empty_evaluators_empty_scores(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert report.scores == []

    def test_gate_decision_present(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert report.gate_decision is not None

    def test_pass_evaluator_produces_pass_score(self, ctx):
        runner = EvalRunner(evaluators=[_PassEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert any(s.status == EvalStatus.PASS for s in report.scores)

    def test_fail_evaluator_produces_fail_score(self, ctx):
        runner = EvalRunner(evaluators=[_FailEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert any(s.status == EvalStatus.FAIL for s in report.scores)

    def test_multiple_evaluators_all_scores_present(self, ctx):
        runner = EvalRunner(evaluators=[_PassEvaluator(), _FailEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert len(report.scores) == 2

    def test_exploding_evaluator_produces_error_score(self, ctx):
        runner = EvalRunner(evaluators=[_ExplodingEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert any(s.status == EvalStatus.ERROR for s in report.scores)

    def test_exploding_evaluator_run_still_completes(self, ctx):
        runner = EvalRunner(evaluators=[_ExplodingEvaluator(), _PassEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert len(report.scores) == 2  # error + pass

    def test_run_id_from_trace(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        trace = make_trace(run_id="my-run-id")
        report = runner.run(suite=make_suite(), case=make_case(), trace=trace)
        assert report.run_id == "my-run-id"

    def test_custom_run_id_overrides_trace(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        trace = make_trace(run_id="trace-run")
        report = runner.run(suite=make_suite(), case=make_case(), trace=trace, run_id="custom-run")
        assert report.run_id == "custom-run"

    def test_suite_id_in_report(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        suite = EvalSuite(id="my-suite-id", policy=GatePolicy())
        report = runner.run(suite=suite, case=make_case(), trace=make_trace())
        assert report.suite_id == "my-suite-id"

    def test_case_id_in_report(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(
            suite=make_suite(), case=EvalCase(id="case-42", input="x"), trace=make_trace()
        )
        assert report.case_id == "case-42"

    def test_started_at_set(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert report.started_at is not None

    def test_completed_at_set(self, ctx):
        runner = EvalRunner(evaluators=[], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        assert report.completed_at is not None

    def test_error_score_has_evaluator_id(self, ctx):
        runner = EvalRunner(evaluators=[_ExplodingEvaluator()], context=ctx)
        report = runner.run(suite=make_suite(), case=make_case(), trace=make_trace())
        error_scores = [s for s in report.scores if s.status == EvalStatus.ERROR]
        assert error_scores[0].evaluator_id == "test.exploding"


# ---------------------------------------------------------------------------
# save_eval_evidence
# ---------------------------------------------------------------------------


class TestSaveEvalEvidence:
    def _report(self) -> EvalReport:
        return EvalReport(
            run_id="ev-run",
            suite_id="test-suite",
            scores=[
                EvalScore(
                    evaluator_id="ev",
                    dimension="outcome",
                    metric="exact_match",
                    status=EvalStatus.PASS,
                )
            ],
        )

    def test_returns_path(self, tmp_path):
        report = self._report()
        result = save_eval_evidence(report, tmp_path)
        assert isinstance(result, Path)

    def test_eval_dir_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert eval_dir.is_dir()

    def test_scores_json_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "scores.json").exists()

    def test_scores_json_valid(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        data = json.loads((eval_dir / "scores.json").read_text())
        assert isinstance(data, list)

    def test_manifest_json_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "manifest.json").exists()

    def test_manifest_has_run_id(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        manifest = json.loads((eval_dir / "manifest.json").read_text())
        assert manifest["run_id"] == "ev-run"

    def test_reports_dir_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "reports").is_dir()

    def test_markdown_report_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "reports" / "report.md").exists()

    def test_json_summary_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "reports" / "summary.json").exists()

    def test_junit_xml_exists(self, tmp_path):
        report = self._report()
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "reports" / "junit.xml").exists()

    def test_policy_decision_written_when_gate_present(self, tmp_path):
        from ananke.plexus.evals.models.report import EvalGateDecision, GateVerdict

        report = EvalReport(
            run_id="ev-run",
            suite_id="s1",
            scores=[],
            gate_decision=EvalGateDecision(
                verdict=GateVerdict.PASS,
                suite_id="s1",
                run_id="ev-run",
            ),
        )
        eval_dir = save_eval_evidence(report, tmp_path)
        assert (eval_dir / "policy-decision.json").exists()


# ---------------------------------------------------------------------------
# run_suite_on_trace
# ---------------------------------------------------------------------------


class TestRunSuiteOnTrace:
    def test_returns_report(self, tmp_path):
        report = run_suite_on_trace(
            suite=make_suite(),
            case=make_case(),
            trace=make_trace(),
            evaluators=[],
            project_root=tmp_path,
        )
        assert isinstance(report, EvalReport)

    def test_with_evaluators(self, tmp_path):
        report = run_suite_on_trace(
            suite=make_suite(),
            case=make_case(),
            trace=make_trace(),
            evaluators=[_PassEvaluator()],
            project_root=tmp_path,
        )
        assert len(report.scores) == 1

    def test_custom_run_id(self, tmp_path):
        report = run_suite_on_trace(
            suite=make_suite(),
            case=make_case(),
            trace=make_trace(),
            evaluators=[],
            project_root=tmp_path,
            run_id="custom-123",
        )
        assert report.run_id == "custom-123"

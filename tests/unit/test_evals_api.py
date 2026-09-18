"""Tests for the high-level eval API (api.py)."""

from __future__ import annotations

from ananke.plexus.evals.api import (
    adapter_doctor,
    evaluate_trace,
    list_native_evaluators,
    run_evaluation,
)
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.suite import EvalSuite, GatePolicy
from ananke.plexus.evals.models.trace import AgentTrace, Usage
from ananke.plexus.evals.traces.exporters import export_trace_to_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_suite() -> EvalSuite:
    return EvalSuite(id="api-test-suite", policy=GatePolicy())


def make_case(cid: str = "c1") -> EvalCase:
    return EvalCase(id=cid, input="hello world")


def make_trace(output=None) -> AgentTrace:
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=[],
        usage=Usage(),
        final_output=output,
    )


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


# ---------------------------------------------------------------------------
# run_evaluation
# ---------------------------------------------------------------------------


class TestRunEvaluation:
    def test_returns_report(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="world",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        assert isinstance(report, EvalReport)

    def test_run_id_set(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        assert report.run_id is not None

    def test_suite_id_in_report(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        assert report.suite_id == "api-test-suite"

    def test_with_evaluator(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[_PassEvaluator()],
            save_evidence=False,
        )
        assert len(report.scores) == 1
        assert report.scores[0].status == EvalStatus.PASS

    def test_save_evidence_creates_directory(self, tmp_path):
        run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=True,
        )
        assert (tmp_path / ".ananke" / "evidence").exists()

    def test_save_evidence_false_no_directory(self, tmp_path):
        run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        # No evidence directory created
        assert not (tmp_path / ".ananke" / "evidence").exists()

    def test_custom_run_id(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
            run_id="my-custom-run",
        )
        assert report.run_id == "my-custom-run"

    def test_with_explicit_trace(self, tmp_path):
        trace = make_trace(output="explicit")
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            trace=trace,
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        assert report.run_id == "r1"

    def test_evaluators_none_defaults_to_empty(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=None,
            save_evidence=False,
        )
        assert report.scores == []

    def test_gate_decision_present(self, tmp_path):
        report = run_evaluation(
            suite=make_suite(),
            case=make_case(),
            output="result",
            project_root=tmp_path,
            evaluators=[],
            save_evidence=False,
        )
        assert report.gate_decision is not None


# ---------------------------------------------------------------------------
# evaluate_trace (from file)
# ---------------------------------------------------------------------------


class TestEvaluateTrace:
    def test_evaluate_trace_from_file(self, tmp_path):
        trace = make_trace("hello from trace")
        trace_path = tmp_path / "trace.json"
        export_trace_to_json(trace, trace_path)

        report = evaluate_trace(
            suite=make_suite(),
            trace_path=trace_path,
            project_root=tmp_path,
            evaluators=[],
        )
        assert isinstance(report, EvalReport)

    def test_evaluate_trace_with_custom_case(self, tmp_path):
        trace = make_trace("output")
        trace_path = tmp_path / "trace.json"
        export_trace_to_json(trace, trace_path)

        custom_case = EvalCase(id="custom-case", input="custom input")
        report = evaluate_trace(
            suite=make_suite(),
            trace_path=trace_path,
            case=custom_case,
            project_root=tmp_path,
            evaluators=[],
        )
        assert report.case_id == "custom-case"

    def test_evaluate_trace_default_case_when_none(self, tmp_path):
        trace = make_trace("output")
        trace_path = tmp_path / "trace.json"
        export_trace_to_json(trace, trace_path)

        report = evaluate_trace(
            suite=make_suite(),
            trace_path=trace_path,
            case=None,
            project_root=tmp_path,
            evaluators=[],
        )
        assert report.case_id == "trace-replay"

    def test_evaluate_trace_with_evaluators(self, tmp_path):
        trace = make_trace("result")
        trace_path = tmp_path / "trace.json"
        export_trace_to_json(trace, trace_path)

        report = evaluate_trace(
            suite=make_suite(),
            trace_path=trace_path,
            project_root=tmp_path,
            evaluators=[_PassEvaluator()],
        )
        assert any(s.status == EvalStatus.PASS for s in report.scores)


# ---------------------------------------------------------------------------
# adapter_doctor
# ---------------------------------------------------------------------------


class TestAdapterDoctor:
    def test_returns_dict(self):
        result = adapter_doctor()
        assert isinstance(result, dict)

    def test_has_mlflow_key(self):
        result = adapter_doctor()
        assert "ananke.adapters.mlflow" in result

    def test_has_deepeval_key(self):
        result = adapter_doctor()
        assert "ananke.adapters.deepeval" in result

    def test_each_value_has_available_key(self):
        result = adapter_doctor()
        for adapter_id, info in result.items():
            assert "available" in info, f"{adapter_id} missing 'available'"

    def test_each_value_is_dict(self):
        result = adapter_doctor()
        for adapter_id, info in result.items():
            assert isinstance(info, dict), f"{adapter_id} value is not a dict"

    def test_mlflow_unavailable_when_not_installed(self):
        """When mlflow is not installed, available should be False."""
        try:
            import mlflow  # noqa: F401

            _mlflow_available = True
        except ImportError:
            _mlflow_available = False

        result = adapter_doctor()
        mlflow_info = result["ananke.adapters.mlflow"]
        assert mlflow_info["available"] == _mlflow_available

    def test_mlflow_status_string_present(self):
        result = adapter_doctor()
        mlflow_info = result["ananke.adapters.mlflow"]
        assert "status" in mlflow_info
        assert isinstance(mlflow_info["status"], str)


# ---------------------------------------------------------------------------
# list_native_evaluators
# ---------------------------------------------------------------------------


class TestListNativeEvaluators:
    def test_returns_list(self):
        result = list_native_evaluators()
        assert isinstance(result, list)

    def test_all_entries_are_dicts(self):
        result = list_native_evaluators()
        for entry in result:
            assert isinstance(entry, dict)

    def test_entries_have_id_key(self):
        result = list_native_evaluators()
        for entry in result:
            assert "id" in entry

    def test_entries_have_dimension_key(self):
        result = list_native_evaluators()
        for entry in result:
            assert "dimension" in entry

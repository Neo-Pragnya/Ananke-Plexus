"""Tests for all Pydantic models in the eval harness."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ananke.plexus.evals.models.baseline import Baseline, BaselineApproval
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.dataset import DataClassification, EvalDataset
from ananke.plexus.evals.models.report import (
    BaselineComparison,
    EvalGateDecision,
    EvalReport,
    GateVerdict,
)
from ananke.plexus.evals.models.rubric import Rubric, RubricCriterion
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.suite import EvalSuite, EvaluatorSpec, GatePolicy, GatePolicyRule
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(kind: SpanKind = SpanKind.TOOL, name: str = "my_tool") -> AgentSpan:
    return AgentSpan(
        span_id="s1",
        kind=kind,
        name=name,
        started_at=datetime.now(tz=UTC),
    )


def _score(
    status: EvalStatus = EvalStatus.PASS,
    dimension: str = "outcome",
    metric: str = "exact_match",
    normalized: float | None = None,
) -> EvalScore:
    return EvalScore(
        evaluator_id="test",
        dimension=dimension,
        metric=metric,
        status=status,
        normalized_score=normalized,
    )


# ---------------------------------------------------------------------------
# SpanKind / EvalStatus enum smoke tests
# ---------------------------------------------------------------------------


class TestSpanKind:
    def test_values_are_strings(self):
        for kind in SpanKind:
            assert isinstance(kind.value, str)

    def test_expected_members_exist(self):
        for name in ("AGENT", "MODEL", "TOOL", "RETRIEVAL", "SUBAGENT", "APPROVAL"):
            assert hasattr(SpanKind, name)

    def test_tool_value(self):
        assert SpanKind.TOOL == "tool"


class TestEvalStatus:
    def test_values_are_strings(self):
        for status in EvalStatus:
            assert isinstance(status.value, str)

    def test_expected_members_exist(self):
        for name in ("PASS", "WARN", "REVIEW", "FAIL", "ERROR", "SKIPPED"):
            assert hasattr(EvalStatus, name)

    def test_pass_value(self):
        assert EvalStatus.PASS == "pass"


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class TestUsage:
    def test_defaults(self):
        u = Usage()
        assert u.tool_calls == 0
        assert u.retries == 0
        assert u.total_tokens is None

    def test_with_values(self):
        u = Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            duration_ms=500,
        )
        assert u.total_tokens == 150
        assert u.cost_usd == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# AgentSpan
# ---------------------------------------------------------------------------


class TestAgentSpan:
    def test_basic_creation(self):
        now = datetime.now(tz=UTC)
        span = AgentSpan(span_id="x", kind=SpanKind.TOOL, name="tool_a", started_at=now)
        assert span.span_id == "x"
        assert span.kind == SpanKind.TOOL
        assert span.ended_at is None

    def test_duration_ms_none_when_no_end(self):
        span = _span()
        assert span.duration_ms is None

    def test_duration_ms_computed(self):
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        span = AgentSpan(
            span_id="s",
            kind=SpanKind.MODEL,
            name="llm",
            started_at=now,
            ended_at=now + timedelta(milliseconds=250),
        )
        assert span.duration_ms == 250

    def test_attributes_default_empty(self):
        span = _span()
        assert span.attributes == {}

    def test_events_default_empty(self):
        span = _span()
        assert span.events == []


# ---------------------------------------------------------------------------
# AgentTrace
# ---------------------------------------------------------------------------


class TestAgentTrace:
    def test_basic_creation(self):
        trace = AgentTrace(trace_id="t1", run_id="r1", runtime="test", spans=[], usage=Usage())
        assert trace.trace_id == "t1"
        assert trace.runtime == "test"

    def test_spans_by_kind_filters_correctly(self):
        tool_span = _span(SpanKind.TOOL, "tool_a")
        model_span = _span(SpanKind.MODEL, "gpt-4")
        trace = AgentTrace(trace_id="t", run_id="r", runtime="x", spans=[tool_span, model_span])
        assert trace.spans_by_kind(SpanKind.TOOL) == [tool_span]
        assert trace.spans_by_kind(SpanKind.MODEL) == [model_span]

    def test_spans_by_kind_empty_result(self):
        trace = AgentTrace(trace_id="t", run_id="r", runtime="x", spans=[_span(SpanKind.TOOL)])
        assert trace.spans_by_kind(SpanKind.RETRIEVAL) == []

    def test_tool_names_returns_tool_span_names(self):
        spans = [
            _span(SpanKind.TOOL, "search"),
            _span(SpanKind.TOOL, "read_file"),
            _span(SpanKind.MODEL, "llm"),
        ]
        trace = AgentTrace(trace_id="t", run_id="r", runtime="x", spans=spans)
        assert trace.tool_names() == ["search", "read_file"]

    def test_tool_names_empty_when_no_tools(self):
        trace = AgentTrace(trace_id="t", run_id="r", runtime="x", spans=[_span(SpanKind.MODEL)])
        assert trace.tool_names() == []

    def test_defaults(self):
        trace = AgentTrace(trace_id="t", run_id="r", runtime="x")
        assert trace.spans == []
        assert trace.final_output is None
        assert trace.model is None


# ---------------------------------------------------------------------------
# EvalScore
# ---------------------------------------------------------------------------


class TestEvalScore:
    def test_basic_creation(self):
        s = _score()
        assert s.status == EvalStatus.PASS
        assert s.passed is True

    def test_fail_not_passed(self):
        s = _score(EvalStatus.FAIL)
        assert s.passed is False

    def test_warn_is_passed(self):
        s = _score(EvalStatus.WARN)
        assert s.passed is True

    def test_review_not_passed(self):
        s = _score(EvalStatus.REVIEW)
        assert s.passed is False

    def test_normalized_score_bounds_valid(self):
        s = _score(normalized=0.75)
        assert s.normalized_score == pytest.approx(0.75)

    def test_normalized_score_zero(self):
        s = _score(normalized=0.0)
        assert s.normalized_score == 0.0

    def test_normalized_score_one(self):
        s = _score(normalized=1.0)
        assert s.normalized_score == 1.0

    def test_normalized_score_above_one_raises(self):
        with pytest.raises(Exception):
            EvalScore(
                evaluator_id="x",
                dimension="d",
                metric="m",
                status=EvalStatus.PASS,
                normalized_score=1.5,
            )

    def test_normalized_score_below_zero_raises(self):
        with pytest.raises(Exception):
            EvalScore(
                evaluator_id="x",
                dimension="d",
                metric="m",
                status=EvalStatus.PASS,
                normalized_score=-0.1,
            )

    def test_evidence_default_empty(self):
        s = _score()
        assert s.evidence == []

    def test_deterministic_default_true(self):
        s = _score()
        assert s.deterministic is True


# ---------------------------------------------------------------------------
# EvalCase
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_basic(self):
        c = EvalCase(id="c1", input="hello")
        assert c.id == "c1"
        assert c.input == "hello"
        assert c.expected is None

    def test_has_tag(self):
        c = EvalCase(id="c1", input="x", tags=["regression", "smoke"])
        assert c.has_tag("regression") is True
        assert c.has_tag("missing") is False

    def test_metadata_default_empty(self):
        c = EvalCase(id="c", input="x")
        assert c.metadata == {}


# ---------------------------------------------------------------------------
# EvalSuite
# ---------------------------------------------------------------------------


class TestEvalSuite:
    def test_basic(self):
        suite = EvalSuite(id="my-suite")
        assert suite.id == "my-suite"
        assert suite.version == 1
        assert suite.evaluators == []

    def test_with_evaluator_spec(self):
        spec = EvaluatorSpec(id="ananke.outcome.exact_match")
        suite = EvalSuite(id="s", evaluators=[spec])
        assert len(suite.evaluators) == 1

    def test_policy_rule_for_existing(self):
        rule = GatePolicyRule(minimum=0.8, on_failure="block")
        policy = GatePolicy(rules={"outcome": rule})
        suite = EvalSuite(id="s", policy=policy)
        found = suite.policy.rule_for("outcome")
        assert found is not None
        assert found.minimum == 0.8

    def test_policy_rule_for_missing_returns_none(self):
        policy = GatePolicy()
        assert policy.rule_for("nonexistent") is None


class TestGatePolicyRule:
    def test_defaults(self):
        r = GatePolicyRule()
        assert r.on_failure == "block"
        assert r.allow_regression is True
        assert r.required is False

    def test_on_failure_values(self):
        for val in ("block", "warn", "review"):
            r = GatePolicyRule(on_failure=val)
            assert r.on_failure == val


# ---------------------------------------------------------------------------
# EvalDataset
# ---------------------------------------------------------------------------


class TestEvalDataset:
    def test_basic(self):
        ds = EvalDataset(id="ds1")
        assert ds.id == "ds1"
        assert ds.version == 1
        assert ds.cases == []

    def test_case_by_id_found(self):
        case = EvalCase(id="c1", input="x")
        ds = EvalDataset(id="ds", cases=[case])
        assert ds.case_by_id("c1") is case

    def test_case_by_id_not_found(self):
        ds = EvalDataset(id="ds", cases=[EvalCase(id="c1", input="x")])
        assert ds.case_by_id("missing") is None

    def test_provenance_defaults(self):
        ds = EvalDataset(id="ds")
        assert ds.provenance.sensitivity == DataClassification.INTERNAL

    def test_data_classification_values(self):
        for val in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "SECRET"):
            assert hasattr(DataClassification, val)


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------


class TestEvalReport:
    def test_basic(self):
        report = EvalReport(run_id="r1", suite_id="s1")
        assert report.run_id == "r1"
        assert report.suite_id == "s1"
        assert report.scores == []

    def test_pass_rate_empty(self):
        report = EvalReport(run_id="r", suite_id="s")
        assert report.pass_rate() == 0.0

    def test_pass_rate_all_pass(self):
        scores = [_score(EvalStatus.PASS) for _ in range(3)]
        report = EvalReport(run_id="r", suite_id="s", scores=scores)
        assert report.pass_rate() == pytest.approx(1.0)

    def test_pass_rate_mixed(self):
        scores = [_score(EvalStatus.PASS), _score(EvalStatus.FAIL), _score(EvalStatus.PASS)]
        report = EvalReport(run_id="r", suite_id="s", scores=scores)
        assert report.pass_rate() == pytest.approx(2 / 3)

    def test_scores_by_dimension(self):
        s1 = _score(dimension="outcome")
        s2 = _score(dimension="safety")
        report = EvalReport(run_id="r", suite_id="s", scores=[s1, s2])
        assert report.scores_by_dimension("outcome") == [s1]
        assert report.scores_by_dimension("trajectory") == []


# ---------------------------------------------------------------------------
# EvalGateDecision
# ---------------------------------------------------------------------------


class TestEvalGateDecision:
    def test_blocks_when_verdict_block(self):
        gd = EvalGateDecision(verdict=GateVerdict.BLOCK, suite_id="s", run_id="r")
        assert gd.blocks is True

    def test_not_blocks_when_pass(self):
        gd = EvalGateDecision(verdict=GateVerdict.PASS, suite_id="s", run_id="r")
        assert gd.blocks is False

    def test_not_blocks_when_warn(self):
        gd = EvalGateDecision(verdict=GateVerdict.WARN, suite_id="s", run_id="r")
        assert gd.blocks is False

    def test_blocking_dimensions_default_empty(self):
        gd = EvalGateDecision(verdict=GateVerdict.PASS, suite_id="s", run_id="r")
        assert gd.blocking_dimensions == []


# ---------------------------------------------------------------------------
# BaselineComparison
# ---------------------------------------------------------------------------


class TestBaselineComparison:
    def test_basic(self):
        bc = BaselineComparison(baseline_id="b1")
        assert bc.baseline_id == "b1"
        assert bc.regressions == {}
        assert bc.improvements == {}
        assert bc.stable == []
        assert bc.regression_blocked is False

    def test_with_regression(self):
        bc = BaselineComparison(
            baseline_id="b1",
            regressions={"outcome": -0.05},
            improvements={"safety": 0.02},
        )
        assert "outcome" in bc.regressions
        assert "safety" in bc.improvements


# ---------------------------------------------------------------------------
# Baseline / BaselineApproval
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_not_approved_by_default(self):
        b = Baseline(baseline_id="b1", suite_id="s1", version="1")
        assert b.approved is False

    def test_approved_when_approval_set(self):
        approval = BaselineApproval(
            approved_by="user@example.com",
            approved_at=datetime.now(tz=UTC),
        )
        b = Baseline(baseline_id="b1", suite_id="s1", version="1", approval=approval)
        assert b.approved is True

    def test_scores_default_empty(self):
        b = Baseline(baseline_id="b1", suite_id="s1", version="1")
        assert b.scores == {}


# ---------------------------------------------------------------------------
# Rubric
# ---------------------------------------------------------------------------


class TestRubric:
    def test_basic(self):
        r = Rubric(rubric_id="r1")
        assert r.rubric_id == "r1"
        assert r.pass_threshold == pytest.approx(0.7)
        assert r.criteria == []

    def test_criterion_weight_default(self):
        c = RubricCriterion(id="c1", description="test criterion")
        assert c.weight == 1.0
        assert c.required is False

    def test_criterion_scale(self):
        c = RubricCriterion(id="c1", description="d", scale_min=0.0, scale_max=5.0)
        assert c.scale_max == 5.0

    def test_rubric_with_criteria(self):
        c = RubricCriterion(id="c1", description="is correct")
        r = Rubric(rubric_id="r1", criteria=[c])
        assert len(r.criteria) == 1

    def test_gate_verdict_values(self):
        for v in ("PASS", "WARN", "REVIEW", "BLOCK"):
            assert hasattr(GateVerdict, v)

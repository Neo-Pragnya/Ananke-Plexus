"""Tests for trajectory evaluators in the eval harness."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.trajectory.matching import (
    ForbiddenStepEvaluator,
    GraphTrajectoryEvaluator,
    LoopDetectionEvaluator,
    OrderedSubsetTrajectoryEvaluator,
    RequiredStepEvaluator,
    StepEfficiencyEvaluator,
    StrictTrajectoryEvaluator,
    SupersetTrajectoryEvaluator,
    UnorderedSubsetTrajectoryEvaluator,
)
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalStatus
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_span(name: str, idx: int = 0) -> AgentSpan:
    return AgentSpan(
        span_id=f"s{idx}",
        kind=SpanKind.TOOL,
        name=name,
        started_at=datetime.now(tz=UTC),
    )


def make_trace_with_tools(*tool_names: str) -> AgentTrace:
    spans = [_tool_span(name, i) for i, name in enumerate(tool_names)]
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=spans,
        usage=Usage(),
    )


def make_case() -> EvalCase:
    return EvalCase(id="c1", input="test")


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


# ---------------------------------------------------------------------------
# StrictTrajectoryEvaluator
# ---------------------------------------------------------------------------


class TestStrictTrajectoryEvaluator:
    def test_pass_when_tools_match(self, ctx):
        ev = StrictTrajectoryEvaluator(["search", "read", "write"])
        trace = make_trace_with_tools("search", "read", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.PASS

    def test_fail_when_tools_mismatch(self, ctx):
        ev = StrictTrajectoryEvaluator(["search"])
        trace = make_trace_with_tools("other")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.FAIL

    def test_pass_empty_trajectory(self, ctx):
        ev = StrictTrajectoryEvaluator([])
        trace = make_trace_with_tools()
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.PASS

    def test_id(self):
        ev = StrictTrajectoryEvaluator([])
        assert ev.id == "ananke.trajectory.strict"

    def test_dimension(self):
        ev = StrictTrajectoryEvaluator([])
        assert ev.dimension == "trajectory"

    def test_deterministic(self):
        ev = StrictTrajectoryEvaluator([])
        assert ev.deterministic is True


# ---------------------------------------------------------------------------
# OrderedSubsetTrajectoryEvaluator
# ---------------------------------------------------------------------------


class TestOrderedSubsetTrajectoryEvaluator:
    def test_pass_ordered_subset_present(self, ctx):
        ev = OrderedSubsetTrajectoryEvaluator(["search", "write"])
        trace = make_trace_with_tools("search", "read", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_wrong_order(self, ctx):
        ev = OrderedSubsetTrajectoryEvaluator(["write", "search"])
        trace = make_trace_with_tools("search", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_fail_missing_step(self, ctx):
        ev = OrderedSubsetTrajectoryEvaluator(["search", "deploy"])
        trace = make_trace_with_tools("search", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL


# ---------------------------------------------------------------------------
# UnorderedSubsetTrajectoryEvaluator
# ---------------------------------------------------------------------------


class TestUnorderedSubsetTrajectoryEvaluator:
    def test_pass_all_required_present(self, ctx):
        ev = UnorderedSubsetTrajectoryEvaluator(["a", "b"])
        trace = make_trace_with_tools("b", "a", "c")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_missing_required(self, ctx):
        ev = UnorderedSubsetTrajectoryEvaluator(["a", "b", "d"])
        trace = make_trace_with_tools("a", "b")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL


# ---------------------------------------------------------------------------
# SupersetTrajectoryEvaluator
# ---------------------------------------------------------------------------


class TestSupersetTrajectoryEvaluator:
    def test_pass_only_allowed_tools(self, ctx):
        ev = SupersetTrajectoryEvaluator(["search", "read", "write"])
        trace = make_trace_with_tools("search", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_unexpected_tool(self, ctx):
        ev = SupersetTrajectoryEvaluator(["search"])
        trace = make_trace_with_tools("search", "delete_all")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL


# ---------------------------------------------------------------------------
# ForbiddenStepEvaluator
# ---------------------------------------------------------------------------


class TestForbiddenStepEvaluator:
    def test_pass_no_violations(self, ctx):
        ev = ForbiddenStepEvaluator(["rm_rf", "format_disk"])
        trace = make_trace_with_tools("search", "read")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.PASS

    def test_fail_with_violations(self, ctx):
        ev = ForbiddenStepEvaluator(["rm_rf"])
        trace = make_trace_with_tools("search", "rm_rf", "read")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.FAIL

    def test_id(self):
        assert ForbiddenStepEvaluator([]).id == "ananke.trajectory.forbidden_step"

    def test_dimension(self):
        assert ForbiddenStepEvaluator([]).dimension == "trajectory"


# ---------------------------------------------------------------------------
# RequiredStepEvaluator
# ---------------------------------------------------------------------------


class TestRequiredStepEvaluator:
    def test_pass_required_present(self, ctx):
        ev = RequiredStepEvaluator("verify")
        trace = make_trace_with_tools("search", "verify", "respond")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_required_absent(self, ctx):
        ev = RequiredStepEvaluator("verify")
        trace = make_trace_with_tools("search", "respond")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_fail_reason_mentions_tool(self, ctx):
        ev = RequiredStepEvaluator("checkpoint")
        trace = make_trace_with_tools("other")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].reason and "checkpoint" in scores[0].reason

    def test_id(self):
        assert RequiredStepEvaluator("x").id == "ananke.trajectory.required_step"


# ---------------------------------------------------------------------------
# LoopDetectionEvaluator
# ---------------------------------------------------------------------------


class TestLoopDetectionEvaluator:
    def test_pass_no_loop(self, ctx):
        ev = LoopDetectionEvaluator(max_consecutive=3)
        trace = make_trace_with_tools("a", "b", "a", "c")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_loop_detected(self, ctx):
        ev = LoopDetectionEvaluator(max_consecutive=2)
        trace = make_trace_with_tools("a", "a", "a")  # 3 consecutive
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_pass_empty_trace(self, ctx):
        ev = LoopDetectionEvaluator()
        trace = make_trace_with_tools()
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_at_threshold(self, ctx):
        ev = LoopDetectionEvaluator(max_consecutive=3)
        trace = make_trace_with_tools("a", "a", "a")  # exactly 3
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_value_contains_run_length(self, ctx):
        ev = LoopDetectionEvaluator(max_consecutive=2)
        trace = make_trace_with_tools("x", "x", "x", "x")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].value == 4

    def test_id(self):
        assert LoopDetectionEvaluator().id == "ananke.trajectory.loop_detection"


# ---------------------------------------------------------------------------
# StepEfficiencyEvaluator
# ---------------------------------------------------------------------------


class TestStepEfficiencyEvaluator:
    def test_pass_optimal(self, ctx):
        ev = StepEfficiencyEvaluator(optimal_steps=3)
        trace = make_trace_with_tools("a", "b", "c")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_fewer_than_optimal(self, ctx):
        ev = StepEfficiencyEvaluator(optimal_steps=5)
        trace = make_trace_with_tools("a", "b")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_too_many_steps(self, ctx):
        ev = StepEfficiencyEvaluator(optimal_steps=2)
        trace = make_trace_with_tools("a", "b", "c", "d", "e", "f")  # ratio = 2/6 = 0.33 < 0.5
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_steps(self, ctx):
        ev = StepEfficiencyEvaluator(optimal_steps=3)
        trace = make_trace_with_tools()
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# GraphTrajectoryEvaluator
# ---------------------------------------------------------------------------


class TestGraphTrajectoryEvaluator:
    def test_pass_valid_transitions(self, ctx):
        """All transitions in the graph → no violations → PASS."""
        ev = GraphTrajectoryEvaluator({"search": ["read"], "read": ["write"]})
        trace = make_trace_with_tools("search", "read", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS
        assert scores[0].value == []

    def test_fail_invalid_transitions(self, ctx):
        """Transition not in graph → violations → FAIL."""
        ev = GraphTrajectoryEvaluator({"search": ["read"]})
        trace = make_trace_with_tools("search", "write")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL
        assert "search -> write" in scores[0].value

    def test_pass_single_step_no_transitions(self, ctx):
        """Single step: no transitions to check → violations=[] → PASS."""
        ev = GraphTrajectoryEvaluator({})
        trace = make_trace_with_tools("only_one")
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS
        assert scores[0].value == []

    def test_id(self):
        assert GraphTrajectoryEvaluator({}).id == "ananke.trajectory.graph"

    def test_dimension(self):
        assert GraphTrajectoryEvaluator({}).dimension == "trajectory"

"""Tests for efficiency evaluators in the eval harness.

Note on source bugs:
  TokenBudgetEvaluator when over budget computes:
    normalized = min(1.0, (max - total) / max)
  which is negative when total > max. Pydantic rejects normalized_score < 0.
  The over-budget tests document this bug with pytest.raises(ValidationError).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.efficiency.budget import (
    CacheEfficiencyEvaluator,
    CostBudgetEvaluator,
    LatencyEvaluator,
    ModelCallCountEvaluator,
    RetryCountEvaluator,
    TokenBudgetEvaluator,
    ToolCallCountEvaluator,
    WallClockEvaluator,
)
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalStatus
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_case() -> EvalCase:
    return EvalCase(id="c1", input="test")


def make_trace(usage: Usage | None = None, spans=None) -> AgentTrace:
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=spans or [],
        usage=usage or Usage(),
    )


def _tool_span(name: str = "tool", idx: int = 0) -> AgentSpan:
    return AgentSpan(
        span_id=f"s{idx}",
        kind=SpanKind.TOOL,
        name=name,
        started_at=datetime.now(tz=UTC),
    )


def _model_span(name: str = "model", cache_hit: bool = False, idx: int = 0) -> AgentSpan:
    return AgentSpan(
        span_id=f"m{idx}",
        kind=SpanKind.MODEL,
        name=name,
        started_at=datetime.now(tz=UTC),
        attributes={"cache.hit": cache_hit},
    )


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


# ---------------------------------------------------------------------------
# TokenBudgetEvaluator
# ---------------------------------------------------------------------------


class TestTokenBudgetEvaluator:
    def test_pass_under_budget(self, ctx):
        ev = TokenBudgetEvaluator(max_tokens=1000)
        trace = make_trace(Usage(total_tokens=500))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_at_budget(self, ctx):
        ev = TokenBudgetEvaluator(max_tokens=1000)
        trace = make_trace(Usage(total_tokens=1000))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_normalized_score_clamped_over_budget(self, ctx):
        """Over-budget normalized_score is clamped to 0.0 (not negative)."""
        ev = TokenBudgetEvaluator(max_tokens=1000)
        trace = make_trace(Usage(total_tokens=1500))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL
        assert scores[0].normalized_score is not None
        assert scores[0].normalized_score >= 0.0

    def test_skipped_no_token_data(self, ctx):
        ev = TokenBudgetEvaluator(max_tokens=1000)
        trace = make_trace(Usage(total_tokens=None))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_fail_reason_included_over_budget(self, ctx):
        """Over-budget produces a fail status with a reason string."""
        ev = TokenBudgetEvaluator(max_tokens=100)
        trace = make_trace(Usage(total_tokens=200))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL
        assert scores[0].reason is not None
        assert "200" in scores[0].reason

    def test_id(self):
        assert TokenBudgetEvaluator().id == "ananke.efficiency.token_budget"

    def test_value_recorded(self, ctx):
        ev = TokenBudgetEvaluator(max_tokens=1000)
        trace = make_trace(Usage(total_tokens=300))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].value == 300


# ---------------------------------------------------------------------------
# CostBudgetEvaluator
# ---------------------------------------------------------------------------


class TestCostBudgetEvaluator:
    def test_pass_under_budget(self, ctx):
        ev = CostBudgetEvaluator(max_cost_usd=1.0)
        trace = make_trace(Usage(cost_usd=0.50))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_over_budget(self, ctx):
        ev = CostBudgetEvaluator(max_cost_usd=1.0)
        trace = make_trace(Usage(cost_usd=2.0))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_cost(self, ctx):
        ev = CostBudgetEvaluator()
        trace = make_trace(Usage(cost_usd=None))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# LatencyEvaluator
# ---------------------------------------------------------------------------


class TestLatencyEvaluator:
    def test_pass_fast(self, ctx):
        ev = LatencyEvaluator(max_duration_ms=5000)
        trace = make_trace(Usage(duration_ms=1000))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_slow(self, ctx):
        ev = LatencyEvaluator(max_duration_ms=1000)
        trace = make_trace(Usage(duration_ms=5000))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_duration(self, ctx):
        ev = LatencyEvaluator()
        trace = make_trace(Usage(duration_ms=None))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_id(self):
        assert LatencyEvaluator().id == "ananke.efficiency.latency"


# ---------------------------------------------------------------------------
# ToolCallCountEvaluator
# ---------------------------------------------------------------------------


class TestToolCallCountEvaluator:
    def test_pass_few_tools(self, ctx):
        ev = ToolCallCountEvaluator(max_calls=10)
        spans = [_tool_span(idx=i) for i in range(3)]
        trace = make_trace(spans=spans)
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_many_tools(self, ctx):
        ev = ToolCallCountEvaluator(max_calls=2)
        spans = [_tool_span(idx=i) for i in range(5)]
        trace = make_trace(spans=spans)
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_uses_usage_tool_calls_when_set(self, ctx):
        ev = ToolCallCountEvaluator(max_calls=10)
        trace = make_trace(Usage(tool_calls=3))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_id(self):
        assert ToolCallCountEvaluator().id == "ananke.efficiency.tool_call_count"


# ---------------------------------------------------------------------------
# ModelCallCountEvaluator
# ---------------------------------------------------------------------------


class TestModelCallCountEvaluator:
    def test_pass_few_model_calls(self, ctx):
        ev = ModelCallCountEvaluator(max_calls=5)
        spans = [_model_span(idx=i) for i in range(3)]
        trace = make_trace(spans=spans)
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_too_many_model_calls(self, ctx):
        ev = ModelCallCountEvaluator(max_calls=2)
        spans = [_model_span(idx=i) for i in range(5)]
        trace = make_trace(spans=spans)
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_id(self):
        assert ModelCallCountEvaluator().id == "ananke.efficiency.model_call_count"


# ---------------------------------------------------------------------------
# WallClockEvaluator
# ---------------------------------------------------------------------------


class TestWallClockEvaluator:
    def test_pass_fast_trace(self, ctx):
        ev = WallClockEvaluator(max_seconds=60.0)
        trace = make_trace(Usage(duration_ms=5000))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_slow_trace(self, ctx):
        ev = WallClockEvaluator(max_seconds=1.0)
        trace = make_trace(Usage(duration_ms=5000))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_duration(self, ctx):
        ev = WallClockEvaluator()
        trace = make_trace(Usage(duration_ms=None))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# RetryCountEvaluator
# ---------------------------------------------------------------------------


class TestRetryCountEvaluator:
    def test_pass_few_retries(self, ctx):
        ev = RetryCountEvaluator(max_retries=5)
        trace = make_trace(Usage(retries=2))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_too_many_retries(self, ctx):
        ev = RetryCountEvaluator(max_retries=3)
        trace = make_trace(Usage(retries=10))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_pass_zero_retries(self, ctx):
        ev = RetryCountEvaluator()
        trace = make_trace(Usage(retries=0))
        scores = ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# CacheEfficiencyEvaluator
# ---------------------------------------------------------------------------


class TestCacheEfficiencyEvaluator:
    def setup_method(self):
        self.ev = CacheEfficiencyEvaluator()

    def test_skipped_no_model_calls(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace(), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_pass_high_cache_hit_rate(self, ctx):
        spans = [
            _model_span("llm1", cache_hit=True, idx=0),
            _model_span("llm2", cache_hit=True, idx=1),
            _model_span("llm3", cache_hit=False, idx=2),
        ]
        trace = make_trace(spans=spans)
        scores = self.ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS
        assert scores[0].normalized_score == pytest.approx(2 / 3, abs=0.01)

    def test_fail_low_cache_hit_rate(self, ctx):
        spans = [
            _model_span("llm1", cache_hit=False, idx=0),
            _model_span("llm2", cache_hit=False, idx=1),
            _model_span("llm3", cache_hit=False, idx=2),
            _model_span("llm4", cache_hit=False, idx=3),
        ]
        trace = make_trace(spans=spans)
        scores = self.ev.evaluate(case=make_case(), trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL
        assert scores[0].normalized_score == pytest.approx(0.0)

    def test_id(self):
        assert self.ev.id == "ananke.efficiency.cache_efficiency"

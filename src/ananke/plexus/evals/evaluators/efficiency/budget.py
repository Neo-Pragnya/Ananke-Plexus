"""Efficiency evaluators: token budget, cost, latency, tool/model call count, wall clock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "efficiency"


class TokenBudgetEvaluator:
    id = "ananke.efficiency.token_budget"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_tokens: int = 80_000) -> None:
        self._max = max_tokens

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        total = trace.usage.total_tokens
        if total is None:
            return [skipped_score(self.id, _DIM, "token_budget", "no token usage recorded")]
        passed = total <= self._max
        normalized = 1.0 if passed else max(0.0, 1.0 - (total - self._max) / self._max)
        return [
            make_score(
                self.id,
                _DIM,
                "token_budget",
                passed,
                value=total,
                normalized=normalized,
                reason=f"{total} tokens > budget {self._max}" if not passed else None,
            )
        ]


class CostBudgetEvaluator:
    id = "ananke.efficiency.cost_budget"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_cost_usd: float = 1.0) -> None:
        self._max = max_cost_usd

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        cost = trace.usage.cost_usd
        if cost is None:
            return [skipped_score(self.id, _DIM, "cost_budget", "no cost recorded")]
        passed = cost <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "cost_budget",
                passed,
                value=cost,
                reason=f"${cost:.4f} > budget ${self._max:.4f}" if not passed else None,
            )
        ]


class LatencyEvaluator:
    id = "ananke.efficiency.latency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_duration_ms: int = 30_000) -> None:
        self._max = max_duration_ms

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        duration = trace.usage.duration_ms
        if duration is None:
            return [skipped_score(self.id, _DIM, "latency", "no duration recorded")]
        passed = duration <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "latency",
                passed,
                value=duration,
                reason=f"{duration}ms > p95 target {self._max}ms" if not passed else None,
            )
        ]


class ToolCallCountEvaluator:
    id = "ananke.efficiency.tool_call_count"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_calls: int = 50) -> None:
        self._max = max_calls

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        count = trace.usage.tool_calls or len([s for s in trace.spans if s.kind == SpanKind.TOOL])
        passed = count <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "tool_call_count",
                passed,
                value=count,
                reason=f"{count} tool calls > max {self._max}" if not passed else None,
            )
        ]


class ModelCallCountEvaluator:
    id = "ananke.efficiency.model_call_count"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_calls: int = 10) -> None:
        self._max = max_calls

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        count = len([s for s in trace.spans if s.kind == SpanKind.MODEL])
        passed = count <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "model_call_count",
                passed,
                value=count,
                reason=f"{count} model calls > max {self._max}" if not passed else None,
            )
        ]


class WallClockEvaluator:
    id = "ananke.efficiency.wall_clock"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_seconds: float = 300.0) -> None:
        self._max_ms = max_seconds * 1000

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        duration = trace.usage.duration_ms
        if duration is None:
            return [skipped_score(self.id, _DIM, "wall_clock", "no duration recorded")]
        passed = duration <= self._max_ms
        return [
            make_score(
                self.id,
                _DIM,
                "wall_clock",
                passed,
                value=duration / 1000,
                reason=f"{duration / 1000:.1f}s > max {self._max_ms / 1000:.1f}s"
                if not passed
                else None,
            )
        ]


class RetryCountEvaluator:
    id = "ananke.efficiency.retry_count"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_retries: int = 5) -> None:
        self._max = max_retries

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        retries = trace.usage.retries
        passed = retries <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "retry_count",
                passed,
                value=retries,
                reason=f"{retries} retries > max {self._max}" if not passed else None,
            )
        ]


class CacheEfficiencyEvaluator:
    id = "ananke.efficiency.cache_efficiency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        cache_hits = sum(1 for s in trace.spans if s.attributes.get("cache.hit") is True)
        total_model_calls = len([s for s in trace.spans if s.kind == SpanKind.MODEL])
        if total_model_calls == 0:
            return [skipped_score(self.id, _DIM, "cache_efficiency", "no model calls")]
        ratio = cache_hits / total_model_calls
        passed = ratio >= 0.3
        return [make_score(self.id, _DIM, "cache_efficiency", passed, normalized=round(ratio, 3))]

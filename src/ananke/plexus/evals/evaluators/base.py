"""Base evaluator Protocol and EvaluationContext."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.trace import AgentTrace

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext


@runtime_checkable
class Evaluator(Protocol):
    id: str
    version: str
    dimension: str
    deterministic: bool

    def evaluate(
        self,
        *,
        case: EvalCase,
        trace: AgentTrace,
        context: EvaluationContext,
    ) -> list[EvalScore]: ...


def skipped_score(evaluator_id: str, dimension: str, metric: str, reason: str) -> EvalScore:
    return EvalScore(
        evaluator_id=evaluator_id,
        dimension=dimension,
        metric=metric,
        status=EvalStatus.SKIPPED,
        reason=reason,
    )


def error_score(evaluator_id: str, dimension: str, metric: str, reason: str) -> EvalScore:
    return EvalScore(
        evaluator_id=evaluator_id,
        dimension=dimension,
        metric=metric,
        status=EvalStatus.ERROR,
        reason=reason,
    )


def make_score(
    evaluator_id: str,
    dimension: str,
    metric: str,
    passed: bool,
    value: Any = None,
    normalized: float | None = None,
    threshold: float | None = None,
    reason: str | None = None,
    evidence: list[str] | None = None,
    deterministic: bool = True,
) -> EvalScore:
    return EvalScore(
        evaluator_id=evaluator_id,
        dimension=dimension,
        metric=metric,
        value=value,
        normalized_score=normalized,
        status=EvalStatus.PASS if passed else EvalStatus.FAIL,
        threshold=threshold,
        reason=reason,
        evidence=evidence or [],
        deterministic=deterministic,
    )

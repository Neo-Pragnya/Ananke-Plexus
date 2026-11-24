"""Resilience evaluators: failure recognition, recovery, repeated failure, checkpoint, containment."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "resilience"


def _chronological_positions(trace: AgentTrace) -> dict[int, int]:
    """Map each span's index in ``trace.spans`` to its chronological position.

    Spans are ordered by start time, ties broken by their order in the trace. If the start
    times cannot be compared (naive and aware datetimes mixed), trace order is used.
    """
    indices = list(range(len(trace.spans)))
    with contextlib.suppress(TypeError):
        indices = sorted(indices, key=lambda i: (trace.spans[i].started_at, i))
    return {span_index: position for position, span_index in enumerate(indices)}


def _any_after_first_failure(
    trace: AgentTrace,
    is_failure: Callable[[AgentSpan], bool],
    is_response: Callable[[AgentSpan], bool],
) -> bool:
    """True when some span matching *is_response* comes after the earliest failure."""
    positions = _chronological_positions(trace)
    failure_positions = [positions[i] for i, s in enumerate(trace.spans) if is_failure(s)]
    if not failure_positions:
        return False
    first_failure = min(failure_positions)
    return any(positions[i] > first_failure for i, s in enumerate(trace.spans) if is_response(s))


class FailureRecognitionEvaluator:
    id = "ananke.resilience.failure_recognition"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        failed_spans = [
            s for s in trace.spans if s.attributes.get("error") or s.attributes.get("exception")
        ]
        if not failed_spans:
            return [
                make_score(
                    self.id, _DIM, "failure_recognition", True, reason="no errors detected in trace"
                )
            ]
        subsequent_recovery = _any_after_first_failure(
            trace,
            lambda s: bool(s.attributes.get("error") or s.attributes.get("exception")),
            lambda s: bool(s.attributes.get("recovery") or s.attributes.get("retry")),
        )
        passed = subsequent_recovery
        return [
            make_score(
                self.id,
                _DIM,
                "failure_recognition",
                passed,
                value=len(failed_spans),
                reason="failure not followed by recovery attempt" if not passed else None,
            )
        ]


class RecoveryPathEvaluator:
    id = "ananke.resilience.recovery_path"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, expected_recovery_actions: list[str]) -> None:
        self._expected = set(expected_recovery_actions)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if not self._expected:
            return [skipped_score(self.id, _DIM, "recovery_path", "no expected_recovery_actions")]
        tool_names = {s.name for s in trace.spans if s.kind == SpanKind.TOOL}
        found = self._expected & tool_names
        passed = len(found) == len(self._expected)
        return [
            make_score(
                self.id,
                _DIM,
                "recovery_path",
                passed,
                reason=f"missing recovery actions: {self._expected - found}"
                if not passed
                else None,
            )
        ]


class RepeatedFailureEvaluator:
    id = "ananke.resilience.repeated_failure"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_repeated_errors: int = 2) -> None:
        self._max = max_repeated_errors

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        from collections import Counter

        error_msgs = Counter(
            str(s.attributes.get("error", "")) for s in trace.spans if s.attributes.get("error")
        )
        repeated = {k: v for k, v in error_msgs.items() if v > self._max}
        passed = len(repeated) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "repeated_failure",
                passed,
                value=dict(repeated),
                reason=f"repeated errors: {list(repeated.keys())}" if repeated else None,
            )
        ]


class CheckpointUsageEvaluator:
    id = "ananke.resilience.checkpoint_usage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        checkpoints = [s for s in trace.spans if "checkpoint" in s.name.lower()]
        has_checkpoints = len(checkpoints) > 0
        return [
            make_score(
                self.id,
                _DIM,
                "checkpoint_usage",
                has_checkpoints,
                value=len(checkpoints),
                reason="no checkpoint spans found" if not has_checkpoints else None,
            )
        ]


class RollbackEvaluator:
    id = "ananke.resilience.rollback"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, rollback_expected: bool = False) -> None:
        self._expected = rollback_expected

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        rollback_spans = [
            s for s in trace.spans if "rollback" in s.name.lower() or "revert" in s.name.lower()
        ]
        has_rollback = len(rollback_spans) > 0
        passed = has_rollback if self._expected else True
        return [
            make_score(
                self.id,
                _DIM,
                "rollback",
                passed,
                reason="expected rollback but none found" if not passed else None,
            )
        ]


class PartialFailureContainmentEvaluator:
    id = "ananke.resilience.partial_failure_containment"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        failed = [s for s in trace.spans if s.attributes.get("error")]
        if not failed:
            return [
                make_score(
                    self.id,
                    _DIM,
                    "partial_failure_containment",
                    True,
                    reason="no failures to contain",
                )
            ]
        # Containment only counts when it happens after the failure it is meant to contain.
        passed = _any_after_first_failure(
            trace,
            lambda s: bool(s.attributes.get("error")),
            lambda s: any(
                k in s.name.lower()
                for k in ["compensate", "cleanup", "mark_partial", "containment"]
            ),
        )
        return [
            make_score(
                self.id,
                _DIM,
                "partial_failure_containment",
                passed,
                reason="failures detected but no compensation/containment spans"
                if not passed
                else None,
            )
        ]

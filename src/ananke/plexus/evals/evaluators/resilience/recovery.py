"""Resilience evaluators: failure recognition, recovery, repeated failure, checkpoint, containment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "resilience"


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
        subsequent_recovery = any(
            s.attributes.get("recovery") or s.attributes.get("retry") for s in trace.spans
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
        compensation_spans = [
            s
            for s in trace.spans
            if any(
                k in s.name.lower()
                for k in ["compensate", "cleanup", "mark_partial", "containment"]
            )
        ]
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
        passed = len(compensation_spans) > 0
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

"""Trajectory evaluators: strict, subset, superset, forbidden/required, loop detection, efficiency."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "trajectory"


def _tool_sequence(trace: AgentTrace) -> list[str]:
    return [s.name for s in trace.spans if s.kind == SpanKind.TOOL]


class StrictTrajectoryEvaluator:
    id = "ananke.trajectory.strict"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, expected: list[str]) -> None:
        self._expected = expected

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = _tool_sequence(trace)
        passed = actual == self._expected
        return [
            make_score(
                self.id,
                _DIM,
                "strict_trajectory",
                passed,
                value=actual,
                reason=f"expected={self._expected}" if not passed else None,
                evidence=[f"actual={actual}"],
            )
        ]


class OrderedSubsetTrajectoryEvaluator:
    id = "ananke.trajectory.ordered_subset"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, required: list[str]) -> None:
        self._required = required

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = _tool_sequence(trace)
        it = iter(actual)
        found = all(step in it for step in self._required)
        return [
            make_score(
                self.id,
                _DIM,
                "ordered_subset_trajectory",
                found,
                reason=None if found else f"required ordered={self._required} not in {actual}",
            )
        ]


class UnorderedSubsetTrajectoryEvaluator:
    id = "ananke.trajectory.unordered_subset"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, required: list[str]) -> None:
        self._required = set(required)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = set(_tool_sequence(trace))
        missing = self._required - actual
        passed = len(missing) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "unordered_subset_trajectory",
                passed,
                reason=f"missing actions: {missing}" if missing else None,
            )
        ]


class SupersetTrajectoryEvaluator:
    id = "ananke.trajectory.superset"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = set(_tool_sequence(trace))
        unexpected = actual - self._allowed
        passed = len(unexpected) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "superset_trajectory",
                passed,
                reason=f"unexpected actions: {unexpected}" if unexpected else None,
            )
        ]


class ForbiddenStepEvaluator:
    id = "ananke.trajectory.forbidden_step"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, forbidden: list[str]) -> None:
        self._forbidden = set(forbidden)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = set(_tool_sequence(trace))
        violations = actual & self._forbidden
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "forbidden_step",
                passed,
                value=list(violations),
                reason=f"forbidden actions used: {violations}" if violations else None,
            )
        ]


class RequiredStepEvaluator:
    id = "ananke.trajectory.required_step"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, required: str) -> None:
        self._required = required

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = _tool_sequence(trace)
        passed = self._required in actual
        return [
            make_score(
                self.id,
                _DIM,
                "required_step",
                passed,
                reason=f"required action '{self._required}' not found" if not passed else None,
            )
        ]


class LoopDetectionEvaluator:
    id = "ananke.trajectory.loop_detection"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_consecutive: int = 3) -> None:
        self._max = max_consecutive

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = _tool_sequence(trace)
        if not actual:
            return [make_score(self.id, _DIM, "loop_detection", True)]

        max_run = 1
        current_run = 1
        for i in range(1, len(actual)):
            if actual[i] == actual[i - 1]:
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 1

        passed = max_run <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "loop_detection",
                passed,
                value=max_run,
                reason=f"max consecutive={max_run} > threshold={self._max}" if not passed else None,
            )
        ]


class StepEfficiencyEvaluator:
    id = "ananke.trajectory.step_efficiency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, optimal_steps: int) -> None:
        self._optimal = max(1, optimal_steps)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual_steps = len(_tool_sequence(trace))
        if actual_steps == 0:
            return [skipped_score(self.id, _DIM, "step_efficiency", "no tool steps")]
        ratio = min(1.0, self._optimal / actual_steps)
        passed = ratio >= 0.5
        return [
            make_score(
                self.id,
                _DIM,
                "step_efficiency",
                passed,
                normalized=round(ratio, 3),
                reason=f"actual={actual_steps}, optimal={self._optimal}",
            )
        ]


class GraphTrajectoryEvaluator:
    """Validates transitions against an allowed-transitions graph."""

    id = "ananke.trajectory.graph"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, allowed_transitions: dict[str, list[str]]) -> None:
        self._graph = allowed_transitions

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = _tool_sequence(trace)
        violations = []
        for i in range(len(actual) - 1):
            src, dst = actual[i], actual[i + 1]
            allowed = self._graph.get(src, [])
            if dst not in allowed and "*" not in allowed:
                violations.append(f"{src} -> {dst}")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "graph_trajectory",
                passed,
                value=violations,
                reason=f"invalid transitions: {violations}" if violations else None,
            )
        ]

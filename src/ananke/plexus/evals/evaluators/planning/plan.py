"""Planning evaluators: coverage, dependency, feasibility, risk, adherence, revision quality."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "planning"


def _planning_span(trace: AgentTrace) -> AgentSpan | None:
    spans = [s for s in trace.spans if s.kind == SpanKind.PLANNING]
    return spans[0] if spans else None


class PlanCoverageEvaluator:
    id = "ananke.planning.plan_coverage"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        span = _planning_span(trace)
        if span is None:
            return [skipped_score(self.id, _DIM, "plan_coverage", "no planning span found")]
        required = case.metadata.get("required_steps", [])
        if not required:
            return [
                skipped_score(self.id, _DIM, "plan_coverage", "no required_steps in case metadata")
            ]
        plan_text = str(span.output or span.input or "").lower()
        covered = [r for r in required if r.lower() in plan_text]
        ratio = len(covered) / len(required)
        passed = ratio >= 0.8
        return [
            make_score(
                self.id,
                _DIM,
                "plan_coverage",
                passed,
                normalized=round(ratio, 3),
                reason=f"{len(covered)}/{len(required)} steps covered",
                evidence=[f"covered={covered}"],
            )
        ]


class PlanDependencyEvaluator:
    id = "ananke.planning.plan_dependency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, dependency_pairs: list[tuple[str, str]]) -> None:
        self._pairs = dependency_pairs

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        span = _planning_span(trace)
        if span is None:
            return [skipped_score(self.id, _DIM, "plan_dependency", "no planning span")]
        plan_text = str(span.output or "").lower()
        violations = []
        for before, after in self._pairs:
            if after.lower() in plan_text and before.lower() not in plan_text:
                violations.append(f"'{after}' requires '{before}'")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "plan_dependency",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class PlanFeasibilityEvaluator:
    id = "ananke.planning.plan_feasibility"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_steps: int = 20) -> None:
        self._max = max_steps

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        span = _planning_span(trace)
        if span is None:
            return [skipped_score(self.id, _DIM, "plan_feasibility", "no planning span")]
        plan_output = span.output
        steps = plan_output if isinstance(plan_output, list) else []
        step_count = len(steps)
        passed = step_count <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "plan_feasibility",
                passed,
                value=step_count,
                reason=f"plan has {step_count} steps (max {self._max})" if not passed else None,
            )
        ]


class PlanRiskEvaluator:
    id = "ananke.planning.plan_risk"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, risky_keywords: list[str] | None = None) -> None:
        self._risky = set(risky_keywords or ["delete", "drop", "truncate", "rm -rf", "force push"])

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        span = _planning_span(trace)
        if span is None:
            return [skipped_score(self.id, _DIM, "plan_risk", "no planning span")]
        plan_text = str(span.output or "").lower()
        found_risks = [k for k in self._risky if k in plan_text]
        passed = len(found_risks) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "plan_risk",
                passed,
                value=found_risks,
                reason=f"risky operations in plan: {found_risks}" if found_risks else None,
            )
        ]


class PlanAdherenceEvaluator:
    """Checks that executed tool steps match what was planned."""

    id = "ananke.planning.plan_adherence"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        span = _planning_span(trace)
        if span is None:
            return [skipped_score(self.id, _DIM, "plan_adherence", "no planning span")]
        planned = span.output if isinstance(span.output, list) else []
        if not planned:
            return [
                skipped_score(self.id, _DIM, "plan_adherence", "planning span has no step list")
            ]
        executed = [s.name for s in trace.spans if s.kind == SpanKind.TOOL]
        planned_set = set(planned)
        executed_set = set(executed)
        adherence = len(planned_set & executed_set) / len(planned_set) if planned_set else 1.0
        passed = adherence >= 0.7
        return [
            make_score(
                self.id,
                _DIM,
                "plan_adherence",
                passed,
                normalized=round(adherence, 3),
                reason=f"adherence={adherence:.1%}",
            )
        ]


class PlanRevisionQualityEvaluator:
    """Checks that plan revisions (re-planning spans) are fewer than threshold."""

    id = "ananke.planning.plan_revision_quality"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_revisions: int = 2) -> None:
        self._max = max_revisions

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        planning_spans = [s for s in trace.spans if s.kind == SpanKind.PLANNING]
        revisions = max(0, len(planning_spans) - 1)
        passed = revisions <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "plan_revision_quality",
                passed,
                value=revisions,
                reason=f"{revisions} revisions > max {self._max}" if not passed else None,
            )
        ]

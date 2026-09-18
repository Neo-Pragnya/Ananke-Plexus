"""Multi-agent evaluators: delegation accuracy, role boundaries, handoff completeness, cyclic delegation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "multi_agent"


def _subagent_spans(trace: AgentTrace) -> list[AgentSpan]:
    return [s for s in trace.spans if s.kind == SpanKind.SUBAGENT]


class DelegationAccuracyEvaluator:
    id = "ananke.multi_agent.delegation_accuracy"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, expected_delegations: dict[str, str]) -> None:
        self._expected = expected_delegations

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        spans = _subagent_spans(trace)
        if not spans and not self._expected:
            return [make_score(self.id, _DIM, "delegation_accuracy", True)]
        violations = []
        for span in spans:
            task = span.attributes.get("task", span.name)
            expected_agent = self._expected.get(task)
            actual_agent = span.attributes.get("agent_id", span.name)
            if expected_agent and actual_agent != expected_agent:
                violations.append(f"task '{task}': expected {expected_agent}, got {actual_agent}")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "delegation_accuracy",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class RoleBoundaryEvaluator:
    id = "ananke.multi_agent.role_boundary"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, role_tool_map: dict[str, list[str]]) -> None:
        self._role_tools = role_tool_map

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        violations = []
        for span in trace.spans:
            agent_role = span.attributes.get("agent.role")
            if agent_role and agent_role in self._role_tools:
                allowed = self._role_tools[agent_role]
                if span.kind == SpanKind.TOOL and span.name not in allowed:
                    violations.append(f"role '{agent_role}' used tool '{span.name}'")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "role_boundary",
                passed,
                value=violations,
                reason="; ".join(violations) if violations else None,
            )
        ]


class HandoffCompletenessEvaluator:
    id = "ananke.multi_agent.handoff_completeness"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, required_handoff_fields: list[str] | None = None) -> None:
        self._required = required_handoff_fields or ["task_id", "context", "status"]

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        spans = _subagent_spans(trace)
        if not spans:
            return [skipped_score(self.id, _DIM, "handoff_completeness", "no subagent spans")]
        violations = []
        for span in spans:
            handoff = span.output if isinstance(span.output, dict) else {}
            missing = [f for f in self._required if f not in handoff]
            if missing:
                violations.append(f"{span.name}: missing={missing}")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "handoff_completeness",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class SharedContextConsistencyEvaluator:
    id = "ananke.multi_agent.shared_context_consistency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        spans = _subagent_spans(trace)
        if len(spans) < 2:
            return [
                skipped_score(
                    self.id, _DIM, "shared_context_consistency", "fewer than 2 subagent spans"
                )
            ]
        context_keys: dict[str, str] = {}
        violations = []
        for span in spans:
            ctx = span.attributes.get("shared_context", {})
            for key, value in ctx.items() if isinstance(ctx, dict) else {}.items():
                if key in context_keys and context_keys[key] != str(value):
                    violations.append(f"key '{key}' inconsistent across agents")
                context_keys[key] = str(value)
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "shared_context_consistency",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class CyclicDelegationEvaluator:
    id = "ananke.multi_agent.cyclic_delegation"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        seen: set[str] = set()
        for span in _subagent_spans(trace):
            agent_id = span.attributes.get("agent_id", span.name)
            if agent_id in seen:
                return [
                    make_score(
                        self.id,
                        _DIM,
                        "cyclic_delegation",
                        False,
                        reason=f"agent '{agent_id}' appeared twice in delegation chain",
                    )
                ]
            seen.add(agent_id)
        return [make_score(self.id, _DIM, "cyclic_delegation", True)]


class MessageDuplicationEvaluator:
    id = "ananke.multi_agent.message_duplication"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        from collections import Counter

        messages = [str(s.input) for s in _subagent_spans(trace) if s.input is not None]
        counts = Counter(messages)
        dupes = {m[:40]: c for m, c in counts.items() if c > 1}
        passed = len(dupes) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "message_duplication",
                passed,
                value=dict(dupes),
                reason=f"duplicate messages: {list(dupes.keys())}" if dupes else None,
            )
        ]

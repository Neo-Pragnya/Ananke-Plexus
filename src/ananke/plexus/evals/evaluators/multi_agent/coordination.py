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


def _agent_of(span: AgentSpan) -> str:
    return str(span.attributes.get("agent_id", span.name))


def _delegation_graph(trace: AgentTrace) -> dict[str, set[str]]:
    """Edges delegator -> delegate, one per subagent span.

    The delegate is the subagent span's agent. The delegator is the agent of its nearest
    ancestor (via ``parent_span_id``) that is a subagent or agent span; a subagent span with
    no such ancestor is delegated from the trace root, which adds no edge.
    """
    by_id = {s.span_id: s for s in trace.spans}
    graph: dict[str, set[str]] = {}
    for span in _subagent_spans(trace):
        parent_id = span.parent_span_id
        visited: set[str] = set()
        delegator: str | None = None
        while parent_id is not None and parent_id in by_id and parent_id not in visited:
            visited.add(parent_id)
            parent = by_id[parent_id]
            if parent.kind in (SpanKind.SUBAGENT, SpanKind.AGENT):
                delegator = _agent_of(parent)
                break
            parent_id = parent.parent_span_id
        if delegator is not None:
            graph.setdefault(delegator, set()).add(_agent_of(span))
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Return one cycle as a closed path (``[a, b, a]``), or None if the graph is acyclic."""
    done: set[str] = set()
    for start in sorted(graph):
        if start in done:
            continue
        path: list[str] = [start]
        on_path = {start}
        stack = [iter(sorted(graph.get(start, ())))]
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                stack.pop()
                finished = path.pop()
                on_path.discard(finished)
                done.add(finished)
            elif nxt in on_path:
                return [*path[path.index(nxt) :], nxt]
            elif nxt not in done:
                path.append(nxt)
                on_path.add(nxt)
                stack.append(iter(sorted(graph.get(nxt, ()))))
    return None


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
        delegated_tasks = {span.attributes.get("task", span.name) for span in spans}
        for task, expected_agent in self._expected.items():
            if task not in delegated_tasks:
                violations.append(f"task '{task}': expected {expected_agent}, never delegated")
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
        cycle = _find_cycle(_delegation_graph(trace))
        if cycle:
            return [
                make_score(
                    self.id,
                    _DIM,
                    "cyclic_delegation",
                    False,
                    reason=f"cyclic delegation: {' -> '.join(cycle)}",
                )
            ]
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

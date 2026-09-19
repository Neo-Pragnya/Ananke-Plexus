"""Tool-use evaluators: selection, argument, schema, allowlist/denylist, idempotency, retry."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "tool_use"


def _tool_spans(trace: AgentTrace) -> list[AgentSpan]:
    return [s for s in trace.spans if s.kind == SpanKind.TOOL]


class ToolSelectionEvaluator:
    id = "ananke.tool_use.tool_selection"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, expected_tools: list[str]) -> None:
        self._expected = set(expected_tools)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        used = {s.name for s in _tool_spans(trace)}
        missing = self._expected - used
        passed = len(missing) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "tool_selection",
                passed,
                reason=f"expected tools not used: {missing}" if missing else None,
                evidence=[f"used={sorted(used)}", f"expected={sorted(self._expected)}"],
            )
        ]


class ToolAllowlistEvaluator:
    id = "ananke.tool_use.tool_allowlist"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        used = {s.name for s in _tool_spans(trace)}
        violations = used - self._allowed
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "tool_allowlist",
                passed,
                value=list(violations),
                reason=f"disallowed tools used: {violations}" if violations else None,
            )
        ]


class ToolDenylistEvaluator:
    id = "ananke.tool_use.tool_denylist"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, denied: list[str]) -> None:
        self._denied = set(denied)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        used = {s.name for s in _tool_spans(trace)}
        violations = used & self._denied
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "tool_denylist",
                passed,
                value=list(violations),
                reason=f"denied tools used: {violations}" if violations else None,
            )
        ]


class ToolSchemaEvaluator:
    """Validates that tool call arguments conform to declared schemas."""

    id = "ananke.tool_use.tool_schema"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, schemas: dict[str, dict[str, Any]]) -> None:
        self._schemas = schemas

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        try:
            import jsonschema
        except ImportError:
            return [skipped_score(self.id, _DIM, "tool_schema", "jsonschema not installed")]

        violations = []
        for span in _tool_spans(trace):
            schema = self._schemas.get(span.name)
            if schema and span.input is not None:
                try:
                    jsonschema.validate(span.input, schema)
                except Exception as exc:
                    violations.append(f"{span.name}: {exc}")

        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "tool_schema",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class ToolOrderEvaluator:
    id = "ananke.tool_use.tool_order"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, must_precede: list[tuple[str, str]]) -> None:
        self._pairs = must_precede

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        names = [s.name for s in _tool_spans(trace)]
        violations = []
        for before, after in self._pairs:
            if before in names and after in names and names.index(before) > names.index(after):
                violations.append(f"{before} must precede {after}")
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "tool_order",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class DuplicateSideEffectEvaluator:
    """Detects tool calls that produce side effects being called more than once."""

    id = "ananke.tool_use.duplicate_side_effect"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, side_effect_tools: list[str]) -> None:
        self._side_effects = set(side_effect_tools)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        counts = Counter(s.name for s in _tool_spans(trace) if s.name in self._side_effects)
        violations = {k: v for k, v in counts.items() if v > 1}
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "duplicate_side_effect",
                passed,
                value=violations,
                reason=f"repeated side-effect calls: {violations}" if violations else None,
            )
        ]


class IdempotencyEvaluator:
    """Checks that idempotency keys in tool inputs are consistent (not reused with different args)."""

    id = "ananke.tool_use.idempotency"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        key_map: dict[str, Any] = {}
        violations = []
        for span in _tool_spans(trace):
            if isinstance(span.input, dict):
                ikey = span.input.get("idempotency_key")
                if ikey:
                    if ikey in key_map and key_map[ikey] != span.input:
                        violations.append(f"key '{ikey}' reused with different args")
                    key_map[ikey] = span.input
        passed = len(violations) == 0
        return [
            make_score(
                self.id,
                _DIM,
                "idempotency",
                passed,
                reason="; ".join(violations) if violations else None,
            )
        ]


class ToolRetryEvaluator:
    id = "ananke.tool_use.tool_retry"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, max_retries: int = 3) -> None:
        self._max = max_retries

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = trace.usage.retries
        passed = actual <= self._max
        return [
            make_score(
                self.id,
                _DIM,
                "tool_retry",
                passed,
                value=actual,
                reason=f"retries={actual} > max={self._max}" if not passed else None,
            )
        ]

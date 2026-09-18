"""Outcome evaluators: exact match, normalized, regex, JSON, schema, set, numeric, task completion."""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING, Any

from ananke.plexus.evals.evaluators.base import make_score, skipped_score
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore
from ananke.plexus.evals.models.trace import AgentTrace

if TYPE_CHECKING:
    from ananke.plexus.evals.context import EvaluationContext

_DIM = "outcome"


class ExactMatchEvaluator:
    id = "ananke.outcome.exact_match"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if case.expected is None:
            return [skipped_score(self.id, _DIM, "exact_match", "no expected value")]
        actual = str(trace.final_output)
        expected = str(case.expected)
        passed = actual == expected
        return [
            make_score(
                self.id,
                _DIM,
                "exact_match",
                passed,
                value=actual,
                reason=f"expected={expected!r}" if not passed else None,
            )
        ]


class NormalizedMatchEvaluator:
    id = "ananke.outcome.normalized_match"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if case.expected is None:
            return [skipped_score(self.id, _DIM, "normalized_match", "no expected value")]
        actual = str(trace.final_output).strip().lower()
        expected = str(case.expected).strip().lower()
        passed = actual == expected
        return [make_score(self.id, _DIM, "normalized_match", passed, value=actual)]


class RegexMatchEvaluator:
    id = "ananke.outcome.regex_match"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, pattern: str) -> None:
        self._pattern = re.compile(pattern)

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        actual = str(trace.final_output)
        passed = bool(self._pattern.search(actual))
        return [
            make_score(
                self.id,
                _DIM,
                "regex_match",
                passed,
                value=actual,
                reason=f"pattern={self._pattern.pattern!r}" if not passed else None,
            )
        ]


class JsonEqualityEvaluator:
    id = "ananke.outcome.json_equality"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if case.expected is None:
            return [skipped_score(self.id, _DIM, "json_equality", "no expected value")]
        try:
            actual_str = (
                trace.final_output
                if isinstance(trace.final_output, str)
                else json.dumps(trace.final_output)
            )
            expected_str = (
                case.expected if isinstance(case.expected, str) else json.dumps(case.expected)
            )
            actual_norm = json.dumps(json.loads(actual_str), sort_keys=True)
            expected_norm = json.dumps(json.loads(expected_str), sort_keys=True)
            passed = actual_norm == expected_norm
        except (json.JSONDecodeError, TypeError) as exc:
            return [make_score(self.id, _DIM, "json_equality", False, reason=str(exc))]
        return [make_score(self.id, _DIM, "json_equality", passed)]


class SchemaConformanceEvaluator:
    id = "ananke.outcome.schema_conformance"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        try:
            import jsonschema  # type: ignore[import-untyped]

            jsonschema.validate(trace.final_output, self._schema)
            return [make_score(self.id, _DIM, "schema_conformance", True)]
        except ImportError:
            return [skipped_score(self.id, _DIM, "schema_conformance", "jsonschema not installed")]
        except Exception as exc:
            return [make_score(self.id, _DIM, "schema_conformance", False, reason=str(exc))]


class SetEqualityEvaluator:
    id = "ananke.outcome.set_equality"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if case.expected is None:
            return [skipped_score(self.id, _DIM, "set_equality", "no expected value")]
        try:
            final = trace.final_output
            actual: set[object] = (
                set(final) if not isinstance(final, (set, type(None))) else (final or set())
            )
            exp = case.expected
            expected: set[object] = (
                set(exp) if not isinstance(exp, (set, type(None))) else (exp or set())
            )
            passed = actual == expected
            missing = expected - actual
            extra = actual - expected
            reason = None if passed else f"missing={missing}, extra={extra}"
            return [make_score(self.id, _DIM, "set_equality", passed, reason=reason)]
        except TypeError as exc:
            return [make_score(self.id, _DIM, "set_equality", False, reason=str(exc))]


class NumericToleranceEvaluator:
    id = "ananke.outcome.numeric_tolerance"
    version = "1"
    dimension = _DIM
    deterministic = True

    def __init__(self, epsilon: float = 1e-6) -> None:
        self._epsilon = epsilon

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        if case.expected is None:
            return [skipped_score(self.id, _DIM, "numeric_tolerance", "no expected value")]
        try:
            actual = float(trace.final_output)  # type: ignore[arg-type]
            expected = float(case.expected)
            passed = math.isclose(actual, expected, abs_tol=self._epsilon)
            return [
                make_score(
                    self.id,
                    _DIM,
                    "numeric_tolerance",
                    passed,
                    value=actual,
                    reason=f"|{actual}-{expected}|>{self._epsilon}" if not passed else None,
                )
            ]
        except (TypeError, ValueError) as exc:
            return [make_score(self.id, _DIM, "numeric_tolerance", False, reason=str(exc))]


class TaskCompletionEvaluator:
    """Hybrid: checks if final_output is non-empty and expected['task_completed'] is satisfied."""

    id = "ananke.outcome.task_completion"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        output = trace.final_output
        if output is None:
            return [
                make_score(
                    self.id,
                    _DIM,
                    "task_completion",
                    False,
                    normalized=0.0,
                    reason="no final_output produced",
                )
            ]

        if isinstance(case.expected, dict) and "task_completed" in case.expected:
            expected_completed: bool = case.expected["task_completed"]
            has_output = bool(output)
            passed = has_output if expected_completed else not has_output
        else:
            passed = bool(output)

        return [
            make_score(self.id, _DIM, "task_completion", passed, normalized=1.0 if passed else 0.0)
        ]


class AcceptanceCriteriaEvaluator:
    """Checks how many acceptance criteria from the case metadata are referenced in the output."""

    id = "ananke.outcome.acceptance_criteria"
    version = "1"
    dimension = _DIM
    deterministic = True

    def evaluate(
        self, *, case: EvalCase, trace: AgentTrace, context: EvaluationContext
    ) -> list[EvalScore]:
        criteria: list[str] = case.metadata.get("acceptance_criteria", [])
        if not criteria:
            return [
                skipped_score(self.id, _DIM, "acceptance_criteria", "no criteria in case metadata")
            ]

        output_text = str(trace.final_output).lower()
        met = [c for c in criteria if c.lower() in output_text]
        ratio = len(met) / len(criteria)
        passed = ratio >= 0.8
        return [
            make_score(
                self.id,
                _DIM,
                "acceptance_criteria",
                passed,
                normalized=round(ratio, 3),
                reason=f"{len(met)}/{len(criteria)} criteria found",
                evidence=[f"met: {met}", f"missed: {[c for c in criteria if c not in met]}"],
            )
        ]

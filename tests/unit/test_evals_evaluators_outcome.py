"""Tests for outcome evaluators in the eval harness."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.base import error_score, make_score, skipped_score
from ananke.plexus.evals.evaluators.outcome.exact import (
    AcceptanceCriteriaEvaluator,
    ExactMatchEvaluator,
    JsonEqualityEvaluator,
    NormalizedMatchEvaluator,
    NumericToleranceEvaluator,
    RegexMatchEvaluator,
    SchemaConformanceEvaluator,
    SetEqualityEvaluator,
    TaskCompletionEvaluator,
)
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalStatus
from ananke.plexus.evals.models.trace import AgentTrace, Usage

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_trace(output=None) -> AgentTrace:
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=[],
        usage=Usage(),
        final_output=output,
    )


def make_case(expected=None, metadata=None) -> EvalCase:
    return EvalCase(id="c1", input="test", expected=expected, metadata=metadata or {})


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


# ---------------------------------------------------------------------------
# ExactMatchEvaluator
# ---------------------------------------------------------------------------


class TestExactMatchEvaluator:
    def setup_method(self):
        self.ev = ExactMatchEvaluator()

    def test_pass_exact(self, ctx):
        scores = self.ev.evaluate(case=make_case("hello"), trace=make_trace("hello"), context=ctx)
        assert len(scores) == 1
        assert scores[0].status == EvalStatus.PASS

    def test_fail_different(self, ctx):
        scores = self.ev.evaluate(case=make_case("hello"), trace=make_trace("world"), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_when_no_expected(self, ctx):
        scores = self.ev.evaluate(case=make_case(None), trace=make_trace("x"), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_id_and_dimension(self):
        assert self.ev.id == "ananke.outcome.exact_match"
        assert self.ev.dimension == "outcome"

    def test_deterministic(self):
        assert self.ev.deterministic is True

    def test_metric_name(self, ctx):
        scores = self.ev.evaluate(case=make_case("a"), trace=make_trace("a"), context=ctx)
        assert scores[0].metric == "exact_match"


# ---------------------------------------------------------------------------
# NormalizedMatchEvaluator
# ---------------------------------------------------------------------------


class TestNormalizedMatchEvaluator:
    def setup_method(self):
        self.ev = NormalizedMatchEvaluator()

    def test_pass_same_case_insensitive(self, ctx):
        scores = self.ev.evaluate(case=make_case("HELLO"), trace=make_trace("hello"), context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_trims_whitespace(self, ctx):
        scores = self.ev.evaluate(
            case=make_case("  hello  "), trace=make_trace("hello"), context=ctx
        )
        assert scores[0].status == EvalStatus.PASS

    def test_fail_different(self, ctx):
        scores = self.ev.evaluate(case=make_case("hello"), trace=make_trace("bye"), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_expected(self, ctx):
        scores = self.ev.evaluate(case=make_case(None), trace=make_trace("x"), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_id(self):
        assert self.ev.id == "ananke.outcome.normalized_match"


# ---------------------------------------------------------------------------
# RegexMatchEvaluator
# ---------------------------------------------------------------------------


class TestRegexMatchEvaluator:
    def test_pass_when_pattern_matches(self, ctx):
        ev = RegexMatchEvaluator(r"\d+")
        scores = ev.evaluate(case=make_case(), trace=make_trace("answer is 42"), context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_when_pattern_does_not_match(self, ctx):
        ev = RegexMatchEvaluator(r"\d{10,}")
        scores = ev.evaluate(case=make_case(), trace=make_trace("hello world"), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_fail_reason_contains_pattern(self, ctx):
        ev = RegexMatchEvaluator(r"xyz")
        scores = ev.evaluate(case=make_case(), trace=make_trace("abc"), context=ctx)
        assert scores[0].reason and "xyz" in scores[0].reason

    def test_id(self):
        assert RegexMatchEvaluator(r"x").id == "ananke.outcome.regex_match"


# ---------------------------------------------------------------------------
# JsonEqualityEvaluator
# ---------------------------------------------------------------------------


class TestJsonEqualityEvaluator:
    def setup_method(self):
        self.ev = JsonEqualityEvaluator()

    def test_pass_equal_json(self, ctx):
        scores = self.ev.evaluate(
            case=make_case('{"b": 2, "a": 1}'),
            trace=make_trace('{"a": 1, "b": 2}'),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.PASS

    def test_fail_different_json(self, ctx):
        scores = self.ev.evaluate(
            case=make_case('{"a": 1}'),
            trace=make_trace('{"a": 2}'),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.FAIL

    def test_fail_invalid_json(self, ctx):
        scores = self.ev.evaluate(
            case=make_case('{"a": 1}'),
            trace=make_trace("not-json"),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_expected(self, ctx):
        scores = self.ev.evaluate(case=make_case(None), trace=make_trace('{"a": 1}'), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_pass_with_dict_objects(self, ctx):
        scores = self.ev.evaluate(
            case=make_case({"a": 1, "b": 2}),
            trace=make_trace({"b": 2, "a": 1}),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# SetEqualityEvaluator
# ---------------------------------------------------------------------------


class TestSetEqualityEvaluator:
    def setup_method(self):
        self.ev = SetEqualityEvaluator()

    def test_pass_equal_sets(self, ctx):
        scores = self.ev.evaluate(
            case=make_case(["b", "a"]),
            trace=make_trace(["a", "b"]),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.PASS

    def test_fail_missing_element(self, ctx):
        scores = self.ev.evaluate(
            case=make_case(["a", "b", "c"]),
            trace=make_trace(["a", "b"]),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_expected(self, ctx):
        scores = self.ev.evaluate(case=make_case(None), trace=make_trace(["a"]), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# NumericToleranceEvaluator
# ---------------------------------------------------------------------------


class TestNumericToleranceEvaluator:
    def test_pass_within_default_epsilon(self, ctx):
        ev = NumericToleranceEvaluator()
        scores = ev.evaluate(case=make_case(3.14), trace=make_trace(3.14), context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_within_custom_epsilon(self, ctx):
        ev = NumericToleranceEvaluator(epsilon=0.1)
        scores = ev.evaluate(case=make_case(3.0), trace=make_trace(3.05), context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_outside_epsilon(self, ctx):
        ev = NumericToleranceEvaluator(epsilon=0.001)
        scores = ev.evaluate(case=make_case(3.0), trace=make_trace(3.5), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_fail_non_numeric_output(self, ctx):
        ev = NumericToleranceEvaluator()
        scores = ev.evaluate(case=make_case(3.0), trace=make_trace("not a number"), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_skipped_no_expected(self, ctx):
        ev = NumericToleranceEvaluator()
        scores = ev.evaluate(case=make_case(None), trace=make_trace(1.0), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED


# ---------------------------------------------------------------------------
# TaskCompletionEvaluator
# ---------------------------------------------------------------------------


class TestTaskCompletionEvaluator:
    def setup_method(self):
        self.ev = TaskCompletionEvaluator()

    def test_fail_no_output(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace(None), context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_pass_has_output(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace("done"), context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_pass_when_task_completed_true_and_has_output(self, ctx):
        scores = self.ev.evaluate(
            case=make_case(expected={"task_completed": True}),
            trace=make_trace("result"),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.PASS

    def test_fail_when_task_completed_true_but_no_output(self, ctx):
        scores = self.ev.evaluate(
            case=make_case(expected={"task_completed": True}),
            trace=make_trace(None),
            context=ctx,
        )
        assert scores[0].status == EvalStatus.FAIL

    def test_normalized_score_1_on_pass(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace("output"), context=ctx)
        assert scores[0].normalized_score == pytest.approx(1.0)

    def test_normalized_score_0_on_fail(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace(None), context=ctx)
        assert scores[0].normalized_score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# AcceptanceCriteriaEvaluator
# ---------------------------------------------------------------------------


class TestAcceptanceCriteriaEvaluator:
    def setup_method(self):
        self.ev = AcceptanceCriteriaEvaluator()

    def test_skipped_no_criteria(self, ctx):
        scores = self.ev.evaluate(case=make_case(), trace=make_trace("output"), context=ctx)
        assert scores[0].status == EvalStatus.SKIPPED

    def test_pass_all_criteria_met(self, ctx):
        case = make_case(metadata={"acceptance_criteria": ["criterion one", "criterion two"]})
        trace = make_trace("This output mentions criterion one and also criterion two in detail.")
        scores = self.ev.evaluate(case=case, trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.PASS

    def test_fail_too_few_criteria_met(self, ctx):
        case = make_case(
            metadata={"acceptance_criteria": ["alpha", "beta", "gamma", "delta", "epsilon"]}
        )
        trace = make_trace("only alpha is mentioned here")
        scores = self.ev.evaluate(case=case, trace=trace, context=ctx)
        assert scores[0].status == EvalStatus.FAIL

    def test_normalized_score_in_range(self, ctx):
        case = make_case(metadata={"acceptance_criteria": ["a", "b"]})
        trace = make_trace("output with a inside")
        scores = self.ev.evaluate(case=case, trace=trace, context=ctx)
        assert 0.0 <= scores[0].normalized_score <= 1.0


# ---------------------------------------------------------------------------
# SchemaConformanceEvaluator
# ---------------------------------------------------------------------------


class TestSchemaConformanceEvaluator:
    def test_skipped_when_jsonschema_not_installed(self, ctx):
        """If jsonschema is not installed it returns SKIPPED, otherwise PASS/FAIL."""
        ev = SchemaConformanceEvaluator({"type": "object"})
        scores = ev.evaluate(case=make_case(), trace=make_trace({"key": "val"}), context=ctx)
        # either SKIPPED (jsonschema missing) or PASS (conformant object)
        assert scores[0].status in (EvalStatus.SKIPPED, EvalStatus.PASS)


# ---------------------------------------------------------------------------
# make_score / skipped_score / error_score helpers
# ---------------------------------------------------------------------------


class TestBaseHelpers:
    def test_make_score_pass(self):
        s = make_score("ev", "dim", "met", True, value=1.0, normalized=1.0, reason="ok")
        assert s.status == EvalStatus.PASS
        assert s.normalized_score == 1.0

    def test_make_score_fail(self):
        s = make_score("ev", "dim", "met", False)
        assert s.status == EvalStatus.FAIL

    def test_skipped_score(self):
        s = skipped_score("ev", "dim", "met", "reason text")
        assert s.status == EvalStatus.SKIPPED
        assert s.reason == "reason text"

    def test_error_score(self):
        s = error_score("ev", "dim", "met", "boom")
        assert s.status == EvalStatus.ERROR
        assert "boom" in s.reason

    def test_make_score_evidence(self):
        s = make_score("ev", "d", "m", True, evidence=["e1", "e2"])
        assert s.evidence == ["e1", "e2"]

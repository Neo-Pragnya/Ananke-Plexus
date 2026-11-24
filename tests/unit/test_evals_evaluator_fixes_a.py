"""Regression tests for evaluator fixes (architecture, efficiency, outcome, planning)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.architecture.engineering import (
    ArchitectureConformanceEvaluator,
    BreakingContractEvaluator,
    ChangedFileScopeEvaluator,
)
from ananke.plexus.evals.evaluators.efficiency.budget import (
    CacheEfficiencyEvaluator,
    LatencyEvaluator,
)
from ananke.plexus.evals.evaluators.outcome.exact import (
    ExactMatchEvaluator,
    NormalizedMatchEvaluator,
    SetEqualityEvaluator,
    TaskCompletionEvaluator,
)
from ananke.plexus.evals.evaluators.planning.plan import (
    PlanAdherenceEvaluator,
    PlanDependencyEvaluator,
    PlanFeasibilityEvaluator,
    PlanRiskEvaluator,
)
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalStatus
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage
from ananke.plexus.evals.traces.normalize import normalize_trace

PASS, FAIL, SKIPPED = EvalStatus.PASS, EvalStatus.FAIL, EvalStatus.SKIPPED


def _span(name: str, kind: SpanKind, output: Any = None, **attrs: Any) -> AgentSpan:
    return AgentSpan(
        span_id=name,
        kind=kind,
        name=name,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        output=output,
        attributes=attrs,
    )


def _trace(
    output: Any = None, spans: list[AgentSpan] | None = None, usage: Usage | None = None
) -> AgentTrace:
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=spans or [],
        usage=usage or Usage(),
        final_output=output,
    )


def _case(expected: Any = None, metadata: dict[str, Any] | None = None) -> EvalCase:
    return EvalCase(id="c1", input="task", expected=expected, metadata=metadata or {})


def _plan(output: Any, *extra: AgentSpan) -> AgentTrace:
    return _trace(spans=[_span("plan", SpanKind.PLANNING, output=output), *extra])


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


def _status(ev: Any, ctx: EvaluationContext, case: EvalCase, trace: AgentTrace) -> EvalStatus:
    return ev.evaluate(case=case, trace=trace, context=ctx)[0].status


# ---------------------------------------------------------------------------
# 1. architecture_conformance
# ---------------------------------------------------------------------------


class TestArchitectureConformance:
    def _ctx(self, tmp_path, ids: list[str]) -> EvaluationContext:
        calm = tmp_path / "system.calm.json"
        calm.write_text(json.dumps({"nodes": [{"id": i} for i in ids]}), encoding="utf-8")
        return EvaluationContext(project_root=tmp_path, calm_path=calm)

    def test_component_id_with_capitals_is_detected(self, tmp_path):
        ctx = self._ctx(tmp_path, ["OrderService"])
        score = ArchitectureConformanceEvaluator().evaluate(
            case=_case(), trace=_trace("I will DEL OrderService now"), context=ctx
        )[0]
        assert score.status == FAIL
        assert score.value == ["OrderService"]

    def test_lowercase_id_and_clean_output_unchanged(self, tmp_path):
        ctx = self._ctx(tmp_path, ["orders"])
        ev = ArchitectureConformanceEvaluator()
        assert _status(ev, ctx, _case(), _trace("del orders")) == FAIL
        assert _status(ev, ctx, _case(), _trace("added a field to orders")) == PASS


# ---------------------------------------------------------------------------
# 2. breaking_contract
# ---------------------------------------------------------------------------


class TestBreakingContract:
    def test_indicator_with_capitals_matches(self, ctx):
        ev = BreakingContractEvaluator(["Remove Endpoint"])
        assert _status(ev, ctx, _case(), _trace("we remove endpoint /v1/x")) == FAIL

    @pytest.mark.parametrize(
        "text",
        [
            "No breaking change here.",
            "This is not a breaking change.",
            "A non-breaking change to the docs.",
            "Refactor without breaking changes.",
        ],
    )
    def test_negated_mentions_are_not_breaking(self, ctx, text):
        assert _status(BreakingContractEvaluator(), ctx, _case(), _trace(text)) == PASS

    def test_unnegated_mention_still_breaking(self, ctx):
        ev = BreakingContractEvaluator()
        assert _status(ev, ctx, _case(), _trace("This is a breaking change.")) == FAIL
        # A negation in an earlier clause does not cover a later indicator.
        score = ev.evaluate(
            case=_case(), trace=_trace("Not risky, but we remove field `id`."), context=ctx
        )[0]
        assert score.status == FAIL
        assert score.value == ["remove field"]

    def test_negated_and_unnegated_occurrences(self, ctx):
        text = "No breaking change in v1. In v2 there is a breaking change."
        assert _status(BreakingContractEvaluator(), ctx, _case(), _trace(text)) == FAIL

    def test_empty_indicator_list_means_no_indicators(self, ctx):
        ev = BreakingContractEvaluator([])
        assert _status(ev, ctx, _case(), _trace("breaking change: remove field")) == PASS

    def test_none_uses_defaults(self, ctx):
        ev = BreakingContractEvaluator(None)
        assert _status(ev, ctx, _case(), _trace("rename field foo")) == FAIL


# ---------------------------------------------------------------------------
# 3. changed_file_scope
# ---------------------------------------------------------------------------


class TestChangedFileScope:
    def test_sibling_with_shared_prefix_is_out_of_scope(self, ctx):
        score = ChangedFileScopeEvaluator(["src/api"]).evaluate(
            case=_case(metadata={"changed_files": ["src/api_old/x.py"]}),
            trace=_trace(),
            context=ctx,
        )[0]
        assert score.status == FAIL
        assert score.value == ["src/api_old/x.py"]

    def test_paths_under_scope_and_normalised_forms_are_in_scope(self, ctx):
        files = ["src/api/x.py", "./src/api/sub/y.py", "src\\api\\z.py", "src/api"]
        ev = ChangedFileScopeEvaluator(["./src/api/"])
        assert _status(ev, ctx, _case(metadata={"changed_files": files}), _trace()) == PASS

    def test_out_of_scope_unchanged(self, ctx):
        ev = ChangedFileScopeEvaluator(["src/api", "tests"])
        case = _case(metadata={"changed_files": ["tests/test_a.py", "docs/readme.md"]})
        score = ev.evaluate(case=case, trace=_trace(), context=ctx)[0]
        assert score.status == FAIL
        assert score.value == ["docs/readme.md"]


# ---------------------------------------------------------------------------
# 4. cache_efficiency / 5. latency
# ---------------------------------------------------------------------------


class TestCacheEfficiency:
    def test_hits_on_non_model_spans_are_ignored(self, ctx):
        spans = [
            _span("llm", SpanKind.MODEL, **{"cache.hit": False}),
            _span("t1", SpanKind.TOOL, **{"cache.hit": True}),
            _span("t2", SpanKind.TOOL, **{"cache.hit": True}),
        ]
        score = CacheEfficiencyEvaluator().evaluate(
            case=_case(), trace=_trace(spans=spans), context=ctx
        )[0]
        assert score.status == FAIL
        assert score.normalized_score == 0.0

    def test_ratio_never_exceeds_one(self, ctx):
        spans = [
            _span("llm", SpanKind.MODEL, **{"cache.hit": True}),
            _span("t1", SpanKind.RETRIEVAL, **{"cache.hit": True}),
        ]
        score = CacheEfficiencyEvaluator().evaluate(
            case=_case(), trace=_trace(spans=spans), context=ctx
        )[0]
        assert score.status == PASS
        assert score.normalized_score == 1.0


class TestLatency:
    def test_failure_reason_names_the_limit_not_p95(self, ctx):
        score = LatencyEvaluator(max_duration_ms=100).evaluate(
            case=_case(), trace=_trace(usage=Usage(duration_ms=250)), context=ctx
        )[0]
        assert score.status == FAIL
        assert score.reason == "latency 250 ms exceeds the limit 100 ms"
        assert "p95" not in score.reason

    def test_pass_has_no_reason(self, ctx):
        score = LatencyEvaluator(max_duration_ms=100).evaluate(
            case=_case(), trace=_trace(usage=Usage(duration_ms=50)), context=ctx
        )[0]
        assert score.status == PASS
        assert score.reason is None


# ---------------------------------------------------------------------------
# 6. exact_match / normalized_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ev_cls", [ExactMatchEvaluator, NormalizedMatchEvaluator])
class TestMissingOutputMatch:
    def test_missing_output_does_not_match_text_none(self, ctx, ev_cls):
        score = ev_cls().evaluate(case=_case("None"), trace=_trace(None), context=ctx)[0]
        assert score.status == FAIL
        assert score.reason == "no final output"

    def test_missing_expected_still_skips(self, ctx, ev_cls):
        assert _status(ev_cls(), ctx, _case(None), _trace(None)) == SKIPPED

    def test_real_outputs_unchanged(self, ctx, ev_cls):
        assert _status(ev_cls(), ctx, _case("42"), _trace(42)) == PASS
        assert _status(ev_cls(), ctx, _case("False"), _trace(False)) == PASS
        assert _status(ev_cls(), ctx, _case("x"), _trace("y")) == FAIL


# ---------------------------------------------------------------------------
# 7. set_equality
# ---------------------------------------------------------------------------


class TestSetEqualityStrings:
    def test_string_is_not_a_set_of_characters(self, ctx):
        assert _status(SetEqualityEvaluator(), ctx, _case(["abc"]), _trace("cab")) == FAIL
        assert _status(SetEqualityEvaluator(), ctx, _case("abc"), _trace("cab")) == FAIL

    def test_string_split_on_commas_and_newlines(self, ctx):
        ev = SetEqualityEvaluator()
        assert _status(ev, ctx, _case(["a", "b", "c"]), _trace("c, b\na,\n")) == PASS
        assert _status(ev, ctx, _case("a,b"), _trace("b\na")) == PASS

    def test_string_json_list_is_parsed(self, ctx):
        ev = SetEqualityEvaluator()
        assert _status(ev, ctx, _case([1, 2]), _trace("[2, 1]")) == PASS
        assert _status(ev, ctx, _case('["x", "y"]'), _trace(["y", "x"])) == PASS

    def test_list_outputs_unchanged(self, ctx):
        ev = SetEqualityEvaluator()
        assert _status(ev, ctx, _case(["a", "b"]), _trace(["b", "a"])) == PASS
        assert _status(ev, ctx, _case(["a", "b"]), _trace(["a"])) == FAIL


# ---------------------------------------------------------------------------
# 8. task_completion on recorded traces (normalize_trace)
# ---------------------------------------------------------------------------


class TestFalsyRecordedOutput:
    @pytest.mark.parametrize("value", [False, 0, "", []])
    def test_falsy_final_output_is_kept(self, value):
        assert normalize_trace({"trace_id": "t", "final_output": value}).final_output == value

    def test_missing_final_output_falls_back_to_output(self):
        assert normalize_trace({"trace_id": "t", "output": "x"}).final_output == "x"
        assert normalize_trace({"trace_id": "t"}).final_output is None

    def test_task_completed_false_is_seen(self, ctx):
        trace = normalize_trace({"trace_id": "t", "final_output": False})
        case = _case({"task_completed": False})
        assert _status(TaskCompletionEvaluator(), ctx, case, trace) == PASS


# ---------------------------------------------------------------------------
# 9-12. planning
# ---------------------------------------------------------------------------


class TestPlanDependency:
    def test_prerequisite_after_dependent_is_a_violation(self, ctx):
        ev = PlanDependencyEvaluator([("build", "deploy")])
        score = ev.evaluate(case=_case(), trace=_plan("1. deploy\n2. build"), context=ctx)[0]
        assert score.status == FAIL
        assert "prerequisite" in (score.reason or "")

    def test_correct_order_and_missing_prerequisite_unchanged(self, ctx):
        ev = PlanDependencyEvaluator([("build", "deploy")])
        assert _status(ev, ctx, _case(), _plan(["build", "test", "deploy"])) == PASS
        assert _status(ev, ctx, _case(), _plan("just deploy")) == FAIL
        assert _status(ev, ctx, _case(), _plan("build only")) == PASS


class TestPlanFeasibility:
    def test_text_plan_steps_are_counted(self, ctx):
        ev = PlanFeasibilityEvaluator(max_steps=2)
        score = ev.evaluate(case=_case(), trace=_plan("1. a\n2. b\n3. c"), context=ctx)[0]
        assert score.status == FAIL
        assert score.value == 3

    def test_bullets_counted_over_prose_lines(self, ctx):
        ev = PlanFeasibilityEvaluator(max_steps=2)
        text = "Plan:\n- one\n- two\n\nThat is all."
        score = ev.evaluate(case=_case(), trace=_plan(text), context=ctx)[0]
        assert score.status == PASS
        assert score.value == 2

    def test_plain_lines_counted(self, ctx):
        score = PlanFeasibilityEvaluator(max_steps=1).evaluate(
            case=_case(), trace=_plan("read\n\nwrite\n"), context=ctx
        )[0]
        assert score.value == 2
        assert score.status == FAIL

    def test_list_plan_unchanged(self, ctx):
        ev = PlanFeasibilityEvaluator(max_steps=2)
        assert _status(ev, ctx, _case(), _plan(["a", "b"])) == PASS
        assert _status(ev, ctx, _case(), _plan(["a", "b", "c"])) == FAIL


class TestPlanAdherence:
    def test_dict_steps_do_not_raise(self, ctx):
        planned = [{"tool": "search"}, {"name": "write", "args": {"p": 1}}, {"x": 1}]
        trace = _plan(planned, _span("search", SpanKind.TOOL), _span("write", SpanKind.TOOL))
        score = PlanAdherenceEvaluator().evaluate(case=_case(), trace=trace, context=ctx)[0]
        assert score.status == FAIL  # 2 of 3 planned steps run
        assert score.normalized_score == pytest.approx(0.667)

    def test_string_steps_unchanged(self, ctx):
        trace = _plan(["search", "write"], _span("search", SpanKind.TOOL))
        trace.spans.append(_span("write", SpanKind.TOOL))
        score = PlanAdherenceEvaluator().evaluate(case=_case(), trace=trace, context=ctx)[0]
        assert score.status == PASS
        assert score.normalized_score == 1.0


class TestPlanRisk:
    def test_keyword_inside_a_word_does_not_match(self, ctx):
        plan = "add a dropdown and update the deleted_at column"
        assert _status(PlanRiskEvaluator(), ctx, _case(), _plan(plan)) == PASS

    def test_whole_words_and_multi_word_keywords_match(self, ctx):
        ev = PlanRiskEvaluator()
        score = ev.evaluate(
            case=_case(), trace=_plan("then DROP table users and force  push"), context=ctx
        )[0]
        assert score.status == FAIL
        assert sorted(score.value) == ["drop", "force push"]
        assert _status(ev, ctx, _case(), _plan("cleanup: rm -rf build/")) == FAIL


@pytest.mark.parametrize(
    ("keyword", "text", "hit"),
    [
        ("drop", "drop the table", True),
        ("drop", "dropped the old table", True),
        ("drop", "drops a column", True),
        ("drop", "open the dropdown menu", False),
        ("delete", "deleted rows", True),
        ("delete", "deleting the cache", True),
        ("truncate", "truncated logs", True),
        ("force push", "force pushed to main", True),
        ("rm -rf", "run rm -rf build", True),
    ],
)
def test_plan_risk_keywords_match_inflected_whole_words(keyword: str, text: str, hit: bool) -> None:
    from ananke.plexus.evals.evaluators.planning.plan import _keyword_pattern

    assert bool(_keyword_pattern(keyword).search(text)) is hit

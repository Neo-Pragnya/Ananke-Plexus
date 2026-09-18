"""Property-like invariant tests for the Ananke eval harness.

Uses only stdlib + pytest (no Hypothesis).  Each test encodes a logical
invariant that must hold across a wide range of inputs by iterating over
a representative sample or using parametrize.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

# ===========================================================================
# EvalStatus invariants
# ===========================================================================


def test_eval_status_all_values_are_strings() -> None:
    """Every EvalStatus member value is a str (StrEnum)."""
    from ananke.plexus.evals.models.score import EvalStatus

    for member in EvalStatus:
        assert isinstance(member.value, str), (
            f"EvalStatus.{member.name}.value should be str, got {type(member.value)}"
        )


def test_eval_status_is_str_enum() -> None:
    """EvalStatus members compare equal to their string representations."""
    from ananke.plexus.evals.models.score import EvalStatus

    assert EvalStatus.PASS == "pass"
    assert EvalStatus.FAIL == "fail"
    assert EvalStatus.ERROR == "error"
    assert EvalStatus.WARN == "warn"
    assert EvalStatus.REVIEW == "review"
    assert EvalStatus.SKIPPED == "skipped"


def test_eval_status_round_trip() -> None:
    """str → EvalStatus → str is the identity for every defined value."""
    from ananke.plexus.evals.models.score import EvalStatus

    for member in EvalStatus:
        roundtripped = EvalStatus(member.value)
        assert roundtripped == member
        assert str(roundtripped) == str(member)


def test_eval_status_passed_property() -> None:
    """EvalScore.passed is True for PASS and WARN, False for all others."""
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    for status in EvalStatus:
        score = EvalScore(
            evaluator_id="em",
            dimension="d",
            metric="m",
            status=status,
        )
        expected = status in (EvalStatus.PASS, EvalStatus.WARN)
        assert score.passed == expected, f"EvalScore(status={status}).passed should be {expected}"


# ===========================================================================
# EvalScore: normalized_score bounds
# ===========================================================================


@pytest.mark.parametrize("value", [0.0, 0.1, 0.5, 0.99, 1.0])
def test_eval_score_normalized_score_valid(value: float) -> None:
    """normalized_score in [0.0, 1.0] is accepted."""
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    score = EvalScore(
        evaluator_id="em",
        dimension="d",
        metric="m",
        status=EvalStatus.PASS,
        normalized_score=value,
    )
    assert score.normalized_score == value


@pytest.mark.parametrize("bad_value", [-0.1, -1.0, 1.01, 1.1, 2.0, 100.0])
def test_eval_score_normalized_score_rejects_out_of_bounds(bad_value: float) -> None:
    """normalized_score outside [0.0, 1.0] raises ValidationError."""
    from pydantic import ValidationError

    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    with pytest.raises(ValidationError):
        EvalScore(
            evaluator_id="em",
            dimension="d",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=bad_value,
        )


def test_eval_score_normalized_score_none_is_allowed() -> None:
    """normalized_score=None (absent) is valid."""
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    score = EvalScore(
        evaluator_id="em",
        dimension="d",
        metric="m",
        status=EvalStatus.PASS,
    )
    assert score.normalized_score is None


def test_eval_score_boundary_zero_is_valid() -> None:
    """normalized_score=0.0 is accepted."""
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    score = EvalScore(
        evaluator_id="em",
        dimension="d",
        metric="m",
        status=EvalStatus.PASS,
        normalized_score=0.0,
    )
    assert score.normalized_score == 0.0


def test_eval_score_boundary_one_is_valid() -> None:
    """normalized_score=1.0 is accepted."""
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    score = EvalScore(
        evaluator_id="em",
        dimension="d",
        metric="m",
        status=EvalStatus.PASS,
        normalized_score=1.0,
    )
    assert score.normalized_score == 1.0


# ===========================================================================
# SpanKind invariants
# ===========================================================================


def test_span_kind_all_values_are_strings() -> None:
    """Every SpanKind member value is a str (StrEnum)."""
    from ananke.plexus.evals.models.trace import SpanKind

    for member in SpanKind:
        assert isinstance(member.value, str), (
            f"SpanKind.{member.name}.value should be str, got {type(member.value)}"
        )


def test_span_kind_round_trip() -> None:
    """str → SpanKind → str round-trip is the identity for every member."""
    from ananke.plexus.evals.models.trace import SpanKind

    for member in SpanKind:
        roundtripped = SpanKind(member.value)
        assert roundtripped == member


def test_span_kind_values_are_lowercase() -> None:
    """SpanKind values are all lowercase strings."""
    from ananke.plexus.evals.models.trace import SpanKind

    for member in SpanKind:
        assert member.value == member.value.lower(), (
            f"SpanKind.{member.name} value should be lowercase"
        )


# ===========================================================================
# AgentTrace invariants
# ===========================================================================


def test_agent_trace_spans_by_kind_empty_trace() -> None:
    """spans_by_kind on a trace with no spans always returns []."""
    from ananke.plexus.evals.models.trace import AgentTrace, SpanKind

    trace = AgentTrace(trace_id="t", run_id="r", runtime="test")
    for kind in SpanKind:
        assert trace.spans_by_kind(kind) == []


def test_agent_trace_spans_by_kind_filtered_correctly() -> None:
    """spans_by_kind returns only spans matching the given kind."""

    from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind

    now = datetime.now(tz=UTC)
    spans = [
        AgentSpan(span_id=f"s{i}", kind=k, name=k.value, started_at=now)
        for i, k in enumerate(SpanKind)
    ]
    trace = AgentTrace(trace_id="t", run_id="r", runtime="test", spans=spans)
    for kind in SpanKind:
        result = trace.spans_by_kind(kind)
        assert len(result) == 1
        assert result[0].kind == kind


def test_agent_trace_tool_names_empty() -> None:
    """tool_names() on a trace with no tool spans returns []."""
    from ananke.plexus.evals.models.trace import AgentTrace

    trace = AgentTrace(trace_id="t", run_id="r", runtime="test")
    assert trace.tool_names() == []


def test_agent_trace_usage_defaults() -> None:
    """A fresh AgentTrace has Usage with tool_calls=0 and retries=0."""
    from ananke.plexus.evals.models.trace import AgentTrace

    trace = AgentTrace(trace_id="t", run_id="r", runtime="test")
    assert trace.usage.tool_calls == 0
    assert trace.usage.retries == 0


# ===========================================================================
# GateVerdict / EvalGateDecision invariants
# ===========================================================================


def test_gate_verdict_block_means_blocks_true() -> None:
    """Only BLOCK verdict should make EvalGateDecision.blocks True."""
    from ananke.plexus.evals.models.report import EvalGateDecision, GateVerdict

    for verdict in GateVerdict:
        decision = EvalGateDecision(verdict=verdict, suite_id="s", run_id="r")
        if verdict == GateVerdict.BLOCK:
            assert decision.blocks is True
        else:
            assert decision.blocks is False


def test_gate_verdict_all_values_are_strings() -> None:
    """Every GateVerdict value is a str."""
    from ananke.plexus.evals.models.report import GateVerdict

    for member in GateVerdict:
        assert isinstance(member.value, str)


def test_gate_verdict_round_trip() -> None:
    """str → GateVerdict → str round-trip is the identity."""
    from ananke.plexus.evals.models.report import GateVerdict

    for member in GateVerdict:
        assert GateVerdict(member.value) == member


# ===========================================================================
# EvalReport invariants
# ===========================================================================


def test_eval_report_pass_rate_empty_scores() -> None:
    """EvalReport.pass_rate() is 0.0 when there are no scores."""
    from ananke.plexus.evals.models.report import EvalReport

    report = EvalReport(run_id="r", suite_id="s")
    assert report.pass_rate() == 0.0


def test_eval_report_pass_rate_all_pass() -> None:
    """EvalReport.pass_rate() is 1.0 when all scores PASS."""
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    scores = [
        EvalScore(
            evaluator_id="em",
            dimension=f"d{i}",
            metric="m",
            status=EvalStatus.PASS,
        )
        for i in range(5)
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    assert report.pass_rate() == 1.0


def test_eval_report_pass_rate_no_pass() -> None:
    """EvalReport.pass_rate() is 0.0 when no scores PASS."""
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    scores = [
        EvalScore(
            evaluator_id="em",
            dimension=f"d{i}",
            metric="m",
            status=EvalStatus.FAIL,
        )
        for i in range(3)
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    assert report.pass_rate() == 0.0


def test_eval_report_scores_by_dimension() -> None:
    """scores_by_dimension returns only scores with matching dimension."""
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus

    scores = [
        EvalScore(evaluator_id="em", dimension="outcome", metric="m", status=EvalStatus.PASS),
        EvalScore(evaluator_id="em", dimension="quality", metric="m", status=EvalStatus.FAIL),
        EvalScore(evaluator_id="em", dimension="outcome", metric="m2", status=EvalStatus.WARN),
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    outcome_scores = report.scores_by_dimension("outcome")
    assert len(outcome_scores) == 2
    for s in outcome_scores:
        assert s.dimension == "outcome"


# ===========================================================================
# BaselineComparison: regressions and improvements are disjoint
# ===========================================================================


def test_baseline_comparison_regressions_improvements_disjoint() -> None:
    """Regressions and improvements never share a dimension key."""
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    # Create baseline with known scores
    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        scores={"d1": 0.5, "d2": 0.8, "d3": 0.3},
        created_at=datetime.now(tz=UTC),
    )
    # Candidate: d1 improves, d2 regresses, d3 is stable
    scores = [
        EvalScore(
            evaluator_id="em",
            dimension="d1",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=0.9,  # up from 0.5 → improvement
        ),
        EvalScore(
            evaluator_id="em",
            dimension="d2",
            metric="m",
            status=EvalStatus.FAIL,
            normalized_score=0.5,  # down from 0.8 → regression
        ),
        EvalScore(
            evaluator_id="em",
            dimension="d3",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=0.3,  # same → stable
        ),
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    comparison = compare_to_baseline(report, baseline)

    reg_keys = set(comparison.regressions.keys())
    imp_keys = set(comparison.improvements.keys())
    # Regressions and improvements must be disjoint
    assert reg_keys.isdisjoint(imp_keys), (
        f"dimensions appear in both regressions and improvements: {reg_keys & imp_keys}"
    )


def test_baseline_comparison_stable_not_in_regression_or_improvement() -> None:
    """Stable dimensions are not in regressions or improvements."""
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        scores={"d1": 0.7},
        created_at=datetime.now(tz=UTC),
    )
    scores = [
        EvalScore(
            evaluator_id="em",
            dimension="d1",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=0.7,  # exactly equal → stable
        )
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    comparison = compare_to_baseline(report, baseline)

    assert "d1" in comparison.stable
    assert "d1" not in comparison.regressions
    assert "d1" not in comparison.improvements


def test_baseline_comparison_regression_is_negative_delta() -> None:
    """All regression deltas are negative."""
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        scores={"d1": 0.9, "d2": 0.8},
        created_at=datetime.now(tz=UTC),
    )
    scores = [
        EvalScore(
            evaluator_id="em",
            dimension="d1",
            metric="m",
            status=EvalStatus.FAIL,
            normalized_score=0.3,
        ),
        EvalScore(
            evaluator_id="em",
            dimension="d2",
            metric="m",
            status=EvalStatus.FAIL,
            normalized_score=0.2,
        ),
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    comparison = compare_to_baseline(report, baseline)

    for dim, delta in comparison.regressions.items():
        assert delta < 0, f"Regression delta for {dim} should be negative, got {delta}"


def test_baseline_comparison_improvement_is_positive_delta() -> None:
    """All improvement deltas are positive."""
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        scores={"d1": 0.1, "d2": 0.2},
        created_at=datetime.now(tz=UTC),
    )
    scores = [
        EvalScore(
            evaluator_id="em",
            dimension="d1",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=0.9,
        ),
        EvalScore(
            evaluator_id="em",
            dimension="d2",
            metric="m",
            status=EvalStatus.PASS,
            normalized_score=0.8,
        ),
    ]
    report = EvalReport(run_id="r", suite_id="s", scores=scores)
    comparison = compare_to_baseline(report, baseline)

    for dim, delta in comparison.improvements.items():
        assert delta > 0, f"Improvement delta for {dim} should be positive, got {delta}"


# ===========================================================================
# Bootstrap CI invariants
# ===========================================================================


@pytest.mark.parametrize(
    "values",
    [
        [0.5],
        [0.0, 1.0],
        [0.3, 0.4, 0.5, 0.6, 0.7],
        [1.0] * 10,
        [0.0] * 10,
        [i / 10 for i in range(11)],
    ],
)
def test_bootstrap_ci_is_ordered(values: list) -> None:
    """bootstrap_confidence_interval always returns (lo, hi) with lo <= hi."""
    from ananke.plexus.evals.regression.statistics import (
        bootstrap_confidence_interval,
    )

    lo, hi = bootstrap_confidence_interval(values, n_bootstrap=200, seed=0)
    assert lo <= hi, f"CI lower bound {lo} > upper bound {hi} for values={values}"


def test_bootstrap_ci_empty_input() -> None:
    """bootstrap_confidence_interval on empty list returns (0.0, 0.0)."""
    from ananke.plexus.evals.regression.statistics import (
        bootstrap_confidence_interval,
    )

    lo, hi = bootstrap_confidence_interval([])
    assert lo == 0.0
    assert hi == 0.0


def test_bootstrap_ci_constant_input() -> None:
    """bootstrap_confidence_interval on a constant list: lo == hi == constant."""
    from ananke.plexus.evals.regression.statistics import (
        bootstrap_confidence_interval,
    )

    lo, hi = bootstrap_confidence_interval([0.7] * 50, n_bootstrap=100, seed=1)
    assert abs(lo - 0.7) < 1e-9
    assert abs(hi - 0.7) < 1e-9


def test_bootstrap_ci_is_within_data_range() -> None:
    """Bootstrap CI bounds lie within the min/max of the data."""
    from ananke.plexus.evals.regression.statistics import (
        bootstrap_confidence_interval,
    )

    values = [0.2, 0.4, 0.6, 0.8, 1.0]
    lo, hi = bootstrap_confidence_interval(values, n_bootstrap=500, seed=42)
    assert lo >= min(values)
    assert hi <= max(values)


# ===========================================================================
# Cohen's kappa invariants
# ===========================================================================


def test_cohens_kappa_perfect_agreement() -> None:
    """Identical rater sequences → kappa == 1.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    ratings = [0, 1, 2, 0, 1, 2, 1, 0]
    kappa = cohens_kappa(ratings, ratings)
    assert kappa == 1.0, f"Expected 1.0 for identical sequences, got {kappa}"


def test_cohens_kappa_perfect_disagreement_two_classes() -> None:
    """Perfectly disagreeing binary sequences → kappa == -1.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    a = [0, 0, 1, 1]
    b = [1, 1, 0, 0]
    kappa = cohens_kappa(a, b)
    assert abs(kappa - (-1.0)) < 1e-9, f"Expected -1.0 for perfect disagreement, got {kappa}"


def test_cohens_kappa_single_category() -> None:
    """When only one category exists, kappa == 1.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    kappa = cohens_kappa([1, 1, 1], [1, 1, 1])
    assert kappa == 1.0


def test_cohens_kappa_empty_returns_zero() -> None:
    """Empty rater lists → kappa == 0.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    assert cohens_kappa([], []) == 0.0


def test_cohens_kappa_mismatched_lengths_returns_zero() -> None:
    """Mismatched-length lists → kappa == 0.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    assert cohens_kappa([0, 1], [0]) == 0.0


@pytest.mark.parametrize(
    "ratings",
    [
        [0, 0, 1, 1, 2, 2],
        [1, 2, 3, 1, 2, 3],
        [0] * 4 + [1] * 4,
    ],
)
def test_cohens_kappa_self_agreement_is_one(ratings: list) -> None:
    """Any sequence agrees perfectly with itself → kappa == 1.0."""
    from ananke.plexus.evals.evaluators.meta.calibration import cohens_kappa

    assert cohens_kappa(ratings, ratings) == 1.0


# ===========================================================================
# EnterpriseJudgeGateway invariants
# ===========================================================================


def test_judge_gateway_list_providers_nonempty() -> None:
    """list_providers() returns a non-empty list."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway()
    providers = gw.list_providers()
    assert len(providers) > 0


def test_judge_gateway_list_providers_sorted() -> None:
    """list_providers() returns a sorted list."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway()
    providers = gw.list_providers()
    assert providers == sorted(providers)


def test_judge_gateway_supported_providers_in_list() -> None:
    """All SUPPORTED_PROVIDERS appear in list_providers() for the default gateway."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway()
    providers = set(gw.list_providers())
    for p in EnterpriseJudgeGateway.SUPPORTED_PROVIDERS:
        assert p in providers, f"Provider '{p}' missing from list_providers()"


def test_judge_gateway_local_is_in_list() -> None:
    """'local' is always in the default provider list."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway()
    assert "local" in gw.list_providers()


def test_judge_gateway_restricted_providers() -> None:
    """Gateway with restricted allowed_providers returns only those."""
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway

    gw = EnterpriseJudgeGateway(allowed_providers=["local", "azure"])
    providers = set(gw.list_providers())
    assert providers == {"local", "azure"}


def test_judge_gateway_disallowed_provider_raises() -> None:
    """Scoring with a provider not in allowed_providers raises PermissionError."""
    from ananke.plexus.evals.judges.base import JudgeInputEnvelope
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
    from ananke.plexus.evals.models.rubric import Rubric

    gw = EnterpriseJudgeGateway(allowed_providers=["local"])
    rubric = Rubric(rubric_id="r", title="T", pass_threshold=0.5)
    envelope = JudgeInputEnvelope(rubric=rubric, case_input="q", agent_output="a")

    with pytest.raises(PermissionError):
        gw.score(envelope=envelope, provider="azure")


def test_judge_gateway_local_score_in_range() -> None:
    """Local provider score is always in [0.0, 1.0]."""
    from ananke.plexus.evals.judges.base import JudgeInputEnvelope
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
    from ananke.plexus.evals.models.rubric import Rubric

    gw = EnterpriseJudgeGateway()
    rubric = Rubric(rubric_id="r", title="T", pass_threshold=0.5)

    for text in ["", "short", "a " * 100, "x " * 1000]:
        envelope = JudgeInputEnvelope(rubric=rubric, case_input="q", agent_output=text)
        result = gw.score(envelope=envelope, provider="local")
        assert 0.0 <= result.normalized_score <= 1.0, (
            f"Score {result.normalized_score} out of range for input length {len(text)}"
        )


# ===========================================================================
# EvalCase invariants
# ===========================================================================


def test_eval_case_has_tag_true() -> None:
    """has_tag returns True when tag is in tags list."""
    from ananke.plexus.evals.models.case import EvalCase

    case = EvalCase(id="c", input="x", tags=["regression", "smoke"])
    assert case.has_tag("regression")
    assert case.has_tag("smoke")


def test_eval_case_has_tag_false() -> None:
    """has_tag returns False when tag is not in tags list."""
    from ananke.plexus.evals.models.case import EvalCase

    case = EvalCase(id="c", input="x", tags=["smoke"])
    assert not case.has_tag("regression")
    assert not case.has_tag("")


def test_eval_case_has_tag_empty_tags() -> None:
    """has_tag always returns False when tags is empty."""
    from ananke.plexus.evals.models.case import EvalCase

    case = EvalCase(id="c", input="x")
    for tag in ("a", "b", "regression", "smoke"):
        assert not case.has_tag(tag)


# ===========================================================================
# Statistics utility invariants
# ===========================================================================


@pytest.mark.parametrize(
    "values,expected",
    [
        ([0.0, 1.0], 0.5),
        ([1.0, 1.0, 1.0], 1.0),
        ([0.0, 0.0, 0.0], 0.0),
        ([0.2, 0.4, 0.6, 0.8], 0.5),
    ],
)
def test_statistics_mean(values: list, expected: float) -> None:
    """mean() is correct for representative inputs."""
    from ananke.plexus.evals.regression.statistics import mean

    assert abs(mean(values) - expected) < 1e-9


def test_statistics_mean_empty() -> None:
    """mean([]) returns 0.0."""
    from ananke.plexus.evals.regression.statistics import mean

    assert mean([]) == 0.0


def test_statistics_pass_rate_all_above_threshold() -> None:
    """pass_rate is 1.0 when all values are at or above the threshold."""
    from ananke.plexus.evals.regression.statistics import pass_rate

    assert pass_rate([0.7, 0.8, 0.9, 1.0], threshold=0.7) == 1.0


def test_statistics_pass_rate_none_above_threshold() -> None:
    """pass_rate is 0.0 when no values meet the threshold."""
    from ananke.plexus.evals.regression.statistics import pass_rate

    assert pass_rate([0.0, 0.1, 0.5], threshold=0.7) == 0.0


# ===========================================================================
# EvalSuite / GatePolicy invariants
# ===========================================================================


def test_eval_suite_default_policy_has_no_rules() -> None:
    """A fresh EvalSuite with default GatePolicy has an empty rules dict."""
    from ananke.plexus.evals.models.suite import EvalSuite, GatePolicy

    suite = EvalSuite(id="s")
    assert isinstance(suite.policy, GatePolicy)
    assert len(suite.policy.rules) == 0


def test_gate_policy_rule_for_missing_dimension() -> None:
    """GatePolicy.rule_for returns None for a dimension with no rule."""
    from ananke.plexus.evals.models.suite import GatePolicy

    policy = GatePolicy()
    assert policy.rule_for("nonexistent-dimension") is None


def test_gate_policy_rule_for_present_dimension() -> None:
    """GatePolicy.rule_for returns the correct rule when one exists."""
    from ananke.plexus.evals.models.suite import GatePolicy, GatePolicyRule

    rule = GatePolicyRule(minimum=0.8, on_failure="block")
    policy = GatePolicy(rules={"outcome": rule})
    found = policy.rule_for("outcome")
    assert found is not None
    assert found.minimum == 0.8


# ===========================================================================
# Baseline invariants
# ===========================================================================


def test_baseline_approved_false_without_approval() -> None:
    """Baseline.approved is False when no approval is set."""
    from ananke.plexus.evals.models.baseline import Baseline

    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        created_at=datetime.now(tz=UTC),
    )
    assert baseline.approved is False


def test_baseline_approved_true_with_approval() -> None:
    """Baseline.approved is True when an approval record is provided."""
    from ananke.plexus.evals.models.baseline import Baseline, BaselineApproval

    approval = BaselineApproval(
        approved_by="alice",
        approved_at=datetime.now(tz=UTC),
    )
    baseline = Baseline(
        baseline_id="b",
        suite_id="s",
        version="v1",
        approval=approval,
        created_at=datetime.now(tz=UTC),
    )
    assert baseline.approved is True

"""Tests for regression statistics, comparison, and policy enforcement."""

from __future__ import annotations

import pytest

from ananke.plexus.evals.models.baseline import Baseline
from ananke.plexus.evals.models.report import BaselineComparison, EvalReport
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.regression.compare import compare_to_baseline
from ananke.plexus.evals.regression.policy import enforce_regression_policy
from ananke.plexus.evals.regression.statistics import (
    bootstrap_confidence_interval,
    mean,
    median,
    pass_rate,
    percentile,
    summary_statistics,
    variance,
)

# ---------------------------------------------------------------------------
# mean
# ---------------------------------------------------------------------------


class TestMean:
    def test_basic(self):
        assert mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_single(self):
        assert mean([5.0]) == pytest.approx(5.0)

    def test_empty(self):
        assert mean([]) == pytest.approx(0.0)

    def test_all_zeros(self):
        assert mean([0.0, 0.0, 0.0]) == pytest.approx(0.0)

    def test_floats(self):
        assert mean([0.1, 0.2, 0.3]) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# median
# ---------------------------------------------------------------------------


class TestMedian:
    def test_odd_count(self):
        assert median([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_even_count(self):
        assert median([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5)

    def test_single(self):
        assert median([7.0]) == pytest.approx(7.0)

    def test_empty(self):
        assert median([]) == pytest.approx(0.0)

    def test_unsorted_input(self):
        assert median([3.0, 1.0, 2.0]) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# pass_rate
# ---------------------------------------------------------------------------


class TestPassRate:
    def test_all_pass(self):
        assert pass_rate([0.8, 0.9, 1.0], threshold=0.7) == pytest.approx(1.0)

    def test_none_pass(self):
        assert pass_rate([0.1, 0.2, 0.3], threshold=0.7) == pytest.approx(0.0)

    def test_mixed(self):
        assert pass_rate([0.9, 0.5, 0.8], threshold=0.7) == pytest.approx(2 / 3)

    def test_empty(self):
        assert pass_rate([], threshold=0.7) == pytest.approx(0.0)

    def test_at_threshold(self):
        # 0.7 >= 0.7 → pass
        assert pass_rate([0.7], threshold=0.7) == pytest.approx(1.0)

    def test_default_threshold(self):
        assert pass_rate([0.8, 0.6]) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# variance
# ---------------------------------------------------------------------------


class TestVariance:
    def test_basic(self):
        v = variance([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert v == pytest.approx(4.571428571, abs=1e-5)

    def test_single_returns_zero(self):
        assert variance([5.0]) == pytest.approx(0.0)

    def test_empty_returns_zero(self):
        assert variance([]) == pytest.approx(0.0)

    def test_same_values(self):
        assert variance([3.0, 3.0, 3.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_p50(self):
        assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)

    def test_p0_returns_min(self):
        assert percentile([1.0, 5.0, 3.0], 0) == pytest.approx(1.0)

    def test_p100_returns_max(self):
        assert percentile([1.0, 5.0, 3.0], 100) == pytest.approx(5.0)

    def test_empty(self):
        assert percentile([], 50) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# bootstrap_confidence_interval
# ---------------------------------------------------------------------------


class TestBootstrapConfidenceInterval:
    def test_returns_tuple(self):
        result = bootstrap_confidence_interval([0.5, 0.6, 0.7, 0.8], seed=42)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_lo_le_hi(self):
        lo, hi = bootstrap_confidence_interval([0.5, 0.6, 0.7, 0.8], seed=42)
        assert lo <= hi

    def test_bounds_in_range(self):
        lo, hi = bootstrap_confidence_interval([0.5, 0.6, 0.7, 0.8], seed=42)
        assert 0.0 <= lo <= 1.0
        assert 0.0 <= hi <= 1.0

    def test_empty_returns_zeros(self):
        lo, hi = bootstrap_confidence_interval([])
        assert lo == 0.0
        assert hi == 0.0

    def test_deterministic_with_seed(self):
        r1 = bootstrap_confidence_interval([0.4, 0.6, 0.8], seed=123)
        r2 = bootstrap_confidence_interval([0.4, 0.6, 0.8], seed=123)
        assert r1 == r2


# ---------------------------------------------------------------------------
# summary_statistics
# ---------------------------------------------------------------------------


class TestSummaryStatistics:
    def test_empty_returns_n_zero(self):
        result = summary_statistics([])
        assert result["n"] == 0

    def test_basic_keys(self):
        result = summary_statistics([0.5, 0.7, 0.9])
        for key in ("n", "mean", "median", "variance", "pass_rate", "p5", "p95"):
            assert key in result

    def test_n_matches(self):
        result = summary_statistics([0.1, 0.5, 0.9])
        assert result["n"] == 3

    def test_mean_correct(self):
        result = summary_statistics([0.6, 0.8, 1.0])
        assert result["mean"] == pytest.approx(0.8, abs=0.001)

    def test_ci_keys_present(self):
        result = summary_statistics([0.5, 0.7, 0.9])
        assert "ci_95_lo" in result
        assert "ci_95_hi" in result


# ---------------------------------------------------------------------------
# compare_to_baseline
# ---------------------------------------------------------------------------


def _make_baseline(scores: dict[str, float]) -> Baseline:
    return Baseline(
        baseline_id="baseline-001",
        suite_id="test-suite",
        version="1",
        scores=scores,
    )


def _make_report(scores: list[tuple[str, float]]) -> EvalReport:
    """scores = [(dimension, normalized_score), ...]"""
    eval_scores = [
        EvalScore(
            evaluator_id="test",
            dimension=dim,
            metric="score",
            status=EvalStatus.PASS,
            normalized_score=val,
        )
        for dim, val in scores
    ]
    return EvalReport(run_id="r1", suite_id="s1", scores=eval_scores)


class TestCompareToBaseline:
    def test_returns_baseline_comparison(self):
        baseline = _make_baseline({"outcome": 0.8})
        report = _make_report([("outcome", 0.9)])
        comparison = compare_to_baseline(report, baseline)
        assert isinstance(comparison, BaselineComparison)

    def test_improvement_detected(self):
        baseline = _make_baseline({"outcome": 0.7})
        report = _make_report([("outcome", 0.85)])
        comparison = compare_to_baseline(report, baseline)
        assert "outcome" in comparison.improvements

    def test_regression_detected(self):
        baseline = _make_baseline({"outcome": 0.9})
        report = _make_report([("outcome", 0.7)])
        comparison = compare_to_baseline(report, baseline)
        assert "outcome" in comparison.regressions

    def test_stable_when_similar(self):
        baseline = _make_baseline({"outcome": 0.8})
        report = _make_report([("outcome", 0.805)])  # delta < 0.01
        comparison = compare_to_baseline(report, baseline)
        assert "outcome" in comparison.stable

    def test_regression_value_is_negative(self):
        baseline = _make_baseline({"outcome": 0.9})
        report = _make_report([("outcome", 0.7)])
        comparison = compare_to_baseline(report, baseline)
        assert comparison.regressions["outcome"] < 0

    def test_improvement_value_is_positive(self):
        baseline = _make_baseline({"outcome": 0.5})
        report = _make_report([("outcome", 0.9)])
        comparison = compare_to_baseline(report, baseline)
        assert comparison.improvements["outcome"] > 0

    def test_baseline_id_set(self):
        baseline = _make_baseline({"outcome": 0.8})
        report = _make_report([("outcome", 0.8)])
        comparison = compare_to_baseline(report, baseline)
        assert comparison.baseline_id == "baseline-001"

    def test_regression_blocked_false_by_default(self):
        baseline = _make_baseline({"outcome": 0.9})
        report = _make_report([("outcome", 0.7)])
        comparison = compare_to_baseline(report, baseline)
        assert comparison.regression_blocked is False

    def test_dimension_not_in_baseline_skipped(self):
        baseline = _make_baseline({"outcome": 0.8})
        report = _make_report([("outcome", 0.9), ("safety", 0.7)])
        comparison = compare_to_baseline(report, baseline)
        # safety is not in baseline so it should not appear in regressions/improvements
        assert "safety" not in comparison.regressions
        assert "safety" not in comparison.improvements


# ---------------------------------------------------------------------------
# enforce_regression_policy
# ---------------------------------------------------------------------------


class TestEnforceRegressionPolicy:
    def _comparison(self, regressions: dict[str, float]) -> BaselineComparison:
        return BaselineComparison(
            baseline_id="b1",
            regressions=regressions,
            improvements={},
            stable=[],
            regression_blocked=False,
        )

    def test_no_regressions_not_blocked(self):
        comparison = self._comparison({})
        blocked, reasons = enforce_regression_policy(comparison, {})
        assert blocked is False
        assert reasons == []

    def test_blocked_when_max_drop_exceeded(self):
        comparison = self._comparison({"outcome": -0.1})
        policy = {"outcome": {"max_drop": 0.05}}
        blocked, reasons = enforce_regression_policy(comparison, policy)
        assert blocked is True
        assert len(reasons) > 0

    def test_not_blocked_when_within_max_drop(self):
        comparison = self._comparison({"outcome": -0.01})
        policy = {"outcome": {"max_drop": 0.05}}
        blocked, _reasons = enforce_regression_policy(comparison, policy)
        assert blocked is False

    def test_blocked_when_regression_not_allowed(self):
        comparison = self._comparison({"safety": -0.02})
        policy = {"safety": {"allow_regression": False}}
        blocked, _reasons = enforce_regression_policy(comparison, policy)
        assert blocked is True

    def test_not_blocked_when_regression_allowed_and_within_drop(self):
        comparison = self._comparison({"outcome": -0.01})
        policy = {"outcome": {"allow_regression": True, "max_drop": 0.05}}
        blocked, _reasons = enforce_regression_policy(comparison, policy)
        assert blocked is False

    def test_reason_mentions_dimension(self):
        comparison = self._comparison({"outcome": -0.2})
        policy = {"outcome": {"max_drop": 0.05}}
        _blocked, reasons = enforce_regression_policy(comparison, policy)
        assert any("outcome" in r for r in reasons)

    def test_regression_blocked_flag_set_on_comparison(self):
        comparison = self._comparison({"outcome": -0.2})
        policy = {"outcome": {"max_drop": 0.01}}
        enforce_regression_policy(comparison, policy)
        assert comparison.regression_blocked is True

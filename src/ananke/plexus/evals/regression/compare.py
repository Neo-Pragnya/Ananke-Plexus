"""Regression comparison — metric deltas between candidate and baseline."""

from __future__ import annotations

from ananke.plexus.evals.models.baseline import Baseline
from ananke.plexus.evals.models.report import BaselineComparison, EvalReport


def compare_to_baseline(report: EvalReport, baseline: Baseline) -> BaselineComparison:
    """Compute score deltas between a candidate report and an approved baseline."""
    candidate_scores: dict[str, float] = {}
    for score in report.scores:
        if score.normalized_score is not None:
            candidate_scores[score.dimension] = score.normalized_score

    regressions: dict[str, float] = {}
    improvements: dict[str, float] = {}
    stable: list[str] = []

    for dim, baseline_val in baseline.scores.items():
        candidate_val = candidate_scores.get(dim)
        if candidate_val is None:
            continue
        delta = candidate_val - baseline_val
        if delta < -0.01:
            regressions[dim] = round(delta, 4)
        elif delta > 0.01:
            improvements[dim] = round(delta, 4)
        else:
            stable.append(dim)

    return BaselineComparison(
        baseline_id=baseline.baseline_id,
        regressions=regressions,
        improvements=improvements,
        stable=stable,
        regression_blocked=False,
    )

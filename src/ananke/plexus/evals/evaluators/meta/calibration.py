"""Meta-evaluation: judge-human agreement, Cohen's kappa, bias detection."""

from __future__ import annotations

import math
from typing import Any


def cohens_kappa(ratings_a: list[int], ratings_b: list[int]) -> float:
    """Compute Cohen's kappa for two rater sequences of integer labels."""
    if len(ratings_a) != len(ratings_b) or not ratings_a:
        return 0.0

    n = len(ratings_a)
    categories = sorted(set(ratings_a) | set(ratings_b))
    k = len(categories)
    if k == 1:
        return 1.0

    cat_index = {c: i for i, c in enumerate(categories)}
    confusion = [[0] * k for _ in range(k)]
    for a, b in zip(ratings_a, ratings_b, strict=False):
        confusion[cat_index[a]][cat_index[b]] += 1

    observed_agreement = sum(confusion[i][i] for i in range(k)) / n
    row_totals = [sum(confusion[i]) / n for i in range(k)]
    col_totals = [sum(confusion[r][c] for r in range(k)) / n for c in range(k)]
    expected_agreement = sum(row_totals[i] * col_totals[i] for i in range(k))

    if expected_agreement >= 1.0:
        return 1.0
    return (observed_agreement - expected_agreement) / (1.0 - expected_agreement)


def judge_human_agreement(judge_scores: list[float], human_scores: list[float]) -> dict[str, float]:
    """Compute agreement statistics between judge and human scores."""
    if len(judge_scores) != len(human_scores) or not judge_scores:
        return {"agreement": 0.0, "mae": 0.0, "correlation": 0.0}

    n = len(judge_scores)
    mae = sum(abs(j - h) for j, h in zip(judge_scores, human_scores, strict=False)) / n
    mean_j = sum(judge_scores) / n
    mean_h = sum(human_scores) / n
    cov = (
        sum((j - mean_j) * (h - mean_h) for j, h in zip(judge_scores, human_scores, strict=False))
        / n
    )
    std_j = math.sqrt(sum((j - mean_j) ** 2 for j in judge_scores) / n)
    std_h = math.sqrt(sum((h - mean_h) ** 2 for h in human_scores) / n)
    correlation = cov / (std_j * std_h) if std_j > 0 and std_h > 0 else 0.0
    exact_matches = sum(
        1 for j, h in zip(judge_scores, human_scores, strict=False) if round(j, 1) == round(h, 1)
    )
    return {
        "agreement": exact_matches / n,
        "mae": round(mae, 4),
        "correlation": round(correlation, 4),
    }


def detect_positional_bias(
    scores_position_a: list[float], scores_position_b: list[float]
) -> dict[str, Any]:
    """Detect positional bias: does position A consistently score higher than B?"""
    if len(scores_position_a) != len(scores_position_b):
        return {"bias_detected": False, "reason": "unequal samples"}
    n = len(scores_position_a)
    if n == 0:
        return {"bias_detected": False}
    a_higher = sum(1 for a, b in zip(scores_position_a, scores_position_b, strict=False) if a > b)
    b_higher = sum(1 for a, b in zip(scores_position_a, scores_position_b, strict=False) if b > a)
    a_rate = a_higher / n
    bias = abs(a_rate - 0.5) > 0.2
    return {
        "bias_detected": bias,
        "position_a_win_rate": round(a_rate, 3),
        "position_b_win_rate": round(b_higher / n, 3),
    }


def score_variance(scores: list[float]) -> float:
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    return sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)

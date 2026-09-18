"""Statistical comparison utilities — mean, pass rate, variance, bootstrap CI."""

from __future__ import annotations

import random
from typing import Any


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]


def pass_rate(values: list[float], threshold: float = 0.7) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v >= threshold) / len(values)


def variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


def bootstrap_confidence_interval(
    values: list[float],
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float]:
    """Return (lower, upper) bootstrap CI for the mean."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    sample_means = sorted(mean([rng.choice(values) for _ in range(n)]) for _ in range(n_bootstrap))
    lo_idx = int(alpha / 2 * n_bootstrap)
    hi_idx = int((1 - alpha / 2) * n_bootstrap) - 1
    return (sample_means[lo_idx], sample_means[hi_idx])


def summary_statistics(values: list[float], threshold: float = 0.7) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ci_lo, ci_hi = bootstrap_confidence_interval(values)
    return {
        "n": len(values),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "variance": round(variance(values), 4),
        "pass_rate": round(pass_rate(values, threshold), 4),
        "p5": round(percentile(values, 5), 4),
        "p95": round(percentile(values, 95), 4),
        "ci_95_lo": round(ci_lo, 4),
        "ci_95_hi": round(ci_hi, 4),
    }

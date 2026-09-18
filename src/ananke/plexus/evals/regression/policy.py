"""Regression policy — enforce max_drop / max_increase / allow_regression rules."""

from __future__ import annotations

from ananke.plexus.evals.models.report import BaselineComparison


def enforce_regression_policy(
    comparison: BaselineComparison,
    policy: dict[str, dict[str, object]],
) -> tuple[bool, list[str]]:
    """
    Returns (blocked, reasons).
    policy format:
      {"task_completion": {"max_drop": 0.02}, "cost": {"max_increase_percent": 20}}
    """
    blocked = False
    reasons = []

    for dim, rules in policy.items():
        delta = comparison.regressions.get(dim, 0.0)
        max_drop = rules.get("max_drop")
        allow_regression = rules.get("allow_regression", True)

        if not allow_regression and dim in comparison.regressions:
            blocked = True
            reasons.append(f"{dim}: regression not allowed (delta={delta:+.4f})")

        elif max_drop is not None and abs(delta) > float(max_drop):  # type: ignore[arg-type]
            blocked = True
            reasons.append(f"{dim}: drop {abs(delta):.4f} > max_drop {max_drop}")

    if blocked:
        comparison.regression_blocked = True
    return blocked, reasons

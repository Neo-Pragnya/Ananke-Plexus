"""Gate policy decisions — EvalScore[] → EvalGateDecision."""

from __future__ import annotations

from datetime import UTC, datetime

from ananke.plexus.evals.models.report import EvalGateDecision, GateVerdict
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.suite import GatePolicy


def apply_gate_policy(
    suite_id: str,
    run_id: str,
    scores: list[EvalScore],
    policy: GatePolicy,
) -> EvalGateDecision:
    """Convert scores + policy rules into a gate decision."""
    blocking: list[str] = []
    warning: list[str] = []
    review: list[str] = []
    evidence: list[str] = []

    for score in scores:
        rule = policy.rule_for(score.dimension) or policy.rule_for(score.metric)
        if rule is None:
            continue

        raw = score.value
        val = (
            score.normalized_score
            if score.normalized_score is not None
            else float(raw if isinstance(raw, (int, float)) else 0)
        )

        failed = False
        if rule.minimum is not None and val < rule.minimum:
            failed = True
            evidence.append(f"{score.dimension}.{score.metric}={val:.3f} < minimum={rule.minimum}")
        if rule.maximum is not None and val > rule.maximum:
            failed = True
            evidence.append(f"{score.dimension}.{score.metric}={val:.3f} > maximum={rule.maximum}")
        if rule.required and score.status in (EvalStatus.FAIL, EvalStatus.ERROR):
            failed = True
            evidence.append(f"{score.dimension}.{score.metric} required but status={score.status}")

        if failed:
            if rule.on_failure == "block":
                blocking.append(score.dimension)
            elif rule.on_failure == "warn":
                warning.append(score.dimension)
            elif rule.on_failure == "review":
                review.append(score.dimension)

    if blocking:
        verdict = GateVerdict.BLOCK
    elif review:
        verdict = GateVerdict.REVIEW
    elif warning:
        verdict = GateVerdict.WARN
    else:
        verdict = GateVerdict.PASS

    return EvalGateDecision(
        verdict=verdict,
        suite_id=suite_id,
        run_id=run_id,
        blocking_dimensions=blocking,
        warning_dimensions=warning,
        review_dimensions=review,
        evidence=evidence,
        decided_at=datetime.now(tz=UTC),
    )

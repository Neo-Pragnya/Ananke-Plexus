"""QualityGateVerdict, QualityGateDecision, apply_quality_gate."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.test import TestStatus


class QualityGateVerdict(StrEnum):
    PASS = "pass"
    WARN = "warn"
    REVIEW = "review"
    BLOCK = "block"


# Alias kept for backwards compatibility with spec name
QualityGateDecision_verdict = QualityGateVerdict


class QualityGateDecision(BaseModel):
    verdict: QualityGateVerdict
    run_id: str
    total_tests: int
    passed: int
    failed: int
    errors: int
    skipped: int
    unavailable: int
    blocks: bool
    reasons: list[str] = Field(default_factory=list)


def _run_seconds(run: TestRun) -> float | None:
    if run.ended_at is not None:
        return (run.ended_at - run.started_at).total_seconds()
    durations = [r.duration_ms for r in run.results if r.duration_ms is not None]
    return sum(durations) / 1000 if durations else None


def apply_quality_gate(run: TestRun, config: Any = None) -> QualityGateDecision:
    """Evaluate a TestRun against quality gate rules and produce a decision."""
    from ananke.plexus.testing.policy.thresholds import QualityGateConfig, load_quality_config

    if config is None:
        gate_config = load_quality_config()
    elif isinstance(config, QualityGateConfig):
        gate_config = config
    else:
        gate_config = QualityGateConfig()

    passed = sum(1 for r in run.results if r.status == TestStatus.PASS)
    failed = sum(1 for r in run.results if r.status == TestStatus.FAIL)
    errors = sum(1 for r in run.results if r.status == TestStatus.ERROR)
    skipped = sum(1 for r in run.results if r.status == TestStatus.SKIPPED)
    unavailable = sum(1 for r in run.results if r.status == TestStatus.UNAVAILABLE)
    warned = sum(1 for r in run.results if r.status == TestStatus.WARN)
    total = len(run.results)

    reasons: list[str] = []
    blocks = False
    verdict = QualityGateVerdict.PASS

    if gate_config.block_on_required_failure and failed > 0:
        blocks = True
        reasons.append(f"{failed} test(s) failed")

    if gate_config.block_on_error and errors > 0:
        blocks = True
        reasons.append(f"{errors} test(s) errored")

    if gate_config.warn_on_skipped and skipped > 0:
        reasons.append(f"{skipped} test(s) skipped")
        if verdict == QualityGateVerdict.PASS:
            verdict = QualityGateVerdict.WARN

    unmeasured: list[str] = []
    for label, metric, threshold in (
        ("coverage", "coverage_percent", gate_config.coverage_threshold),
        ("mutation score", "mutation_score", gate_config.mutation_score_threshold),
    ):
        if threshold is None:
            continue
        values = [
            float(r.metrics[metric])
            for r in run.results
            if isinstance(r.metrics.get(metric), int | float)
        ]
        if not values:
            unmeasured.append(
                f"{label} threshold {threshold:g}% configured but nothing measured it"
            )
            continue
        measured = min(values)
        if measured < threshold:
            blocks = True
            reasons.append(f"{label} {measured:g}% is below the {threshold:g}% threshold")

    if gate_config.max_duration_seconds is not None:
        elapsed = _run_seconds(run)
        if elapsed is not None and elapsed > gate_config.max_duration_seconds:
            blocks = True
            reasons.append(
                f"run took {elapsed:.0f}s, over the {gate_config.max_duration_seconds}s budget"
            )

    if unmeasured:
        # A configured threshold nobody measured must not pass silently.
        reasons.extend(unmeasured)
        if verdict == QualityGateVerdict.PASS:
            verdict = QualityGateVerdict.WARN

    if warned > 0 and verdict == QualityGateVerdict.PASS:
        verdict = QualityGateVerdict.WARN

    if blocks:
        verdict = QualityGateVerdict.BLOCK

    return QualityGateDecision(
        verdict=verdict,
        run_id=run.run_id,
        total_tests=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        unavailable=unavailable,
        blocks=blocks,
        reasons=reasons,
    )

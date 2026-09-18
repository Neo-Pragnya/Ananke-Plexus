"""Markdown report generator — PR-ready evaluation summary table."""

from __future__ import annotations

from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalStatus


def _status_icon(status: EvalStatus) -> str:
    return {
        EvalStatus.PASS: "✅",
        EvalStatus.WARN: "⚠️",
        EvalStatus.REVIEW: "🔍",
        EvalStatus.FAIL: "❌",
        EvalStatus.ERROR: "💥",
        EvalStatus.SKIPPED: "⏭️",
    }.get(status, "❓")


def generate_markdown_report(report: EvalReport) -> str:
    lines = [
        "## Agent Evaluation Report",
        "",
        f"**Suite:** `{report.suite_id}`  ",
        f"**Run:** `{report.run_id}`",
        "",
        "| Dimension | Metric | Score | Threshold | Status |",
        "|---|---|---|---|---|",
    ]
    for score in report.scores:
        score_display = (
            f"{score.normalized_score:.3f}"
            if score.normalized_score is not None
            else str(score.value)
        )
        threshold_display = f">= {score.threshold:.2f}" if score.threshold else "—"
        lines.append(
            f"| {score.dimension} | {score.metric} | {score_display} "
            f"| {threshold_display} | {_status_icon(score.status)} {score.status.value} |"
        )

    lines += [""]
    if report.gate_decision:
        verdict = report.gate_decision.verdict
        lines += [f"**Gate Verdict:** {verdict.value}", ""]
        if report.gate_decision.blocking_dimensions:
            lines += [f"**Blocking:** {', '.join(report.gate_decision.blocking_dimensions)}", ""]

    if report.baseline_comparison:
        bc = report.baseline_comparison
        lines += [f"**Baseline:** `{bc.baseline_id}`", ""]
        if bc.regressions:
            lines += ["**Regressions:**"]
            for dim, delta in bc.regressions.items():
                lines.append(f"- {dim}: {delta:+.4f}")
            lines += [""]

    return "\n".join(lines)

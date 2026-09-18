"""Console report — plain text table for terminal output."""

from __future__ import annotations

from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalStatus


def generate_console_report(report: EvalReport) -> str:
    icon = {
        EvalStatus.PASS: "PASS",
        EvalStatus.WARN: "WARN",
        EvalStatus.REVIEW: "REVIEW",
        EvalStatus.FAIL: "FAIL",
        EvalStatus.ERROR: "ERROR",
        EvalStatus.SKIPPED: "SKIP",
    }
    header = f"Ananke Eval — Suite: {report.suite_id} | Run: {report.run_id}"
    sep = "-" * len(header)
    lines = [header, sep]
    for score in report.scores:
        val = (
            f"{score.normalized_score:.3f}"
            if score.normalized_score is not None
            else str(score.value)
        )
        status = icon.get(score.status, "?")
        reason = f" — {score.reason}" if score.reason else ""
        lines.append(f"  [{status:6}] {score.dimension}.{score.metric} = {val}{reason}")
    lines.append(sep)

    if report.gate_decision:
        lines.append(f"GATE VERDICT: {report.gate_decision.verdict.value}")
    pass_count = sum(1 for s in report.scores if s.status == EvalStatus.PASS)
    fail_count = sum(1 for s in report.scores if s.status in (EvalStatus.FAIL, EvalStatus.ERROR))
    lines.append(f"Passed: {pass_count} | Failed: {fail_count} | Total: {len(report.scores)}")
    return "\n".join(lines)

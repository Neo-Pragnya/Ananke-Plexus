"""generate_markdown_report — Markdown table output for a TestRun."""

from __future__ import annotations

from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.test import TestStatus

_STATUS_EMOJI = {
    TestStatus.PASS: "✅",
    TestStatus.FAIL: "❌",
    TestStatus.ERROR: "💥",
    TestStatus.SKIPPED: "⏭️",
    TestStatus.WARN: "⚠️",
    TestStatus.UNAVAILABLE: "🚫",
}


def generate_markdown_report(run: TestRun) -> str:
    """Return a Markdown table summary of a TestRun."""
    lines: list[str] = [
        f"# Quality Test Run: `{run.run_id}`",
        "",
        f"**Profile:** {run.profile}  ",
        f"**Tests:** {len(run.results)}  ",
        "",
        "| Test ID | Kind | Status | Duration (ms) |",
        "|---------|------|--------|---------------|",
    ]
    for r in run.results:
        duration = f"{r.duration_ms:.1f}" if r.duration_ms is not None else "-"
        emoji = _STATUS_EMOJI.get(r.status, r.status)
        lines.append(f"| `{r.test_id}` | {r.kind} | {emoji} {r.status} | {duration} |")

    passed = sum(1 for r in run.results if r.status == TestStatus.PASS)
    failed = sum(1 for r in run.results if r.status == TestStatus.FAIL)
    errors = sum(1 for r in run.results if r.status == TestStatus.ERROR)
    skipped = sum(1 for r in run.results if r.status == TestStatus.SKIPPED)

    lines += [
        "",
        f"**Summary:** passed={passed} | failed={failed} | errors={errors} | skipped={skipped}",
    ]
    return "\n".join(lines)

"""generate_console_report — ASCII table output for a TestRun."""

from __future__ import annotations

from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.test import TestStatus

_STATUS_SYMBOL = {
    TestStatus.PASS: "PASS",
    TestStatus.FAIL: "FAIL",
    TestStatus.ERROR: "ERR ",
    TestStatus.SKIPPED: "SKIP",
    TestStatus.WARN: "WARN",
    TestStatus.UNAVAILABLE: "N/A ",
}


def generate_console_report(run: TestRun) -> str:
    """Return an ASCII table summary of a TestRun."""
    lines: list[str] = [
        f"Quality Test Run: {run.run_id}",
        f"Profile:          {run.profile}",
        f"Tests:            {len(run.results)}",
        "",
        f"{'TEST ID':<40} {'KIND':<16} {'STATUS':<6} {'DURATION (ms)':>14}",
        "-" * 82,
    ]
    for r in run.results:
        duration = f"{r.duration_ms:.1f}" if r.duration_ms is not None else "-"
        symbol = _STATUS_SYMBOL.get(r.status, r.status.upper()[:4])
        lines.append(f"{r.test_id:<40} {r.kind:<16} {symbol:<6} {duration:>14}")

    passed = sum(1 for r in run.results if r.status == TestStatus.PASS)
    failed = sum(1 for r in run.results if r.status == TestStatus.FAIL)
    errors = sum(1 for r in run.results if r.status == TestStatus.ERROR)
    skipped = sum(1 for r in run.results if r.status == TestStatus.SKIPPED)

    lines += [
        "-" * 82,
        f"passed={passed}  failed={failed}  errors={errors}  skipped={skipped}",
    ]
    return "\n".join(lines)

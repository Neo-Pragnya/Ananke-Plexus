"""generate_sarif_report — SARIF 2.1.0 JSON output for a TestRun."""

from __future__ import annotations

import json
from datetime import UTC

from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.test import TestStatus

_SARIF_LEVEL = {
    TestStatus.PASS: "none",
    TestStatus.FAIL: "error",
    TestStatus.ERROR: "error",
    TestStatus.SKIPPED: "none",
    TestStatus.WARN: "warning",
    TestStatus.UNAVAILABLE: "note",
}


def generate_sarif_report(run: TestRun) -> str:
    """Return SARIF 2.1.0 JSON representing the quality test findings."""
    results = []
    for r in run.results:
        if r.status in (TestStatus.PASS, TestStatus.SKIPPED):
            continue
        results.append(
            {
                "ruleId": r.test_id,
                "level": _SARIF_LEVEL.get(r.status, "none"),
                "message": {"text": r.message or f"Test {r.test_id} status: {r.status}"},
                "properties": {
                    "kind": r.kind,
                    "engine": r.engine,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                },
            }
        )

    sarif: dict[str, object] = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ananke-plexus-testing",
                        "informationUri": "https://github.com/neo-pragnya/ananke-plexus",
                        "rules": [
                            {
                                "id": r.test_id,
                                "name": f"{r.engine}.{r.kind}",
                                "shortDescription": {"text": r.message or r.test_id},
                            }
                            for r in run.results
                            if r.status not in (TestStatus.PASS, TestStatus.SKIPPED)
                        ],
                    }
                },
                "results": results,
                "automationDetails": {
                    "id": run.run_id,
                    "description": {"text": f"Quality run {run.run_id} profile={run.profile}"},
                },
                "versionControlProvenance": [],
                "properties": {
                    "profile": run.profile,
                    "suite_id": run.suite_id,
                    "started_at": run.started_at.astimezone(UTC).isoformat(),
                },
            }
        ],
    }
    return json.dumps(sarif, indent=2)

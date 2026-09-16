"""SARIF 2.1.0 output for security gate findings.

Spec ref: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)


def _level_for_severity(severity: str) -> str:
    mapping = {
        "critical": "error",
        "high": "error",
        "medium": "warning",
        "low": "note",
        "info": "none",
    }
    return mapping.get(severity.lower(), "warning")


def gate_outcomes_to_sarif(
    gate_outcomes: list[dict[str, Any]],
    tool_name: str = "ananke-plexus",
    tool_version: str = "0.1.0",
) -> dict[str, Any]:
    """Convert a list of GateOutcome dicts to a SARIF 2.1.0 document."""
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    rule_ids_seen: set[str] = set()

    for outcome in gate_outcomes:
        gate_id = str(outcome.get("gate_id", "unknown"))
        severity = str(outcome.get("severity", "medium"))
        status = str(outcome.get("status", "PASS"))
        summary = str(outcome.get("summary", ""))

        if gate_id not in rule_ids_seen:
            rule_ids_seen.add(gate_id)
            rules.append(
                {
                    "id": gate_id,
                    "name": gate_id.replace("-", "_").title().replace("_", ""),
                    "defaultConfiguration": {"level": _level_for_severity(severity)},
                    "shortDescription": {"text": f"Ananke gate: {gate_id}"},
                }
            )

        if status in ("BLOCKED", "ERROR"):
            results.append(
                {
                    "ruleId": gate_id,
                    "level": _level_for_severity(severity),
                    "message": {"text": summary},
                    "properties": {
                        "status": status,
                        "exit_code": outcome.get("exit_code"),
                        "started_at": outcome.get("started_at"),
                        "completed_at": outcome.get("completed_at"),
                    },
                }
            )

    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "informationUri": "https://github.com/neo-pragnya/ananke-plexus",
                        "rules": rules,
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": all(
                            str(o.get("status", "PASS")) not in ("BLOCKED", "ERROR")
                            for o in gate_outcomes
                        ),
                        "endTimeUtc": datetime.now(UTC).isoformat(),
                    }
                ],
            }
        ],
    }


def write_sarif(
    output_path: Path,
    gate_outcomes: list[dict[str, Any]],
    tool_name: str = "ananke-plexus",
    tool_version: str = "0.1.0",
) -> Path:
    """Write a SARIF file from gate outcomes. Returns the written path."""
    sarif = gate_outcomes_to_sarif(gate_outcomes, tool_name=tool_name, tool_version=tool_version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sarif, indent=2) + "\n", encoding="utf-8")
    return output_path

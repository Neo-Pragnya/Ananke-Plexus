"""Tests for SARIF 2.1.0 output (C10)."""

import json

from ananke.plexus.evidence.sarif import gate_outcomes_to_sarif, write_sarif


def _outcomes(statuses=None):
    statuses = statuses or ["PASS", "BLOCKED", "UNAVAILABLE"]
    return [
        {
            "gate_id": f"gate-{i}",
            "status": s,
            "severity": "high" if s == "BLOCKED" else "medium",
            "summary": f"Gate {i}: {s}",
            "exit_code": 0 if s == "PASS" else 1,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00",
        }
        for i, s in enumerate(statuses)
    ]


def test_sarif_schema_version():
    sarif = gate_outcomes_to_sarif(_outcomes())
    assert sarif["version"] == "2.1.0"
    assert "$schema" in sarif


def test_sarif_one_run():
    sarif = gate_outcomes_to_sarif(_outcomes())
    assert len(sarif["runs"]) == 1


def test_sarif_rules_deduped():
    outcomes = _outcomes() + _outcomes(["PASS"])
    sarif = gate_outcomes_to_sarif(outcomes)
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    rule_ids = [r["id"] for r in rules]
    assert len(rule_ids) == len(set(rule_ids))


def test_sarif_only_blocked_in_results():
    sarif = gate_outcomes_to_sarif(_outcomes(["PASS", "BLOCKED", "PASS"]))
    results = sarif["runs"][0]["results"]
    assert all(r["level"] in ("error", "warning", "note", "none") for r in results)
    # Only the BLOCKED outcome should produce a result
    assert len(results) == 1
    assert results[0]["ruleId"] == "gate-1"


def test_sarif_invocation_success():
    sarif = gate_outcomes_to_sarif(_outcomes(["PASS", "PASS"]))
    inv = sarif["runs"][0]["invocations"][0]
    assert inv["executionSuccessful"] is True


def test_sarif_invocation_failure():
    sarif = gate_outcomes_to_sarif(_outcomes(["BLOCKED"]))
    inv = sarif["runs"][0]["invocations"][0]
    assert inv["executionSuccessful"] is False


def test_write_sarif(tmp_path):
    out = tmp_path / "gates" / "sast.sarif"
    returned = write_sarif(out, _outcomes())
    assert returned == out
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"


def test_level_mapping():
    sarif = gate_outcomes_to_sarif(
        [{"gate_id": "x", "status": "BLOCKED", "severity": "critical", "summary": "oops"}]
    )
    results = sarif["runs"][0]["results"]
    assert results[0]["level"] == "error"

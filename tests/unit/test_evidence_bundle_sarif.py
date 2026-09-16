"""Integration tests for evidence bundle + SARIF embedding."""

import json

from ananke.plexus.evidence.bundle import create_evidence_bundle


def test_evidence_bundle_creates_sarif(tmp_path):
    outcomes = [
        {
            "gate_id": "ruff",
            "status": "PASS",
            "severity": "medium",
            "summary": "ruff passed",
            "exit_code": 0,
        },
        {
            "gate_id": "mypy",
            "status": "BLOCKED",
            "severity": "high",
            "summary": "mypy failed",
            "exit_code": 1,
        },
    ]
    bundle = create_evidence_bundle(
        tmp_path,
        gate_summary={"ruff": "PASS", "mypy": "BLOCKED"},
        gate_outcomes=outcomes,
        policy_decisions=[],
    )
    sarif_path = bundle / "gates" / "sast.sarif"
    assert sarif_path.exists()
    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"
    # mypy is BLOCKED so should appear in results
    results = data["runs"][0]["results"]
    assert any(r["ruleId"] == "mypy" for r in results)


def test_evidence_bundle_checksum_includes_sarif(tmp_path):
    bundle = create_evidence_bundle(
        tmp_path, gate_summary={}, gate_outcomes=[], policy_decisions=[]
    )
    checksums = (bundle / "checksums.sha256").read_text(encoding="utf-8")
    assert "sast.sarif" in checksums


def test_evidence_bundle_manifest_includes_sarif(tmp_path):
    bundle = create_evidence_bundle(
        tmp_path, gate_summary={}, gate_outcomes=[], policy_decisions=[]
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert "gates/sast.sarif" in manifest["files"]

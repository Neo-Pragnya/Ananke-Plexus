"""Tests for core result types (A1)."""

from ananke.plexus.core.result import CommandResult, Finding, GateResult


def test_command_result_ok():
    r = CommandResult(ok=True, summary="all good", details={"count": 5})
    assert r.ok is True
    assert r.details["count"] == 5


def test_gate_result_ok_statuses():
    for status in ("PASS", "WARN", "SKIPPED", "UNAVAILABLE"):
        r = GateResult(gate_id="g", status=status, severity="medium", summary="ok")
        assert r.ok is True


def test_gate_result_not_ok_statuses():
    for status in ("BLOCKED", "ERROR"):
        r = GateResult(gate_id="g", status=status, severity="high", summary="fail")
        assert r.ok is False


def test_gate_result_to_command_result():
    r = GateResult(
        gate_id="ruff",
        status="BLOCKED",
        severity="high",
        summary="lint failed",
        findings=[Finding(rule_id="E501", message="line too long")],
    )
    cmd = r.to_command_result()
    assert cmd.ok is False
    assert cmd.details["gate_id"] == "ruff"
    assert cmd.details["findings"] == 1


def test_finding_defaults():
    f = Finding()
    assert f.finding_id == ""
    assert f.severity == "medium"
    assert f.line is None

from pathlib import Path

from ananke.plexus.gates.runner import CommandGate, GateRunner


def test_command_gate_skips_when_targets_missing(tmp_path: Path) -> None:
    gate = CommandGate("ruff", "medium", ["ruff", "check", "src", "tests"], ["src", "tests"])
    outcome = gate.run(tmp_path)
    assert outcome.status == "SKIPPED"


def test_gate_runner_returns_contracts_for_local_full(tmp_path: Path) -> None:
    outcomes = GateRunner().run_local_full(tmp_path)
    gate_ids = {item.gate_id for item in outcomes}
    expected = {"ruff", "mypy", "pytest", "gitleaks", "semgrep", "pip-audit", "trivy", "license"}
    assert expected.issubset(gate_ids)

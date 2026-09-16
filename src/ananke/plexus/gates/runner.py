"""Verification gate runner."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class GateOutcome:
    gate_id: str
    status: str
    severity: str
    summary: str
    command: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    exit_code: int | None = None


@dataclass
class CommandGate:
    gate_id: str
    severity: str
    command: list[str]
    targets: list[str] = field(default_factory=list)

    def run(self, repository_root: Path) -> GateOutcome:
        started_at = datetime.now(UTC).isoformat()
        if self.targets and not any((repository_root / target).exists() for target in self.targets):
            return GateOutcome(
                gate_id=self.gate_id,
                status="SKIPPED",
                severity=self.severity,
                summary="No matching targets for gate.",
                command=" ".join(self.command),
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
            )

        executable = shutil.which(self.command[0])
        if not executable:
            return GateOutcome(
                gate_id=self.gate_id,
                status="UNAVAILABLE",
                severity=self.severity,
                summary=f"Executable not available: {self.command[0]}",
                command=" ".join(self.command),
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
            )

        command = [executable, *self.command[1:]]
        result = subprocess.run(  # noqa: S603 - static tool commands controlled by the application
            command,
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )
        completed_at = datetime.now(UTC).isoformat()
        status = "PASS" if result.returncode == 0 else "BLOCKED"
        tail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        summary = tail.splitlines()[-1][:160] if tail else "Gate completed."
        return GateOutcome(
            gate_id=self.gate_id,
            status=status,
            severity=self.severity,
            summary=summary,
            command=" ".join(command),
            started_at=started_at,
            completed_at=completed_at,
            exit_code=result.returncode,
        )


class GateRunner:
    def run_local_full(self, repository_root: Path) -> list[GateOutcome]:
        gates = [
            CommandGate("ruff", "medium", ["ruff", "check", "src", "tests"], ["src", "tests"]),
            CommandGate("mypy", "high", ["mypy", "src"], ["src"]),
            CommandGate("pytest", "high", ["pytest"], ["tests"]),
            CommandGate("gitleaks", "critical", ["gitleaks", "detect", "."], [".git"]),
            CommandGate(
                "semgrep",
                "high",
                ["semgrep", "scan", "--config", "auto", "src", "tests"],
                ["src", "tests"],
            ),
            CommandGate("pip-audit", "high", ["pip-audit"], ["pyproject.toml"]),
            CommandGate("trivy", "high", ["trivy", "fs", "."], ["pyproject.toml"]),
            CommandGate("license", "medium", ["pip-licenses", "--format=json"], ["pyproject.toml"]),
        ]
        return [gate.run(repository_root) for gate in gates]

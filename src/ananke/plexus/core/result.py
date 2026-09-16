"""Shared command and gate result models (A1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GateStatus = Literal["PASS", "WARN", "BLOCKED", "ERROR", "SKIPPED", "UNAVAILABLE"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class CommandResult(BaseModel):
    ok: bool
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    finding_id: str = ""
    rule_id: str = ""
    severity: Severity = "medium"
    message: str = ""
    file_path: str = ""
    line: int | None = None
    column: int | None = None


class GateResult(BaseModel):
    gate_id: str
    status: GateStatus
    severity: Severity
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    exit_code: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "WARN", "SKIPPED", "UNAVAILABLE")

    def to_command_result(self) -> CommandResult:
        return CommandResult(
            ok=self.ok,
            summary=self.summary,
            details={
                "gate_id": self.gate_id,
                "status": self.status,
                "severity": self.severity,
                "findings": len(self.findings),
            },
        )

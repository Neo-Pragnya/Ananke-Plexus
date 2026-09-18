"""SecurityFinding, ThreatModel, SecuritySeverity — security test models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SecuritySeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    NONE = "none"


class SecurityFinding(BaseModel):
    id: str
    title: str
    severity: SecuritySeverity
    description: str | None = None
    location: str | None = None
    cwe: str | None = None
    cve: str | None = None
    remediation: str | None = None
    tool: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ThreatModel(BaseModel):
    id: str
    component: str
    threats: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)
    residual_risk: SecuritySeverity = SecuritySeverity.LOW
    findings: list[SecurityFinding] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

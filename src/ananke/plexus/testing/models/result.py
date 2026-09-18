"""TestResult, TestRun, TestArtifact — run output models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ananke.plexus.testing.models.test import EvidenceRef, TestKind, TestStatus


class TestArtifact(BaseModel):
    name: str
    path: str
    media_type: str = "application/octet-stream"
    sha256: str | None = None
    size_bytes: int | None = None


class TestResult(BaseModel):
    test_id: str
    kind: TestKind
    engine: str
    status: TestStatus
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metrics: dict[str, float | int | str | bool] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    artifacts: list[TestArtifact] = Field(default_factory=list)
    stdout_path: str | None = None
    stderr_path: str | None = None
    seed: int | None = None
    tool_version: str | None = None
    message: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TestRun(BaseModel):
    run_id: str
    suite_id: str
    profile: str
    started_at: datetime
    ended_at: datetime | None = None
    results: list[TestResult] = Field(default_factory=list)
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)

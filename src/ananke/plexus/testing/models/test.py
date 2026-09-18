"""TestKind, TestStatus, TestDefinition, EvidenceRef — core test primitives."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TestKind(StrEnum):
    UNIT = "unit"
    INTEGRATION = "integration"
    ACCEPTANCE = "acceptance"
    BDD = "bdd"
    PROPERTY = "property"
    STATEFUL = "stateful"
    CONTRACT = "contract"
    MUTATION = "mutation"
    FUZZ = "fuzz"
    SNAPSHOT = "snapshot"
    FORMAL = "formal"
    CONCURRENCY = "concurrency"
    PERFORMANCE = "performance"
    SECURITY = "security"
    API_SCHEMA = "api_schema"
    COVERAGE = "coverage"
    COMPILE_FAIL = "compile_fail"
    AGENT_EVAL = "agent_eval"


class TestStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    WARN = "warn"
    UNAVAILABLE = "unavailable"


class EvidenceRef(BaseModel):
    kind: str
    uri: str
    sha256: str | None = None


class TestDefinition(BaseModel):
    id: str
    kind: TestKind
    engine: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    command: list[str] | None = None
    timeout_seconds: int | None = None
    required: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)

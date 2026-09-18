"""FuzzTarget, FuzzOutcome, FuzzFinding — fuzz test models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FuzzOutcome(StrEnum):
    NO_FINDING = "no_finding"
    CRASH = "crash"
    TIMEOUT = "timeout"
    OOM = "oom"
    ASSERTION = "assertion"
    SANITIZER = "sanitizer"


class FuzzFinding(BaseModel):
    target_id: str
    outcome: FuzzOutcome
    input_repr: str | None = None
    stack_trace: str | None = None
    discovered_at: datetime | None = None
    reproducible: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class FuzzTarget(BaseModel):
    id: str
    engine: str = "cargo-fuzz"
    harness: str
    duration_seconds: int = 60
    corpus_dir: str | None = None
    sanitizers: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)

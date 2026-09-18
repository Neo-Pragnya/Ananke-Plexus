"""Canonical agent trace model — runtime-neutral."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SpanKind(StrEnum):
    AGENT = "agent"
    MODEL = "model"
    PLANNING = "planning"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    SUBAGENT = "subagent"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    POLICY = "policy"
    FILESYSTEM = "filesystem"
    SHELL = "shell"


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    tool_calls: int = 0
    retries: int = 0


class AgentSpan(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    kind: SpanKind
    name: str
    started_at: datetime
    ended_at: datetime | None = None
    input: Any | None = None
    output: Any | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int | None:
        if self.ended_at is None:
            return None
        delta = self.ended_at - self.started_at
        return int(delta.total_seconds() * 1000)


class AgentTrace(BaseModel):
    trace_id: str
    run_id: str
    runtime: str
    model: str | None = None

    spans: list[AgentSpan] = Field(default_factory=list)
    final_output: Any | None = None
    usage: Usage = Field(default_factory=Usage)

    spec_hash: str | None = None
    architecture_hash: str | None = None
    policy_hash: str | None = None
    dataset_case_id: str | None = None

    def spans_by_kind(self, kind: SpanKind) -> list[AgentSpan]:
        return [s for s in self.spans if s.kind == kind]

    def tool_names(self) -> list[str]:
        return [s.name for s in self.spans if s.kind == SpanKind.TOOL]

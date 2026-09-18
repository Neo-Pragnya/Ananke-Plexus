"""Judge Protocol, JudgeResult, and governance envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ananke.plexus.evals.models.rubric import Rubric


class JudgeResult(BaseModel):
    judge_id: str
    provider: str
    model: str | None = None
    rubric_id: str
    rubric_version: str = "1"
    score: float
    normalized_score: float = Field(ge=0.0, le=1.0)
    passed: bool
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None
    prompt_hash: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    deterministic: bool = False
    timestamp: datetime | None = None
    raw_output: Any | None = None


class JudgeInputEnvelope(BaseModel):
    """Separates trusted rubric from untrusted agent content."""

    rubric: Rubric
    case_input: Any
    case_expected: Any | None = None
    agent_output: Any
    trace_summary: str = ""
    tool_outputs: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    def to_prompt(self) -> str:
        lines = [
            "=== TRUSTED RUBRIC ===",
            f"Title: {self.rubric.title}",
            f"Criteria: {[c.description for c in self.rubric.criteria]}",
            "",
            "=== TASK INPUT (untrusted) ===",
            str(self.case_input),
            "",
            "=== AGENT OUTPUT (untrusted) ===",
            str(self.agent_output),
        ]
        if self.tool_outputs:
            lines += ["", "=== TOOL OUTPUTS (untrusted) ===", *self.tool_outputs]
        return "\n".join(lines)


@runtime_checkable
class Judge(Protocol):
    id: str
    provider: str

    def score(
        self,
        *,
        envelope: JudgeInputEnvelope,
    ) -> JudgeResult: ...

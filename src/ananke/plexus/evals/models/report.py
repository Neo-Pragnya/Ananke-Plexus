"""EvalReport and EvalGateDecision — final outputs of an eval run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ananke.plexus.evals.models.score import EvalScore, EvalStatus


class GateVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class EvalGateDecision(BaseModel):
    verdict: GateVerdict
    suite_id: str
    run_id: str
    blocking_dimensions: list[str] = Field(default_factory=list)
    warning_dimensions: list[str] = Field(default_factory=list)
    review_dimensions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    decided_at: datetime | None = None

    @property
    def blocks(self) -> bool:
        return self.verdict == GateVerdict.BLOCK


class BaselineComparison(BaseModel):
    baseline_id: str
    regressions: dict[str, float] = Field(default_factory=dict)
    improvements: dict[str, float] = Field(default_factory=dict)
    stable: list[str] = Field(default_factory=list)
    regression_blocked: bool = False


class EvalReport(BaseModel):
    run_id: str
    suite_id: str
    case_id: str | None = None
    scores: list[EvalScore] = Field(default_factory=list)
    gate_decision: EvalGateDecision | None = None
    baseline_comparison: BaselineComparison | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def pass_rate(self) -> float:
        if not self.scores:
            return 0.0
        passed = sum(1 for s in self.scores if s.status == EvalStatus.PASS)
        return passed / len(self.scores)

    def scores_by_dimension(self, dimension: str) -> list[EvalScore]:
        return [s for s in self.scores if s.dimension == dimension]

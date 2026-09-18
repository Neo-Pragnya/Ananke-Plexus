"""EvalScore — the canonical output of every evaluator."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvalStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    REVIEW = "review"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


class EvalScore(BaseModel):
    evaluator_id: str
    evaluator_version: str = "1"

    dimension: str
    metric: str

    value: float | int | bool | str | list[Any] | None = None
    normalized_score: float | None = Field(default=None, ge=0.0, le=1.0)

    status: EvalStatus
    threshold: float | None = None

    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)

    deterministic: bool = True
    judge_model: str | None = None
    judge_prompt_hash: str | None = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status in (EvalStatus.PASS, EvalStatus.WARN)

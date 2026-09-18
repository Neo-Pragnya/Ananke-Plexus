"""Baseline — approved reference scores for regression comparison."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BaselineApproval(BaseModel):
    approved_by: str
    approved_at: datetime
    notes: str = ""


class Baseline(BaseModel):
    baseline_id: str
    suite_id: str
    version: str
    scores: dict[str, float] = Field(default_factory=dict)
    pass_rates: dict[str, float] = Field(default_factory=dict)
    usage_summary: dict[str, Any] = Field(default_factory=dict)
    approval: BaselineApproval | None = None
    created_at: datetime | None = None
    dataset_version: int | None = None
    commit_sha: str | None = None

    @property
    def approved(self) -> bool:
        return self.approval is not None

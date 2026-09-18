"""PerformanceBudget, PerformanceMetric, PerformanceReport — performance test models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MetricUnit(StrEnum):
    MS = "ms"
    SECONDS = "s"
    RPS = "rps"
    PERCENT = "percent"
    BYTES = "bytes"
    MB = "mb"
    COUNT = "count"


class PerformanceBudget(BaseModel):
    id: str
    metric: str
    max_value: float
    unit: MetricUnit = MetricUnit.MS
    percentile: float | None = None
    description: str | None = None


class PerformanceMetric(BaseModel):
    name: str
    value: float
    unit: MetricUnit
    percentile: float | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class PerformanceReport(BaseModel):
    run_id: str
    scenario: str
    started_at: datetime
    ended_at: datetime | None = None
    metrics: list[PerformanceMetric] = Field(default_factory=list)
    budgets_checked: list[PerformanceBudget] = Field(default_factory=list)
    budget_violations: list[str] = Field(default_factory=list)
    passed: bool = True

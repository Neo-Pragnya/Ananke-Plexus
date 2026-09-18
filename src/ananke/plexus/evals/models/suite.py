"""EvalSuite — collection of cases, evaluators, and gate policy."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvaluatorSpec(BaseModel):
    id: str
    engine: str = "ananke"
    optional: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class GatePolicyRule(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    required: bool = False
    on_failure: Literal["block", "warn", "review"] = "block"
    allow_regression: bool = True
    max_drop: float | None = None
    max_increase_percent: float | None = None


class GatePolicy(BaseModel):
    rules: dict[str, GatePolicyRule] = Field(default_factory=dict)

    def rule_for(self, dimension: str) -> GatePolicyRule | None:
        return self.rules.get(dimension)


class EvalSuite(BaseModel):
    id: str
    version: int = 1
    description: str = ""
    cases: list[str] = Field(default_factory=list)
    evaluators: list[EvaluatorSpec] = Field(default_factory=list)
    policy: GatePolicy = Field(default_factory=GatePolicy)
    profile: str = "standard"
    tags: list[str] = Field(default_factory=list)

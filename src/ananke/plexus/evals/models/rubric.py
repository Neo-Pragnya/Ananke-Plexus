"""Rubric — structured judge scoring criteria."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RubricCriterion(BaseModel):
    id: str
    description: str
    weight: float = 1.0
    required: bool = False
    scale_min: float = 0.0
    scale_max: float = 1.0


class Rubric(BaseModel):
    rubric_id: str
    version: str = "1"
    title: str = ""
    description: str = ""
    criteria: list[RubricCriterion] = Field(default_factory=list)
    prompt_template: str = ""
    scale_min: float = 0.0
    scale_max: float = 1.0
    pass_threshold: float = 0.7

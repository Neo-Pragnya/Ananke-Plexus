"""Specification models."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    requirement_id: str
    title: str
    body: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpecBundle(BaseModel):
    spec_id: str
    requirement_id: str
    state: str = "Specified"

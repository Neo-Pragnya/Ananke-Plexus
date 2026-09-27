"""Workflow and WorkflowStep models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

_MAX_STEPS = 200


class WorkflowStep(BaseModel):
    """One command to run. ``command`` is an argv list — never a shell string — so a step
    is exactly as safe as typing it by hand: no shell metacharacters, no injection surface."""

    command: list[str] = Field(min_length=1)
    description: str = ""
    continue_on_error: bool = False

    @field_validator("command")
    @classmethod
    def _no_blank_args(cls, value: list[str]) -> list[str]:
        if any(not arg.strip() for arg in value):
            raise ValueError("command arguments must be non-empty")
        return value

    @property
    def label(self) -> str:
        return self.description or " ".join(self.command)


class Workflow(BaseModel):
    name: str
    description: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=_MAX_STEPS)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not value or not all(c.isalnum() or c in "-_" for c in value):
            raise ValueError(
                f"invalid workflow name {value!r}: use letters, digits, '-' and '_' only"
            )
        return value

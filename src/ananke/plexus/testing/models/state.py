"""StateModel, Transition, StateInvariant — stateful test models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Transition(BaseModel):
    name: str
    from_state: str
    to_state: str
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    args: dict[str, Any] = Field(default_factory=dict)


class StateInvariant(BaseModel):
    id: str
    description: str
    expression: str


class StateModel(BaseModel):
    id: str
    states: list[str] = Field(default_factory=list)
    initial_state: str
    transitions: list[Transition] = Field(default_factory=list)
    invariants: list[StateInvariant] = Field(default_factory=list)
    engine: str = "hypothesis-stateful"

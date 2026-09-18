"""PropertyDefinition, PropertyPattern — property-based test models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PropertyPattern(StrEnum):
    ROUND_TRIP = "round_trip"
    INVARIANT = "invariant"
    ORACLE = "oracle"
    IDEMPOTENT = "idempotent"
    COMMUTATIVE = "commutative"
    ASSOCIATIVE = "associative"
    METAMORPHIC = "metamorphic"
    STATEFUL = "stateful"
    FUZZY = "fuzzy"


class PropertyDefinition(BaseModel):
    id: str
    description: str
    pattern: PropertyPattern
    engine: str = "hypothesis"
    settings: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

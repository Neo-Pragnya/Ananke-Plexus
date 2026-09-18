"""QualitySuite, QualityProfile — suite configuration models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ananke.plexus.testing.models.test import TestDefinition


class QualityProfile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    STRICT = "strict"
    VERIFICATION = "verification"
    RELEASE = "release"


class QualitySuite(BaseModel):
    id: str
    version: str = "1"
    tests: list[TestDefinition] = Field(default_factory=list)
    profile: QualityProfile = QualityProfile.STANDARD

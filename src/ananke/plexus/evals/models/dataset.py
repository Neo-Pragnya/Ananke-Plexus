"""EvalDataset — versioned, provenanced evaluation case collection."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ananke.plexus.evals.models.case import EvalCase


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET = "SECRET"


class DatasetProvenance(BaseModel):
    source: str = ""
    creator: str = ""
    created_at: datetime | None = None
    labeling_methodology: str = ""
    sensitivity: DataClassification = DataClassification.INTERNAL
    retention_policy: str = ""
    allowed_environments: list[str] = Field(default_factory=list)
    case_hashes: dict[str, str] = Field(default_factory=dict)
    approval_state: str = "draft"


class EvalDataset(BaseModel):
    id: str
    version: int = 1
    description: str = ""
    dataset_class: str = "regression"
    cases: list[EvalCase] = Field(default_factory=list)
    provenance: DatasetProvenance = Field(default_factory=DatasetProvenance)
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def case_by_id(self, case_id: str) -> EvalCase | None:
        for c in self.cases:
            if c.id == case_id:
                return c
        return None

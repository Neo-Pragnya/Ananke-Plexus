"""Domain event models for the Ananke Plexus event system."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str
    run_id: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)
    causation_id: str | None = None


# Well-known event type constants
REQUIREMENT_CAPTURED = "RequirementCaptured"
SPEC_CREATED = "SpecCreated"
SPEC_LOCKED = "SpecLocked"
ARCHITECTURE_VALIDATED = "ArchitectureValidated"
GRAPH_UPDATED = "GraphUpdated"
IMPACT_CALCULATED = "ImpactCalculated"
POLICY_EVALUATED = "PolicyEvaluated"
GATE_COMPLETED = "GateCompleted"
BACKEND_INVOKED = "BackendInvoked"
FILE_MUTATED = "FileMutated"
COMMIT_CREATED = "CommitCreated"
PUSH_COMPLETED = "PushCompleted"
PULL_REQUEST_CREATED = "PullRequestCreated"
ISSUE_TRANSITIONED = "IssueTransitioned"
EVIDENCE_FINALIZED = "EvidenceFinalized"
RUN_STARTED = "RunStarted"
RUN_CANCELLED = "RunCancelled"
RUN_SUCCEEDED = "RunSucceeded"
RUN_FAILED = "RunFailed"
HOOK_EXECUTED = "HookExecuted"
APPROVAL_REQUESTED = "ApprovalRequested"
APPROVAL_GRANTED = "ApprovalGranted"
APPROVAL_DENIED = "ApprovalDenied"

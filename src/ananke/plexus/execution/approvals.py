"""Human approval tracking for run engine steps (L4).

Approval classes map to the categories in Section 19.3:
  NONE, PLAN, FILE_WRITE, SHELL, NETWORK, GIT_PUSH, PR_CREATE,
  ISSUE_TRANSITION, RELEASE

Enterprise policy can increase but not silently decrease required approvals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ApprovalClass = Literal[
    "NONE",
    "PLAN",
    "FILE_WRITE",
    "SHELL",
    "NETWORK",
    "GIT_PUSH",
    "PR_CREATE",
    "ISSUE_TRANSITION",
    "RELEASE",
]

ApprovalStatus = Literal["PENDING", "GRANTED", "DENIED", "EXPIRED"]


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    step_id: str
    approval_class: ApprovalClass
    description: str
    requested_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: ApprovalStatus = "PENDING"
    resolved_at: str | None = None
    resolved_by: str | None = None
    notes: str = ""


def _approvals_path(repository_root: Path, run_id: str) -> Path:
    return repository_root / ".ananke" / "runs" / run_id / "approvals.jsonl"


def request_approval(
    repository_root: Path,
    run_id: str,
    step_id: str,
    approval_class: ApprovalClass,
    description: str,
) -> ApprovalRequest:
    req = ApprovalRequest(
        run_id=run_id,
        step_id=step_id,
        approval_class=approval_class,
        description=description,
    )
    path = _approvals_path(repository_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(req.model_dump_json() + "\n")
    return req


def resolve_approval(
    repository_root: Path,
    run_id: str,
    approval_id: str,
    *,
    grant: bool,
    resolved_by: str = "cli",
    notes: str = "",
) -> ApprovalRequest | None:
    path = _approvals_path(repository_root, run_id)
    if not path.exists():
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    found: ApprovalRequest | None = None

    for line in lines:
        if not line.strip():
            continue
        req = ApprovalRequest.model_validate_json(line)
        if req.approval_id == approval_id and req.status == "PENDING":
            req.status = "GRANTED" if grant else "DENIED"
            req.resolved_at = datetime.now(UTC).isoformat()
            req.resolved_by = resolved_by
            req.notes = notes
            found = req
        updated.append(req.model_dump_json())

    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return found


def list_approvals(
    repository_root: Path,
    run_id: str,
    *,
    status: ApprovalStatus | None = None,
) -> list[ApprovalRequest]:
    path = _approvals_path(repository_root, run_id)
    if not path.exists():
        return []
    results: list[ApprovalRequest] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        req = ApprovalRequest.model_validate_json(line)
        if status is None or req.status == status:
            results.append(req)
    return results


def is_approved(
    repository_root: Path,
    run_id: str,
    step_id: str,
    approval_class: ApprovalClass,
) -> bool:
    """Return True if at least one GRANTED approval exists for this step+class."""
    for req in list_approvals(repository_root, run_id):
        if (
            req.step_id == step_id
            and req.approval_class == approval_class
            and req.status == "GRANTED"
        ):
            return True
    return False

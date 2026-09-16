"""Lifecycle compensation engine (J7).

When a step partially succeeds (e.g. branch created but Jira transition failed),
this module records the compensation plan and optional execution path.

Spec ref: Section 19.6 / Section 43.3.

Design principle: compensation preserves all evidence produced so far.
Never delete user work.  Restore only what is clearly Ananke-owned state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

CompensationAction = Literal[
    "retain_branch",
    "restore_issue_state",
    "retry_evidence_comment",
    "mark_run_partial",
    "noop",
]


class CompensationStep(BaseModel):
    step_id: str = Field(default_factory=lambda: uuid4().hex)
    action: CompensationAction
    description: str
    target: str = ""
    completed: bool = False
    completed_at: str | None = None
    notes: str = ""


class CompensationPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    trigger: str = ""
    steps: list[CompensationStep] = Field(default_factory=list)
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"] = "PENDING"


def _plan_path(repository_root: Path, run_id: str) -> Path:
    return repository_root / ".ananke" / "runs" / run_id / "compensation.json"


def create_compensation_plan(
    repository_root: Path,
    run_id: str,
    trigger: str,
    steps: list[tuple[CompensationAction, str, str]],
) -> CompensationPlan:
    """Create and persist a compensation plan.

    ``steps`` is a list of (action, description, target) tuples.
    """
    plan = CompensationPlan(
        run_id=run_id,
        trigger=trigger,
        steps=[CompensationStep(action=a, description=d, target=t) for a, d, t in steps],
    )
    path = _plan_path(repository_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan


def load_compensation_plan(repository_root: Path, run_id: str) -> CompensationPlan | None:
    path = _plan_path(repository_root, run_id)
    if not path.exists():
        return None
    return CompensationPlan.model_validate_json(path.read_text(encoding="utf-8"))


def execute_compensation(
    repository_root: Path,
    run_id: str,
) -> CompensationPlan | None:
    """Execute all pending compensation steps.

    Current implementation marks steps completed; integration with
    Jira/Bitbucket adapters is done when they are initialised.
    """
    plan = load_compensation_plan(repository_root, run_id)
    if plan is None:
        return None

    plan.status = "IN_PROGRESS"
    for step in plan.steps:
        if step.completed:
            continue
        _execute_step(repository_root, step)
        step.completed = True
        step.completed_at = datetime.now(UTC).isoformat()

    plan.status = "COMPLETED"
    path = _plan_path(repository_root, run_id)
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan


def _execute_step(repository_root: Path, step: CompensationStep) -> None:
    if step.action == "retain_branch":
        step.notes = f"branch {step.target} retained (no action required)"
    elif step.action == "restore_issue_state":
        step.notes = (
            f"issue state restoration for {step.target} is advisory; manual action may be needed"
        )
    elif step.action == "retry_evidence_comment":
        step.notes = f"evidence comment retry for {step.target} recorded for next run"
    elif step.action == "mark_run_partial":
        _mark_run_partial(repository_root, step.target)
        step.notes = "run marked partial"
    else:
        step.notes = "noop"


def _mark_run_partial(repository_root: Path, run_id: str) -> None:
    state_path = repository_root / ".ananke" / "runs" / run_id / "state.json"
    if not state_path.exists():
        return
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload["state"] = "FAILED"
        payload["partial"] = True
        payload["updated_at"] = datetime.now(UTC).isoformat()
        state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

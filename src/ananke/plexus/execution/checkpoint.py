"""Execution checkpointing (L3).

The run engine writes a checkpoint after every side effect.
A crash after creating a Jira comment but before recording local state
is recoverable using the idempotency key.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.execution.plan import ExecutionPlan


def _checkpoint_path(repository_root: Path, run_id: str) -> Path:
    return repository_root / ".ananke" / "runs" / run_id / "checkpoint.json"


def save_checkpoint(repository_root: Path, run_id: str, plan: ExecutionPlan) -> Path:
    path = _checkpoint_path(repository_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "checkpointed_at": datetime.now(UTC).isoformat(),
        "plan_state": plan.state,
        "steps": [
            {
                "step_id": s.step_id,
                "step_type": s.step_type,
                "state": s.state,
                "error": s.error,
            }
            for s in plan.steps
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_checkpoint(repository_root: Path, run_id: str) -> dict[str, object] | None:
    path = _checkpoint_path(repository_root, run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]


def resume_from_checkpoint(
    repository_root: Path,
    run_id: str,
    plan: ExecutionPlan,
) -> ExecutionPlan:
    """Restore step states from a saved checkpoint without re-running completed steps."""
    checkpoint = load_checkpoint(repository_root, run_id)
    if checkpoint is None:
        return plan

    step_states: dict[str, str] = {
        str(s.get("step_id", "")): str(s.get("state", "PENDING"))
        for s in (checkpoint.get("steps") or [])
        if isinstance(s, dict)
    }
    for step in plan.steps:
        if step.step_id in step_states:
            restored = step_states[step.step_id]
            if restored in {"SUCCEEDED", "SKIPPED", "COMPENSATED"}:
                step.state = restored  # type: ignore[assignment]
    return plan

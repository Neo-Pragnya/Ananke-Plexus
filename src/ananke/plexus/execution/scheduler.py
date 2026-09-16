"""DAG execution scheduler (L2/L7).

Executes an ExecutionPlan step by step, respecting dependency order.
Writes checkpoints after each step.  Supports concurrent independent steps
via Python threading for L7 concurrency.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.execution.checkpoint import save_checkpoint
from ananke.plexus.execution.plan import ExecutionPlan, ExecutionStep

StepHandler = Callable[[ExecutionStep, Path], dict[str, object]]


def _noop_handler(step: ExecutionStep, repository_root: Path) -> dict[str, object]:
    return {"status": "SUCCEEDED", "note": f"no handler for {step.step_type}"}


def run_plan(
    repository_root: Path,
    plan: ExecutionPlan,
    *,
    handlers: dict[str, StepHandler] | None = None,
    max_workers: int = 4,
    dry_run: bool = False,
) -> ExecutionPlan:
    """Execute the plan DAG, respecting dependencies and concurrency (L7).

    Completed steps are checkpointed immediately.
    Steps that share no dependency edges can run in parallel up to ``max_workers``.
    """
    _handlers = handlers or {}

    plan.state = "RUNNING"
    plan.updated_at = datetime.now(UTC).isoformat()

    max_iterations = len(plan.steps) * 2 + 1
    for _ in range(max_iterations):
        ready = plan.ready_steps()
        if not ready:
            break
        if dry_run:
            for step in ready:
                step.state = "SUCCEEDED"
            continue

        if len(ready) == 1:
            _execute_step(ready[0], repository_root, _handlers)
        else:
            _execute_concurrent(ready, repository_root, _handlers, max_workers=max_workers)

        save_checkpoint(repository_root, plan.run_id, plan)

        if any(s.state == "BLOCKED" for s in plan.steps):
            plan.state = "BLOCKED"
            break

    if plan.is_complete() and plan.state not in {"BLOCKED", "CANCELLED"}:
        has_failed = any(s.state == "FAILED" for s in plan.steps)
        plan.state = "FAILED" if has_failed else "SUCCEEDED"

    plan.updated_at = datetime.now(UTC).isoformat()
    save_checkpoint(repository_root, plan.run_id, plan)
    return plan


def _execute_step(
    step: ExecutionStep,
    repository_root: Path,
    handlers: dict[str, StepHandler],
) -> None:
    handler = handlers.get(step.step_type, _noop_handler)
    step.state = "RUNNING"
    step.started_at = datetime.now(UTC).isoformat()
    try:
        result = handler(step, repository_root)
        step.result = result
        new_state = str(result.get("status", "SUCCEEDED"))
        if new_state not in {
            "PENDING",
            "READY",
            "RUNNING",
            "SUCCEEDED",
            "FAILED",
            "BLOCKED",
            "SKIPPED",
            "COMPENSATING",
            "COMPENSATED",
            "CANCELLED",
        }:
            new_state = "SUCCEEDED"
        step.state = new_state  # type: ignore[assignment]
    except Exception as exc:
        step.state = "FAILED"
        step.error = str(exc)[:500]
    finally:
        step.completed_at = datetime.now(UTC).isoformat()


def _execute_concurrent(
    steps: list[ExecutionStep],
    repository_root: Path,
    handlers: dict[str, StepHandler],
    *,
    max_workers: int,
) -> None:
    with ThreadPoolExecutor(max_workers=min(max_workers, len(steps))) as pool:
        futures = {
            pool.submit(_execute_step, step, repository_root, handlers): step for step in steps
        }
        for future in as_completed(futures):
            future.result()  # re-raise any unexpected exceptions

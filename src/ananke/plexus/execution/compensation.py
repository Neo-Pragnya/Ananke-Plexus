"""Run engine compensation (L8) — delegates to lifecycle compensation (J7)."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.execution.plan import ExecutionPlan
from ananke.plexus.lifecycle.compensation import (
    CompensationAction,
    CompensationPlan,
    create_compensation_plan,
    execute_compensation,
)


def compensate_plan(
    repository_root: Path,
    plan: ExecutionPlan,
) -> CompensationPlan | None:
    """Build and execute a compensation plan for a failed/blocked execution plan."""
    steps: list[tuple[CompensationAction, str, str]] = []

    for step in plan.steps:
        if step.state not in {"SUCCEEDED", "RUNNING", "COMPENSATING"}:
            continue
        if step.step_type == "create_branch":
            branch = str(step.result.get("branch", ""))
            if branch:
                steps.append(("retain_branch", f"retain branch created in {step.step_id}", branch))

        elif step.step_type == "transition_issue":
            issue = str(step.params.get("issue_key", ""))
            if issue:
                steps.append(
                    ("restore_issue_state", f"restore issue {issue} to prior state", issue)
                )

        elif step.step_type == "post_evidence":
            steps.append(
                (
                    "retry_evidence_comment",
                    f"retry evidence comment for {step.step_id}",
                    step.step_id,
                )
            )

    steps.append(("mark_run_partial", f"mark run {plan.run_id} as partial", plan.run_id))

    if not steps:
        return None

    trigger = (
        f"plan {plan.plan_id} compensation triggered — "
        f"failed_steps={sum(1 for s in plan.steps if s.state == 'FAILED')}"
    )

    create_compensation_plan(repository_root, plan.run_id, trigger, steps)
    return execute_compensation(repository_root, plan.run_id)

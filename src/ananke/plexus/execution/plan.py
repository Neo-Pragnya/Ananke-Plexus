"""Execution plan model (L1).

An ExecutionPlan is a DAG of ExecutionSteps with approval requirements.
The scheduler advances the plan and writes checkpoints after each step.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ananke.plexus.execution.approvals import ApprovalClass

StepType = Literal[
    "read_context",
    "create_worktree",
    "create_branch",
    "transition_issue",
    "generate_contracts",
    "invoke_backend",
    "run_gate",
    "update_graph",
    "render_architecture",
    "commit",
    "push",
    "create_pr",
    "post_evidence",
    "request_approval",
    "compensate",
]

StepState = Literal[
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
]


class ExecutionStep(BaseModel):
    step_id: str
    step_type: StepType | str
    state: StepState = "PENDING"
    depends_on: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    completed_at: str = ""


class ApprovalRequirement(BaseModel):
    step_id: str
    approval_class: ApprovalClass
    description: str


class ExecutionPlan(BaseModel):
    plan_id: str
    spec_id: str
    run_id: str = ""
    steps: list[ExecutionStep] = Field(default_factory=list)
    approvals: list[ApprovalRequirement] = Field(default_factory=list)
    compensation_strategy: Literal["retain_and_report", "full_rollback", "partial"] = (
        "retain_and_report"
    )
    state: StepState = "PENDING"
    created_at: str = ""
    updated_at: str = ""

    def ready_steps(self) -> list[ExecutionStep]:
        """Steps whose dependencies are all SUCCEEDED and are themselves PENDING."""
        succeeded = {s.step_id for s in self.steps if s.state == "SUCCEEDED"}
        return [
            s
            for s in self.steps
            if s.state == "PENDING" and all(dep in succeeded for dep in s.depends_on)
        ]

    def is_complete(self) -> bool:
        terminal = {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "COMPENSATED", "SKIPPED"}
        return all(s.state in terminal for s in self.steps)


def default_plan(spec_id: str, run_id: str = "") -> ExecutionPlan:
    """Produce the standard ADLC execution plan steps."""
    from ananke.plexus.core.ids import new_id

    steps = [
        ExecutionStep(step_id=new_id("step"), step_type="read_context"),
        ExecutionStep(step_id=new_id("step"), step_type="create_worktree", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="create_branch", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="generate_contracts", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="invoke_backend", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="run_gate", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="update_graph", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="render_architecture", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="commit", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="push", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="create_pr", depends_on=[]),
        ExecutionStep(step_id=new_id("step"), step_type="post_evidence", depends_on=[]),
    ]
    approvals = [
        ApprovalRequirement(
            step_id=steps[8].step_id, approval_class="GIT_PUSH", description="Approve commit+push"
        ),
        ApprovalRequirement(
            step_id=steps[10].step_id, approval_class="PR_CREATE", description="Approve PR creation"
        ),
    ]
    from ananke.plexus.core.ids import new_id as _id

    return ExecutionPlan(
        plan_id=_id("plan"),
        spec_id=spec_id,
        run_id=run_id,
        steps=steps,
        approvals=approvals,
    )

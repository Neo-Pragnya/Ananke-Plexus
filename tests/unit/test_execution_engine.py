"""Tests for execution plan, checkpoint, scheduler, approvals (L1-L4/L7)."""

from ananke.plexus.execution.approvals import (
    is_approved,
    list_approvals,
    request_approval,
    resolve_approval,
)
from ananke.plexus.execution.checkpoint import (
    load_checkpoint,
    resume_from_checkpoint,
    save_checkpoint,
)
from ananke.plexus.execution.plan import ExecutionPlan, ExecutionStep, default_plan
from ananke.plexus.execution.scheduler import run_plan

# ---------- Plan ----------


def test_default_plan_has_steps():
    plan = default_plan("SPEC-1", run_id="run-1")
    assert len(plan.steps) > 0
    assert plan.plan_id.startswith("plan-")


def test_ready_steps_with_no_deps():
    plan = ExecutionPlan(
        plan_id="p1",
        spec_id="s1",
        steps=[
            ExecutionStep(step_id="a", step_type="read_context"),
            ExecutionStep(step_id="b", step_type="run_gate"),
        ],
    )
    ready = plan.ready_steps()
    assert len(ready) == 2


def test_ready_steps_respects_deps():
    plan = ExecutionPlan(
        plan_id="p1",
        spec_id="s1",
        steps=[
            ExecutionStep(step_id="a", step_type="read_context"),
            ExecutionStep(step_id="b", step_type="run_gate", depends_on=["a"]),
        ],
    )
    ready = plan.ready_steps()
    assert len(ready) == 1
    assert ready[0].step_id == "a"


def test_is_complete_when_all_terminal():
    plan = ExecutionPlan(
        plan_id="p1",
        spec_id="s1",
        steps=[
            ExecutionStep(step_id="a", step_type="read_context", state="SUCCEEDED"),
            ExecutionStep(step_id="b", step_type="run_gate", state="SKIPPED"),
        ],
    )
    assert plan.is_complete() is True


# ---------- Scheduler ----------


def test_scheduler_dry_run(tmp_path):
    plan = default_plan("SPEC-DRY", run_id="run-dry")
    plan.run_id = "run-dry"
    result = run_plan(tmp_path, plan, dry_run=True)
    assert all(s.state == "SUCCEEDED" for s in result.steps)


def test_scheduler_with_handler(tmp_path):
    from ananke.plexus.execution.plan import ExecutionStep

    plan = ExecutionPlan(
        plan_id="p1",
        spec_id="s1",
        run_id="run-h",
        steps=[
            ExecutionStep(step_id="s1", step_type="read_context"),
            ExecutionStep(step_id="s2", step_type="run_gate"),
        ],
    )

    def noop(step, root):
        return {"status": "SUCCEEDED"}

    result = run_plan(tmp_path, plan, handlers={"read_context": noop, "run_gate": noop})
    assert all(s.state == "SUCCEEDED" for s in result.steps)


# ---------- Checkpoint ----------


def test_checkpoint_save_load(tmp_path):
    plan = default_plan("SPEC-CP", run_id="run-cp")
    plan.run_id = "run-cp"
    save_checkpoint(tmp_path, "run-cp", plan)
    ckpt = load_checkpoint(tmp_path, "run-cp")
    assert ckpt is not None
    assert ckpt["run_id"] == "run-cp"


def test_resume_from_checkpoint(tmp_path):
    plan = default_plan("SPEC-RESUME", run_id="run-r")
    plan.run_id = "run-r"
    # Mark first step succeeded
    plan.steps[0].state = "SUCCEEDED"
    save_checkpoint(tmp_path, "run-r", plan)

    # Reload the same plan (same step IDs) and resume
    plan.steps[0].state = "PENDING"  # reset to test resume
    resumed = resume_from_checkpoint(tmp_path, "run-r", plan)
    assert resumed.steps[0].state == "SUCCEEDED"


# ---------- Approvals ----------


def test_request_approval(tmp_path):
    req = request_approval(tmp_path, "run-1", "step-1", "GIT_PUSH", "Approve push")
    assert req.status == "PENDING"
    assert req.approval_class == "GIT_PUSH"


def test_grant_approval(tmp_path):
    req = request_approval(tmp_path, "run-2", "step-2", "PR_CREATE", "Create PR")
    resolved = resolve_approval(
        tmp_path, "run-2", req.approval_id, grant=True, resolved_by="tester"
    )
    assert resolved is not None
    assert resolved.status == "GRANTED"


def test_deny_approval(tmp_path):
    req = request_approval(tmp_path, "run-3", "step-3", "RELEASE", "Release approval")
    resolved = resolve_approval(tmp_path, "run-3", req.approval_id, grant=False)
    assert resolved is not None
    assert resolved.status == "DENIED"


def test_list_approvals_pending(tmp_path):
    request_approval(tmp_path, "run-4", "s1", "PLAN", "approve plan")
    request_approval(tmp_path, "run-4", "s2", "SHELL", "approve shell")
    pending = list_approvals(tmp_path, "run-4", status="PENDING")
    assert len(pending) == 2


def test_is_approved_false_until_granted(tmp_path):
    req = request_approval(tmp_path, "run-5", "s1", "GIT_PUSH", "approve push")
    assert is_approved(tmp_path, "run-5", "s1", "GIT_PUSH") is False
    resolve_approval(tmp_path, "run-5", req.approval_id, grant=True)
    assert is_approved(tmp_path, "run-5", "s1", "GIT_PUSH") is True

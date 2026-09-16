"""Tests for run engine compensation (L8)."""

from ananke.plexus.execution.compensation import compensate_plan
from ananke.plexus.execution.plan import ExecutionPlan, ExecutionStep


def _plan_with_succeeded_branch(run_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="p1",
        spec_id="s1",
        run_id=run_id,
        steps=[
            ExecutionStep(
                step_id="s1",
                step_type="create_branch",
                state="SUCCEEDED",
                result={"branch": "feature/PROJ-1"},
            ),
            ExecutionStep(step_id="s2", step_type="transition_issue", state="FAILED"),
        ],
    )


def test_compensate_plan_creates_plan(tmp_path):
    plan = _plan_with_succeeded_branch("run-comp-1")
    result = compensate_plan(tmp_path, plan)
    assert result is not None
    assert result.status == "COMPLETED"


def test_compensate_plan_retain_branch(tmp_path):
    plan = _plan_with_succeeded_branch("run-comp-2")
    result = compensate_plan(tmp_path, plan)
    assert result is not None
    retain_steps = [s for s in result.steps if s.action == "retain_branch"]
    assert len(retain_steps) >= 1
    assert "feature/PROJ-1" in retain_steps[0].target


def test_compensate_plan_always_has_mark_partial(tmp_path):
    plan = ExecutionPlan(
        plan_id="p2",
        spec_id="s2",
        run_id="run-empty",
        steps=[],
    )
    result = compensate_plan(tmp_path, plan)
    # even with no side-effecting steps, mark_run_partial is always added
    assert result is not None
    assert any(s.action == "mark_run_partial" for s in result.steps)


def test_compensate_plan_pending_steps_only(tmp_path):
    plan = ExecutionPlan(
        plan_id="p3",
        spec_id="s3",
        run_id="run-pending",
        steps=[
            ExecutionStep(step_id="a", step_type="read_context", state="PENDING"),
        ],
    )
    result = compensate_plan(tmp_path, plan)
    # mark_run_partial is always present
    assert result is not None
    assert result.status == "COMPLETED"

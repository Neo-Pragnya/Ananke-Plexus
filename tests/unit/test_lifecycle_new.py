"""Tests for new lifecycle modules — git service, worktree, compensation, PR evidence (J1/J2/J5/J7)."""

from ananke.plexus.lifecycle.compensation import (
    create_compensation_plan,
    execute_compensation,
    load_compensation_plan,
)
from ananke.plexus.lifecycle.git import make_branch_name
from ananke.plexus.lifecycle.pr_evidence import generate_pr_body
from ananke.plexus.lifecycle.worktree import create_isolated_worktree, list_worktrees

# ---------- Git service ----------


def test_make_branch_name():
    name = make_branch_name("feature", "PROJ-101", "add payment webhook")
    assert name == "feature/PROJ-101-add-payment-webhook"


def test_make_branch_name_slug_lowercased():
    name = make_branch_name("bugfix", "API-42", "Fix Auth Bug")
    assert name == "bugfix/API-42-fix-auth-bug"


# ---------- Worktree ----------


def test_create_isolated_worktree_plain(tmp_path):
    # In test env no git repo, falls back to plain directory
    worktree = create_isolated_worktree(tmp_path, "run-abc", use_git_worktree=False)
    assert worktree.exists()
    assert worktree.is_dir()


def test_list_worktrees_empty(tmp_path):
    assert list_worktrees(tmp_path) == []


def test_list_worktrees_after_create(tmp_path):
    create_isolated_worktree(tmp_path, "run-001", use_git_worktree=False)
    create_isolated_worktree(tmp_path, "run-002", use_git_worktree=False)
    worktrees = list_worktrees(tmp_path)
    assert len(worktrees) == 2


# ---------- Compensation ----------


def test_create_compensation_plan(tmp_path):
    plan = create_compensation_plan(
        tmp_path,
        "run-comp-1",
        trigger="test trigger",
        steps=[
            ("retain_branch", "keep the branch", "feature/test"),
            ("mark_run_partial", "mark partial", "run-comp-1"),
        ],
    )
    assert plan.run_id == "run-comp-1"
    assert len(plan.steps) == 2
    assert plan.steps[0].action == "retain_branch"


def test_load_compensation_plan(tmp_path):
    create_compensation_plan(tmp_path, "run-load", "trigger", [("noop", "do nothing", "")])
    loaded = load_compensation_plan(tmp_path, "run-load")
    assert loaded is not None
    assert loaded.run_id == "run-load"


def test_load_compensation_plan_missing(tmp_path):
    result = load_compensation_plan(tmp_path, "not-exists")
    assert result is None


def test_execute_compensation_completes(tmp_path):
    create_compensation_plan(
        tmp_path,
        "run-exec",
        "trigger",
        [("retain_branch", "keep branch", "feat/x"), ("noop", "nothing", "")],
    )
    plan = execute_compensation(tmp_path, "run-exec")
    assert plan is not None
    assert plan.status == "COMPLETED"
    assert all(s.completed for s in plan.steps)


# ---------- PR Evidence ----------


def test_generate_pr_body_minimal(tmp_path):
    body = generate_pr_body(tmp_path)
    assert "## Requirement" in body
    assert "## Verification Evidence" in body
    assert "## Traceability" in body


def test_generate_pr_body_with_spec(tmp_path):
    spec_dir = tmp_path / ".ananke" / "specs" / "DEMO-101"
    spec_dir.mkdir(parents=True)
    req_file = spec_dir / "requirement.md"
    req_file.write_text(
        "# Add idempotent webhook\n\n## Acceptance Criteria\n- Events deduplicated\n",
        encoding="utf-8",
    )
    body = generate_pr_body(tmp_path, spec_id="DEMO-101")
    assert "Add idempotent webhook" in body
    assert "Events deduplicated" in body


def test_generate_pr_body_with_run(tmp_path):
    run_dir = tmp_path / ".ananke" / "evidence" / "20260916T000000Z-abc12345"
    run_dir.mkdir(parents=True)
    import json

    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "20260916T000000Z-abc12345", "files": {"gates/ruff.json": "hash"}}),
        encoding="utf-8",
    )
    body = generate_pr_body(tmp_path, run_id="20260916T000000Z-abc12345")
    assert "20260916T000000Z-abc12345" in body

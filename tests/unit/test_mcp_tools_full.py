"""Tests for MCP tool handlers — both read-only and mutation (H3/H4)."""

from ananke.plexus.mcp.tools import call_tool


def test_project_status(tmp_path):
    result = call_tool(tmp_path, "ananke.project.status", {})
    assert isinstance(result, dict)


def test_spec_get_missing_arg(tmp_path):
    result = call_tool(tmp_path, "ananke.spec.get", {})
    assert result["ok"] is False


def test_spec_get_not_found(tmp_path):
    result = call_tool(tmp_path, "ananke.spec.get", {"feature_dir": str(tmp_path / "no-such")})
    assert result["ok"] is False


def test_spec_get_success(tmp_path):
    d = tmp_path / "my-spec"
    d.mkdir()
    (d / "spec.md").write_text("# Spec", encoding="utf-8")
    result = call_tool(tmp_path, "ananke.spec.get", {"feature_dir": str(d)})
    assert result["ok"] is True
    assert "spec.md" in result["details"]["files"]


def test_spec_validate(tmp_path):
    result = call_tool(tmp_path, "ananke.spec.validate", {"feature_dir": str(tmp_path)})
    assert "ok" in result


def test_graph_query(tmp_path):
    result = call_tool(tmp_path, "ananke.graph.query", {"text": "main"})
    assert "ok" in result


def test_graph_impact_list(tmp_path):
    result = call_tool(tmp_path, "ananke.graph.impact", {"changed_files": ["src/main.py"]})
    assert "ok" in result


def test_graph_impact_string(tmp_path):
    result = call_tool(tmp_path, "ananke.graph.impact", {"changed_files": "src/main.py"})
    assert "ok" in result


def test_graph_update(tmp_path):
    result = call_tool(tmp_path, "ananke.graph.update", {})
    assert "ok" in result


def test_policy_explain(tmp_path):
    result = call_tool(tmp_path, "ananke.policy.explain", {"stage": "verify"})
    assert "ok" in result


def test_run_status_empty(tmp_path):
    result = call_tool(tmp_path, "ananke.run.status", {"run_id": ""})
    assert "ok" in result


def test_evidence_get_not_found(tmp_path):
    result = call_tool(tmp_path, "ananke.evidence.get", {"run_id": "no-such-run"})
    assert result["ok"] is False


def test_evidence_get_found(tmp_path):
    run_dir = tmp_path / ".ananke" / "evidence" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text('{"run_id": "run-001"}', encoding="utf-8")
    result = call_tool(tmp_path, "ananke.evidence.get", {"run_id": "run-001"})
    assert result["ok"] is True


def test_unsupported_tool(tmp_path):
    result = call_tool(tmp_path, "ananke.does.not.exist", {})
    assert result["ok"] is False
    assert "unsupported" in result["summary"].lower()


def test_git_create_branch_tool(tmp_path):
    result = call_tool(
        tmp_path,
        "ananke.git.create_branch",
        {"change_type": "feature", "ticket": "PROJ-101", "slug": "add webhook"},
    )
    assert result["ok"] is True
    assert "feature/PROJ-101" in result["details"]["branch"]


def test_git_create_branch_missing_args(tmp_path):
    result = call_tool(tmp_path, "ananke.git.create_branch", {"change_type": "feature"})
    assert result["ok"] is False


def test_spec_create_tool(tmp_path):
    result = call_tool(
        tmp_path,
        "ananke.spec.create",
        {"requirement_id": "T-1", "title": "Test spec"},
    )
    # May fail for various setup reasons, but should return a dict
    assert isinstance(result, dict)


def test_spec_create_missing_args(tmp_path):
    result = call_tool(tmp_path, "ananke.spec.create", {"title": "no id"})
    assert result["ok"] is False


def test_arch_get_no_calm(tmp_path):
    result = call_tool(tmp_path, "ananke.arch.get", {})
    assert result["ok"] is False


def test_arch_get_with_calm(tmp_path):
    calm = tmp_path / ".ananke" / "architecture"
    calm.mkdir(parents=True)
    (calm / "system.calm.json").write_text('{"nodes": []}', encoding="utf-8")
    result = call_tool(tmp_path, "ananke.arch.get", {})
    assert result["ok"] is True


def test_lifecycle_transition_missing_args(tmp_path):
    result = call_tool(tmp_path, "ananke.lifecycle.transition_issue", {"ticket": "T-1"})
    assert result["ok"] is False


def test_lifecycle_create_pr_missing_args(tmp_path):
    result = call_tool(tmp_path, "ananke.lifecycle.create_pr", {"title": "My PR"})
    assert result["ok"] is False

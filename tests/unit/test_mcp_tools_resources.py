"""Tests for MCP tools and resources (H2-H4)."""

import json
from pathlib import Path

from ananke.plexus.mcp.auth import DEFAULT_PERMISSION_MAP
from ananke.plexus.mcp.resources import list_dynamic_resources, list_resources, read_resource
from ananke.plexus.mcp.server import handle_message


def test_tools_list_read_only_by_default():
    result = handle_message(Path("."), {"action": "tools.list"})
    assert result["ok"] is True
    tools = result["result"]
    assert "ananke.project.status" in tools
    assert "ananke.run.execute" not in tools


def test_tools_list_includes_mutations_when_enabled():
    result = handle_message(Path("."), {"action": "tools.list"}, allow_mutations=True)
    tools = result["result"]
    assert "ananke.run.execute" in tools
    assert "ananke.git.create_branch" in tools


def test_mutation_tool_blocked_without_flag():
    msg = {"action": "tools.call", "tool": "ananke.run.execute", "args": {"spec_id": "S1"}}
    result = handle_message(Path("."), msg)
    assert result["ok"] is False
    assert result["error"] == "mutation_tool_blocked"


def test_unknown_tool_blocked():
    msg = {"action": "tools.call", "tool": "ananke.does.not.exist", "args": {}}
    result = handle_message(Path("."), msg)
    assert result["ok"] is False
    assert result["error"] == "unknown_tool"


def test_ping():
    result = handle_message(Path("."), {"action": "ping"})
    assert result["ok"] is True
    assert result["result"] == "pong"


def test_shutdown():
    result = handle_message(Path("."), {"action": "shutdown"})
    assert result["ok"] is True


def test_static_resources_list():
    resources = list_resources()
    assert "ananke://project/status" in resources
    assert "ananke://architecture/system" in resources
    assert "ananke://policy/index" in resources


def test_dynamic_resources_discovers_specs(tmp_path):
    specs = tmp_path / ".ananke" / "specs"
    (specs / "DEMO-101").mkdir(parents=True)
    resources = list_dynamic_resources(tmp_path)
    assert "ananke://spec/DEMO-101" in resources


def test_dynamic_resources_discovers_policy(tmp_path):
    policy_dir = tmp_path / ".ananke" / "policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "baseline.toml").write_text("[x]\n", encoding="utf-8")
    resources = list_dynamic_resources(tmp_path)
    assert "ananke://policy/baseline" in resources


def test_read_resource_project_status(tmp_path):
    config = tmp_path / ".ananke" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[project]\nname = 'test'\n", encoding="utf-8")
    text = read_resource(tmp_path, "ananke://project/status")
    assert "test" in text


def test_read_resource_spec_index(tmp_path):
    (tmp_path / ".ananke" / "specs" / "F1").mkdir(parents=True)
    text = read_resource(tmp_path, "ananke://spec/index")
    data = json.loads(text)
    assert "F1" in data["specs"]


def test_read_resource_per_spec(tmp_path):
    spec_dir = tmp_path / ".ananke" / "specs" / "DEMO-101"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec", encoding="utf-8")
    text = read_resource(tmp_path, "ananke://spec/DEMO-101")
    data = json.loads(text)
    assert "spec.md" in data


def test_read_resource_not_found(tmp_path):
    result = read_resource(tmp_path, "ananke://does/not/exist")
    assert "not found" in result


def test_permission_map_all_tools():
    all_tools = DEFAULT_PERMISSION_MAP.all_tools
    assert "ananke.project.status" in all_tools
    assert "ananke.run.execute" in all_tools

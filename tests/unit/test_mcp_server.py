from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.mcp.server import handle_message


def test_mcp_lists_tools_and_resources(tmp_path: Path) -> None:
    Ananke.open(tmp_path).init_project()

    tools = handle_message(tmp_path, {"action": "tools.list"})
    assert tools["ok"] is True

    resources = handle_message(tmp_path, {"action": "resources.list"})
    assert resources["ok"] is True


def test_mcp_calls_project_status(tmp_path: Path) -> None:
    Ananke.open(tmp_path).init_project()

    result = handle_message(
        tmp_path,
        {"action": "tools.call", "tool": "ananke.project.status", "args": {}},
    )
    assert result["ok"] is True


def test_mcp_blocks_mutation_tools(tmp_path: Path) -> None:
    result = handle_message(
        tmp_path,
        {"action": "tools.call", "tool": "ananke.run.execute", "args": {}},
    )
    assert result["ok"] is False

"""MCP JSON-RPC 2.0: what IDE and agent MCP clients speak."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ananke.plexus.mcp.jsonrpc import LATEST_PROTOCOL, handle_jsonrpc
from ananke.plexus.mcp.server import handle_message


def _call(tmp_path: Path, method: str, params: dict | None = None, **kw: bool) -> dict:  # type: ignore[type-arg]
    reply = handle_jsonrpc(
        tmp_path, {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}, **kw
    )
    assert reply is not None and reply["id"] == 7
    return reply


def test_initialize_negotiates_protocol_and_capabilities(tmp_path: Path) -> None:
    reply = _call(
        tmp_path,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )
    result = reply["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert {"tools", "resources", "prompts"} <= set(result["capabilities"])
    assert result["serverInfo"]["name"] == "ananke-plexus"
    unknown = _call(tmp_path, "initialize", {"protocolVersion": "1999-01-01"})
    assert unknown["result"]["protocolVersion"] == LATEST_PROTOCOL


def test_notifications_get_no_reply(tmp_path: Path) -> None:
    assert (
        handle_jsonrpc(tmp_path, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )
    assert (
        handle_message(
            tmp_path, {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}
        )
        is None
    )


def test_tools_have_schemas_and_mutations_are_opt_in(tmp_path: Path) -> None:
    tools = _call(tmp_path, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "ananke.verify.run" in names and "registry_search" in names
    assert "ananke.git.commit" not in names
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert "ananke.bmad.get_contract" in names
    with_mut = {
        t["name"] for t in _call(tmp_path, "tools/list", allow_mutations=True)["result"]["tools"]
    }
    assert "ananke.git.commit" in with_mut


def test_tools_call_returns_content_and_flags_errors(tmp_path: Path) -> None:
    reply = _call(tmp_path, "tools/call", {"name": "ananke.spec.validate", "arguments": {}})
    assert reply["result"]["isError"] is True
    assert "feature_dir required" in reply["result"]["content"][0]["text"]
    blocked = _call(
        tmp_path, "tools/call", {"name": "ananke.git.commit", "arguments": {"message": "x"}}
    )
    assert (
        blocked["result"]["isError"] is True
        and "--allow-mutations" in blocked["result"]["content"][0]["text"]
    )
    unknown = _call(tmp_path, "tools/call", {"name": "nope", "arguments": {}})
    assert unknown["error"]["code"] == -32602


def test_resources_and_prompts(tmp_path: Path) -> None:
    resources = _call(tmp_path, "resources/list")["result"]["resources"]
    assert any(r["uri"] == "ananke://project/status" for r in resources)
    text = _call(tmp_path, "resources/read", {"uri": "ananke://project/status"})["result"][
        "contents"
    ][0]["text"]
    assert "not initialized" in text
    prompts = _call(tmp_path, "prompts/list")["result"]["prompts"]
    assert any(p["name"] == "blast-radius-review" for p in prompts)
    got = _call(tmp_path, "prompts/get", {"name": "blast-radius-review"})["result"]
    assert got["messages"][0]["role"] == "user"


def test_unknown_method_is_method_not_found(tmp_path: Path) -> None:
    assert _call(tmp_path, "sampling/createMessage")["error"]["code"] == -32601


def test_legacy_action_protocol_still_works(tmp_path: Path) -> None:
    assert handle_message(tmp_path, {"action": "ping"}) == {"ok": True, "result": "pong"}


def test_the_official_mcp_client_can_talk_to_serve_mcp(tmp_path: Path) -> None:
    """End to end with the MCP Python SDK over stdio, as an IDE would connect."""
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario() -> tuple[str, list[str], str]:
        params = StdioServerParameters(
            command=str(Path(sys.executable).parent / "ananke"),
            args=["serve-mcp", "--project", str(tmp_path)],
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("ananke.project.status", {})
            text = result.content[0].text if result.content else ""
            info = getattr(init, "server_info", None) or init.serverInfo  # SDK v2 is snake_case
            return info.name, [t.name for t in tools.tools], text

    name, tool_names, status = asyncio.run(asyncio.wait_for(scenario(), timeout=60))
    assert name == "ananke-plexus"
    assert "ananke.graph.impact" in tool_names
    assert json.loads(status)["ok"] in (True, False)


def test_feature_dir_tools_stay_inside_the_project(tmp_path: Path) -> None:
    outside = tmp_path.parent
    for tool in ("ananke.spec.get", "ananke.bmad.get_contract", "ananke.spec.validate"):
        reply = _call(
            tmp_path, "tools/call", {"name": tool, "arguments": {"feature_dir": str(outside)}}
        )
        assert reply["result"]["isError"] is True
        assert "inside the project" in reply["result"]["content"][0]["text"]
    reply = _call(
        tmp_path, "tools/call", {"name": "ananke.spec.get", "arguments": {"feature_dir": "../"}}
    )
    assert "inside the project" in reply["result"]["content"][0]["text"]


def test_bmad_contract_is_returned(tmp_path: Path) -> None:
    feature = tmp_path / ".ananke" / "specs" / "P-1-x"
    feature.mkdir(parents=True)
    (feature / "bmad.yaml").write_text("behavior: []\n")
    reply = _call(
        tmp_path,
        "tools/call",
        {"name": "ananke.bmad.get_contract", "arguments": {"feature_dir": ".ananke/specs/P-1-x"}},
    )
    assert reply["result"]["isError"] is False
    assert "behavior" in reply["result"]["structuredContent"]["details"]["bmad"]

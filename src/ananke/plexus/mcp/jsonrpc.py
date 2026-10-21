"""Model Context Protocol over JSON-RPC 2.0: what IDE and agent MCP clients speak.

Copilot, Claude, Kiro and other MCP clients send ``initialize``, then ``tools/list`` and ``tools/call`` (and the
resource and prompt methods) as JSON-RPC 2.0 messages, one per line on stdio. This module answers them using the
same tool, resource and prompt handlers, and the same permission map, as Ananke's original ``{"action": …}``
protocol. That protocol keeps working: :func:`ananke.plexus.mcp.server.handle_message` routes a message here
only when it carries ``"jsonrpc"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ananke.plexus.mcp.auth import DEFAULT_PERMISSION_MAP
from ananke.plexus.mcp.prompts import PROMPTS
from ananke.plexus.mcp.resources import list_resources, read_resource
from ananke.plexus.mcp.tools import call_tool
from ananke.plexus.version import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[0]

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _obj(
    properties: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or []}


_S = {"type": "string"}
_FEATURE_DIR = {
    "type": "string",
    "description": "Spec feature folder, e.g. .ananke/specs/PROJ-1-title",
}

# name → (description, input schema). Only tools with a handler are advertised.
TOOL_SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "ananke.project.status": ("Environment and project diagnostics.", _obj()),
    "ananke.spec.get": (
        "List the artifacts in a spec feature folder.",
        _obj({"feature_dir": _FEATURE_DIR}, ["feature_dir"]),
    ),
    "ananke.spec.validate": (
        "Validate a spec folder and report lock drift.",
        _obj({"feature_dir": _FEATURE_DIR}, ["feature_dir"]),
    ),
    "ananke.bmad.get_contract": (
        "The compiled BMAD contract (behaviour, model, architecture) of a spec.",
        _obj({"feature_dir": _FEATURE_DIR}, ["feature_dir"]),
    ),
    "ananke.spec.create": (
        "Create a requirement, spec and BMAD bundle.",
        _obj(
            {
                "requirement_id": _S,
                "title": _S,
                "body": _S,
                "acceptance_criteria": {"type": "array", "items": _S},
            },
            ["requirement_id", "title"],
        ),
    ),
    "ananke.graph.query": ("Find code-graph nodes by name or path.", _obj({"text": _S}, ["text"])),
    "ananke.graph.impact": (
        "Symbols and files impacted by changed files (blast radius).",
        _obj({"changed_files": {"type": "array", "items": _S}}, ["changed_files"]),
    ),
    "ananke.graph.update": ("Rebuild the code-graph snapshot.", _obj()),
    "ananke.arch.get": ("The CALM architecture document.", _obj()),
    "ananke.arch.validate": ("Validate the CALM architecture.", _obj()),
    "ananke.arch.render": ("Render the architecture as Markdown and Mermaid.", _obj()),
    "ananke.policy.explain": (
        "Evaluate policy rules for a stage, rule by rule.",
        _obj({"stage": {"type": "string", "default": "verify"}}),
    ),
    "ananke.verify.run": ("Run the verification gates and write an evidence bundle.", _obj()),
    "ananke.run.status": ("State of an autonomous run.", _obj({"run_id": _S}, ["run_id"])),
    "ananke.evidence.get": ("Evidence manifest of a run.", _obj({"run_id": _S}, ["run_id"])),
    "registry_search": (
        "Search the capability registry (skills, agents, tools, …).",
        _obj(
            {
                "query": _S,
                "kind": _S,
                "capability": _S,
                "runtime": _S,
                "trust": _S,
                "channel": _S,
                "limit": {"type": "integer", "default": 20},
            }
        ),
    ),
    "registry_get_skill": (
        "A registered skill (latest or a version).",
        _obj({"ref": _S, "version": _S}, ["ref"]),
    ),
    "registry_get_agent": (
        "A registered agent (latest or a version).",
        _obj({"ref": _S, "version": _S}, ["ref"]),
    ),
    "registry_resolve": (
        "Policy-aware resolution of a reference, with reasons.",
        _obj({"ref": _S, "kind": _S, "version": _S, "runtime": _S, "mode": _S}, ["ref"]),
    ),
    "registry_compare_versions": (
        "Diff two versions (capabilities, permissions, schema).",
        _obj({"a": _S, "b": _S}, ["a", "b"]),
    ),
    "registry_list_capabilities": ("Capabilities known to the registry.", _obj()),
    # mutation tools: advertised only with --allow-mutations
    "ananke.run.execute": (
        "Start an autonomous run for a spec.",
        _obj({"spec_id": _S}, ["spec_id"]),
    ),
    "ananke.git.create_branch": (
        "Compute a governed branch name.",
        _obj({"change_type": _S, "ticket": _S, "slug": _S}, ["ticket", "slug"]),
    ),
    "ananke.git.commit": ("Create a git commit.", _obj({"message": _S}, ["message"])),
    "ananke.lifecycle.transition_issue": (
        "Move a tracker issue to a new state.",
        _obj({"ticket": _S, "transition": _S}, ["ticket", "transition"]),
    ),
    "ananke.lifecycle.create_pr": (
        "Open a pull request.",
        _obj({"title": _S, "branch": _S}, ["title", "branch"]),
    ),
}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def advertised_tools(*, allow_mutations: bool) -> list[dict[str, Any]]:
    names = (
        DEFAULT_PERMISSION_MAP.all_tools
        if allow_mutations
        else DEFAULT_PERMISSION_MAP.read_only_tools
    )
    tools = []
    for name in sorted(names):
        if name not in TOOL_SPECS:
            continue  # declared in the permission map but without a handler yet
        description, schema = TOOL_SPECS[name]
        readonly = name in DEFAULT_PERMISSION_MAP.read_only_tools
        tools.append(
            {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "annotations": {"readOnlyHint": readonly, "destructiveHint": not readonly},
            }
        )
    return tools


def handle_jsonrpc(
    repository_root: Path, message: dict[str, Any], *, allow_mutations: bool = False
) -> dict[str, Any] | None:
    """Answer one JSON-RPC 2.0 message. Returns ``None`` for notifications (no reply is sent)."""
    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message
    if not isinstance(method, str):
        return None if is_notification else _error(msg_id, INVALID_REQUEST, "missing method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return (
            None if is_notification else _error(msg_id, INVALID_PARAMS, "params must be an object")
        )

    if method.startswith("notifications/"):
        return None
    if is_notification:
        return None

    if method == "initialize":
        requested = str(params.get("protocolVersion", LATEST_PROTOCOL))
        version = requested if requested in PROTOCOL_VERSIONS else LATEST_PROTOCOL
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                "serverInfo": {
                    "name": "ananke-plexus",
                    "title": "Ananke Plexus",
                    "version": __version__,
                },
                "instructions": (
                    "Ananke Plexus governs agentic software engineering: specs, CALM architecture, the code "
                    "graph, policy gates and evidence. Read before you change; verify before you claim done."
                ),
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": advertised_tools(allow_mutations=allow_mutations)})
    if method == "tools/call":
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        allowed, reason = DEFAULT_PERMISSION_MAP.can_call(name, allow_mutations=allow_mutations)
        if not allowed or name not in TOOL_SPECS:
            if reason == "unknown_tool" or name not in TOOL_SPECS:
                return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
            return _result(
                msg_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": f"{name} is a mutation tool; start the server with --allow-mutations",
                        }
                    ],
                    "isError": True,
                },
            )
        try:
            payload = call_tool(repository_root, name, args)
        except Exception as exc:  # a tool failure is a tool result, not a protocol error
            return _result(
                msg_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )
        ok = not (isinstance(payload, dict) and payload.get("ok") is False)
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, default=str, indent=2)}],
                "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
                "isError": not ok,
            },
        )
    if method == "resources/list":
        return _result(
            msg_id,
            {
                "resources": [
                    {"uri": uri, "name": uri.removeprefix("ananke://"), "mimeType": "text/plain"}
                    for uri in list_resources()
                ]
            },
        )
    if method == "resources/read":
        uri = str(params.get("uri", ""))
        if not uri:
            return _error(msg_id, INVALID_PARAMS, "uri required")
        return _result(
            msg_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": read_resource(repository_root, uri),
                    }
                ]
            },
        )
    if method == "prompts/list":
        return _result(
            msg_id,
            {
                "prompts": [
                    {"name": name, "description": text} for name, text in sorted(PROMPTS.items())
                ]
            },
        )
    if method == "prompts/get":
        name = str(params.get("name", ""))
        if name not in PROMPTS:
            return _error(msg_id, INVALID_PARAMS, f"unknown prompt: {name}")
        return _result(
            msg_id,
            {
                "description": PROMPTS[name],
                "messages": [{"role": "user", "content": {"type": "text", "text": PROMPTS[name]}}],
            },
        )
    return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

import json
import sys
from pathlib import Path
from typing import Any

from ananke.plexus.mcp.auth import DEFAULT_PERMISSION_MAP, MAX_REQUEST_BYTES
from ananke.plexus.mcp.prompts import get_prompt, list_prompts
from ananke.plexus.mcp.resources import list_resources, read_resource
from ananke.plexus.mcp.tools import call_tool


def _response(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle_message(
    repository_root: Path,
    message: dict[str, Any],
    *,
    allow_mutations: bool = False,
) -> dict[str, Any]:
    action = message.get("action")

    if action == "ping":
        return {"ok": True, "result": "pong"}

    if action == "tools.list":
        tools = sorted(DEFAULT_PERMISSION_MAP.read_only_tools)
        if allow_mutations:
            tools = sorted(DEFAULT_PERMISSION_MAP.all_tools)
        return {"ok": True, "result": tools}

    if action == "resources.list":
        return {"ok": True, "result": list_resources()}

    if action == "resources.read":
        uri = str(message.get("resource", ""))
        return {"ok": True, "result": read_resource(repository_root, uri)}

    if action == "prompts.list":
        return {"ok": True, "result": list_prompts()}

    if action == "prompts.get":
        name = str(message.get("prompt", ""))
        return {"ok": True, "result": get_prompt(name)}

    if action == "tools.call":
        tool_name = str(message.get("tool", ""))
        allowed, reason = DEFAULT_PERMISSION_MAP.can_call(
            tool_name, allow_mutations=allow_mutations
        )
        if not allowed:
            return {"ok": False, "error": reason, "tool": tool_name}
        args = message.get("args", {})
        if not isinstance(args, dict):
            return {"ok": False, "error": "invalid_args"}
        return {"ok": True, "result": call_tool(repository_root, tool_name, args)}

    if action == "shutdown":
        return {"ok": True, "result": "bye"}

    return {"ok": False, "error": "unsupported_action"}


def serve_stdio(repository_root: Path | None = None, *, allow_mutations: bool = False) -> None:
    root = repository_root or Path.cwd()
    for line in sys.stdin:
        raw = line.strip("\n")
        if not raw:
            continue

        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            _response({"ok": False, "error": "request_too_large"})
            continue

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            _response({"ok": False, "error": "invalid_json"})
            continue

        if not isinstance(message, dict):
            _response({"ok": False, "error": "invalid_payload"})
            continue

        response = handle_message(root, message, allow_mutations=allow_mutations)
        _response(response)
        if message.get("action") == "shutdown":
            break

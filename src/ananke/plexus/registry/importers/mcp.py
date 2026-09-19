"""MCP importer (spec §39): learn tools/resources/prompts from an MCP server.

Three source forms, in increasing order of risk:

* ``mcp:<snapshot.json>`` — a saved ``tools/list`` response; fully static, always allowed.
* ``mcp-stdio:<command …>`` — spawns a *local process* (code execution) → needs dynamic
  introspection to be allowed.
* ``mcp-http:<url>`` — a remote server → needs explicit network approval.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ananke.plexus.registry.errors import ImporterError, PolicyViolationError
from ananke.plexus.registry.hashing import canonical_json
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImporterPermissions,
    InspectionContext,
    ProbeResult,
    Source,
    slugify,
)
from ananke.plexus.registry.importers.common import now_iso, tree_fingerprint
from ananke.plexus.registry.models import ImporterInfo, Provenance
from ananke.plexus.registry.semver import normalize_version

PROTOCOL_VERSION = "2024-11-05"
_TIMEOUT = 20.0
_MAX_BYTES = 5_000_000


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _rpc(
    method: str, params: dict[str, Any] | None = None, rid: int | None = None
) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if rid is not None:
        msg["id"] = rid
    return msg


_INIT = {
    "protocolVersion": PROTOCOL_VERSION,
    "capabilities": {},
    "clientInfo": {"name": "ananke-registry", "version": "1"},
}


class _StdioClient:
    def __init__(self, argv: list[str]) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ananke-mcp-")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.tmp.name, "LANG": "C.UTF-8"}
        self.proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False, scrubbed env
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=self.tmp.name,
            env=env,
        )

    def call(self, method: str, params: dict[str, Any] | None, rid: int) -> dict[str, Any]:
        assert self.proc.stdin and self.proc.stdout  # noqa: S101
        self.proc.stdin.write(json.dumps(_rpc(method, params, rid)) + "\n")
        self.proc.stdin.flush()
        result: list[dict[str, Any]] = []

        def read() -> None:
            assert self.proc.stdout is not None  # noqa: S101
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    return
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("id") == rid:
                    result.append(data)
                    return

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(_TIMEOUT)
        if not result:
            raise ImporterError(f"MCP server did not answer {method!r} within {_TIMEOUT}s")
        if "error" in result[0]:
            raise ImporterError(f"MCP error for {method}: {result[0]['error']}")
        return dict(result[0].get("result", {}))

    def notify(self, method: str) -> None:
        assert self.proc.stdin  # noqa: S101
        self.proc.stdin.write(json.dumps(_rpc(method)) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.tmp.cleanup()


def _query_stdio(argv: list[str]) -> dict[str, Any]:
    client = _StdioClient(argv)
    try:
        init = client.call("initialize", _INIT, 1)
        client.notify("notifications/initialized")
        out: dict[str, Any] = {"serverInfo": init.get("serverInfo", {})}
        caps = init.get("capabilities", {}) or {}
        for i, (key, method) in enumerate(
            (("tools", "tools/list"), ("resources", "resources/list"), ("prompts", "prompts/list")),
            start=2,
        ):
            out[key] = (
                client.call(method, {}, i).get(key, []) if (key in caps or key == "tools") else []
            )
        return out
    finally:
        client.close()


def _query_http(url: str) -> dict[str, Any]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    out: dict[str, Any] = {}
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:

        def post(method: str, params: dict[str, Any] | None, rid: int | None) -> dict[str, Any]:
            resp = client.post(url, json=_rpc(method, params, rid), headers=headers)
            resp.raise_for_status()
            if "mcp-session-id" in resp.headers:
                headers["Mcp-Session-Id"] = resp.headers["mcp-session-id"]
            if rid is None:
                return {}
            text = resp.text[:_MAX_BYTES]
            if "text/event-stream" in resp.headers.get("content-type", ""):
                for line in text.splitlines():
                    if line.startswith("data:"):
                        text = line[5:].strip()
                        break
            data = json.loads(text)
            if "error" in data:
                raise ImporterError(f"MCP error for {method}: {data['error']}")
            return dict(data.get("result", {}))

        init = post("initialize", _INIT, 1)
        post("notifications/initialized", None, None)
        out["serverInfo"] = init.get("serverInfo", {})
        caps = init.get("capabilities", {}) or {}
        for i, (key, method) in enumerate(
            (("tools", "tools/list"), ("resources", "resources/list"), ("prompts", "prompts/list")),
            start=2,
        ):
            out[key] = post(method, {}, i).get(key, []) if (key in caps or key == "tools") else []
    return out


class McpImporter:
    id: ClassVar[str] = "mcp"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = False
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions(
        executes_code=True, network=True
    )

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme in {"mcp", "mcp-stdio", "mcp-http"}:
            return ProbeResult(ok=True, confidence=1.0, reason=source.scheme)
        return ProbeResult(ok=False, reason="not an MCP source")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        dynamic = False
        if source.scheme == "mcp":
            path = source.path
            if not path.is_file():
                raise ImporterError(f"MCP snapshot not found: {path}")
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ImporterError(f"invalid MCP snapshot: {exc}") from exc
            if isinstance(snapshot, list):
                snapshot = {"tools": snapshot}
            origin = path.name
        elif source.scheme == "mcp-stdio":
            if not ctx.policy.dynamic_allowed(ctx.allow_dynamic):
                raise PolicyViolationError(
                    "mcp-stdio launches a local process (code execution); enable "
                    "dynamic_introspection or pass --allow-dynamic",
                    code="DYNAMIC_INTROSPECTION_DISABLED",
                )
            argv = shlex.split(source.target)
            if not argv:
                raise ImporterError("mcp-stdio requires a command")
            snapshot, dynamic = _query_stdio(argv), True
            origin = argv[0]
        else:
            url = source.target
            if not url.startswith(("http://", "https://")):
                raise ImporterError("mcp-http requires an http(s) URL")
            if not ctx.policy.network_allowed("mcp", ctx.allow_network):
                raise PolicyViolationError(
                    "remote MCP introspection needs explicit network approval "
                    "(remote_sources.allow_mcp_network or --allow-network)",
                    code="NETWORK_NOT_APPROVED",
                )
            snapshot = _query_http(url)
            origin = _redact_url(url)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tools", []), list):
            raise ImporterError("MCP snapshot must be an object with a 'tools' list")

        info = snapshot.get("serverInfo") or snapshot.get("server") or {}
        server_name = str(info.get("name") or "").strip()
        tools = [t for t in snapshot.get("tools", []) if isinstance(t, dict) and t.get("name")]
        resources = [r for r in snapshot.get("resources", []) if isinstance(r, dict)]
        prompts = [p for p in snapshot.get("prompts", []) if isinstance(p, dict)]
        canonical: dict[str, Any] = {
            "server": {"name": server_name, "version": str(info.get("version") or "")},
            "tools": sorted(tools, key=lambda t: str(t["name"])),
            "resources": sorted(resources, key=lambda r: str(r.get("uri", r.get("name", "")))),
            "prompts": sorted(prompts, key=lambda p: str(p.get("name", ""))),
        }
        files = {"mcp-snapshot.json": canonical_json(canonical) + b"\n"}
        draft: dict[str, Any] = {
            "kind": "skill",
            "name": slugify(server_name or origin.rsplit(".", 1)[0]),
            "summary": str(info.get("title") or f"MCP server {server_name}".strip()),
            "description": (
                f"Capabilities discovered from MCP server {server_name or origin}: "
                f"{len(tools)} tool(s), {len(resources)} resource(s), {len(prompts)} prompt(s)."
            ),
            "tools": [
                {
                    "name": str(t["name"]),
                    "description": str(t.get("description", "")),
                    **(
                        {"input_schema": t["inputSchema"]}
                        if isinstance(t.get("inputSchema"), dict)
                        else {}
                    ),
                    **(
                        {"output_schema": t["outputSchema"]}
                        if isinstance(t.get("outputSchema"), dict)
                        else {}
                    ),
                }
                for t in canonical["tools"]
            ],
            "runtime": {"supported": ["generic-mcp"]},
            "dependencies": [{"type": "mcp-server", "name": server_name}] if server_name else [],
        }
        version = str(info.get("version") or "")
        if version:
            try:
                draft["version"] = str(normalize_version(version))
            except Exception:
                draft["version"] = None
        cand = Candidate(draft=draft, files=files, source=source.raw, dynamic=dynamic)
        cand.provenance = Provenance(
            source_type="mcp",
            source_name=server_name or origin,
            source_version=version or None,
            source_url=_redact_url(source.target) if source.scheme == "mcp-http" else None,
            native_id=server_name or None,
            native_schema={"tools": canonical["tools"]},
            importer=ImporterInfo(id=self.id, version=self.version),
            discovered_at=now_iso(),
            fingerprint=tree_fingerprint(files),
        )
        cand.note(
            "info",
            "discovered",
            f"{len(tools)} tools, {len(resources)} resources, {len(prompts)} prompts",
        )
        cand.note(
            "warning",
            "no-permissions",
            "MCP does not declare permissions; declare them explicitly before approving",
            "permissions",
        )
        if not version:
            cand.note(
                "warning",
                "no-version",
                "server reports no version; supply one with --version",
                "version",
            )
        return [cand]

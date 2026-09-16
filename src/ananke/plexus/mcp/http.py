"""Minimal HTTP transport for MCP action messages."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ananke.plexus.mcp.auth import MAX_REQUEST_BYTES
from ananke.plexus.mcp.server import handle_message


def handle_http_payload(
    repository_root: Path,
    payload: dict[str, Any],
    auth_token: str | None,
    auth_header: str | None,
) -> dict[str, Any]:
    if auth_token:
        expected = f"Bearer {auth_token}"
        if auth_header != expected:
            return {"ok": False, "error": "unauthorized"}

    return handle_message(repository_root, payload)


def serve_http(
    repository_root: Path,
    host: str,
    port: int,
    auth_token: str | None = None,
) -> None:
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/mcp":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self.send_response(413)
                self.end_headers()
                return

            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return

            if not isinstance(payload, dict):
                self.send_response(400)
                self.end_headers()
                return

            result = handle_http_payload(
                repository_root,
                payload,
                auth_token,
                self.headers.get("Authorization"),
            )

            encoded = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = HTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()

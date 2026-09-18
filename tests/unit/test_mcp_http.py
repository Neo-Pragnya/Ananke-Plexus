from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.mcp.http import handle_http_payload


def test_http_payload_requires_token_when_configured(tmp_path: Path) -> None:
    Ananke.open(tmp_path).init_project()
    token = "demo-token"

    unauthorized = handle_http_payload(
        tmp_path,
        {"action": "ping"},
        auth_token=token,
        auth_header=None,
    )
    assert unauthorized["ok"] is False

    authorized = handle_http_payload(
        tmp_path,
        {"action": "ping"},
        auth_token=token,
        auth_header=f"Bearer {token}",
    )
    assert authorized["ok"] is True


def test_http_payload_calls_tool(tmp_path: Path) -> None:
    Ananke.open(tmp_path).init_project()
    result = handle_http_payload(
        tmp_path,
        {"action": "tools.call", "tool": "ananke.project.status", "args": {}},
        auth_token=None,
        auth_header=None,
    )
    assert result["ok"] is True

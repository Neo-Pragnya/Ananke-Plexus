"""Identifiers from MCP clients, gateways and the command line can't leave the project."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananke.plexus.app import AppError, AppFacade
from ananke.plexus.core.paths import UnsafeIdentifierError, safe_child
from ananke.plexus.execution.state import read_run_state
from ananke.plexus.lifecycle.http_client import json_request
from ananke.plexus.mcp.tools import call_tool

HOSTILE = ["../../outside", "..", "a/../../b", "/etc", "a/b", "", ".hidden", "a\x00b", "x" * 300]


@pytest.mark.parametrize("identifier", HOSTILE)
def test_hostile_identifiers_are_refused(tmp_path: Path, identifier: str) -> None:
    with pytest.raises(UnsafeIdentifierError):
        safe_child(tmp_path, identifier)


@pytest.mark.parametrize("identifier", ["run-1a2b", "20260101T000000Z-ab12cd34", "spec-req-1_x.y"])
def test_real_identifiers_work(tmp_path: Path, identifier: str) -> None:
    assert safe_child(tmp_path, identifier).name == identifier


def test_a_symlink_out_of_the_parent_is_refused(tmp_path: Path) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    (parent / "escape").symlink_to(tmp_path)
    with pytest.raises(UnsafeIdentifierError):
        safe_child(parent, "escape")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".ananke" / "evidence" / "good").mkdir(parents=True)
    (root / ".ananke" / "evidence" / "good" / "manifest.json").write_text('{"ok": true}')
    (root / ".ananke" / "runs").mkdir()
    (tmp_path / "manifest.json").write_text('{"secret": "outside the project"}')
    (tmp_path / "state.json").write_text('{"secret": "outside the project"}')
    return root


def test_mcp_evidence_reads_real_runs_and_cant_escape(project: Path) -> None:
    assert call_tool(project, "ananke.evidence.get", {"run_id": "good"})["ok"] is True
    escaped = call_tool(project, "ananke.evidence.get", {"run_id": "../../.."})
    assert escaped["ok"] is False and "secret" not in json.dumps(escaped)


def test_mcp_run_status_cant_escape(project: Path) -> None:
    result = call_tool(project, "ananke.run.status", {"run_id": "../../.."})
    assert result["ok"] is False and "secret" not in json.dumps(result)


def test_mcp_feature_dir_stays_in_the_project(project: Path) -> None:
    result = call_tool(project, "ananke.bmad.get_contract", {"feature_dir": "../"})
    assert result["ok"] is False and "inside the project" in str(result["summary"])


def test_reading_run_state_cant_escape(project: Path) -> None:
    with pytest.raises(UnsafeIdentifierError):
        read_run_state(project, "../../..")


@pytest.mark.parametrize("call", ["get_run", "cancel_run", "stream_events"])
def test_the_facade_treats_a_hostile_run_id_as_not_found(project: Path, call: str) -> None:
    (project / ".ananke" / "app" / "runs").mkdir(parents=True)
    outside = project / ".ananke" / "app" / "run.json"
    outside.write_text('{"state": "queued", "secret": true}')
    with pytest.raises(AppError) as exc:
        getattr(AppFacade(project), call)("../")
    assert exc.value.code == "RUN_NOT_FOUND"
    assert outside.read_text() == '{"state": "queued", "secret": true}'


@pytest.mark.parametrize("url", ["http://jira.example.org/x", "http://10.0.0.5/x"])
def test_credentials_never_travel_over_plain_http(url: str) -> None:
    result = json_request(url, "POST", {}, "Basic abc", retries=0)
    assert result["error"] == "credentials_require_https"


def test_other_schemes_are_refused() -> None:
    assert json_request("file:///etc/passwd", "GET", {}, "")["error"] == "unsupported_url_scheme"

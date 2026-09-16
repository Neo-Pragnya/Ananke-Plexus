from pathlib import Path

from ananke.plexus.api import Ananke


def test_lifecycle_auto_mode_falls_back_local_without_credentials(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        jira_email="",
        jira_token="",
        bitbucket_username="",
        bitbucket_app_password="",
    )

    result = app.lifecycle_transition_issue("PROJ-1", "In Progress", "idem-a", mode="auto")
    assert result.ok
    assert str(result.details["mode"]) == "local"


def test_lifecycle_remote_dry_run_with_credentials(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",  # noqa: S106 - fixture value
        bitbucket_base_url="https://api.bitbucket.org",
        bitbucket_workspace="acme",
        bitbucket_repo_slug="ananke",
        bitbucket_username="bb-user",
        bitbucket_app_password="bb-pass",  # noqa: S106 - fixture value
    )

    issue = app.lifecycle_transition_issue("PROJ-2", "Done", "idem-b", mode="auto")
    pr = app.lifecycle_create_pr("Demo", "feature/proj-2", "idem-c", mode="auto")

    assert issue.ok
    assert pr.ok
    assert str(issue.details["mode"]) == "remote-dry-run"
    assert str(pr.details["mode"]) == "remote-dry-run"
    assert str(issue.details["service"]) == "jira"
    assert str(pr.details["service"]) == "bitbucket"


def test_auth_validate_reports_missing(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    result = app.configure_auth_validate()

    assert not result.ok
    assert str(result.details["jira_ready"]) == "False"
    assert "ANANKE_JIRA_TOKEN" in str(result.details["jira_missing"])


def test_lifecycle_remote_live_success(tmp_path: Path, monkeypatch) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        remote_live_enabled="true",
        remote_live_services="jira,bitbucket,confluence",
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",  # noqa: S106 - fixture value
        bitbucket_base_url="https://api.bitbucket.org",
        bitbucket_workspace="acme",
        bitbucket_repo_slug="ananke",
        bitbucket_username="bb-user",
        bitbucket_app_password="bb-pass",  # noqa: S106 - fixture value
    )

    def fake_request(**kwargs):
        return {"ok": True, "status": 204, "body": ""}

    monkeypatch.setattr("ananke.plexus.lifecycle.adapters.json_request", fake_request)

    issue = app.lifecycle_transition_issue("PROJ-3", "Done", "idem-r1", mode="remote-live")
    pr = app.lifecycle_create_pr("Title", "feature/proj-3", "idem-r2", mode="remote-live")

    assert issue.ok
    assert pr.ok
    assert str(issue.details["status"]) == "remote_live_success"
    assert str(pr.details["status"]) == "remote_live_success"
    evidence_file = tmp_path / ".ananke" / "evidence" / "lifecycle-remote-events.jsonl"
    assert evidence_file.exists()


def test_lifecycle_remote_live_error(tmp_path: Path, monkeypatch) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        remote_live_enabled="true",
        remote_live_services="jira",
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",  # noqa: S106 - fixture value
    )

    def fake_request(**kwargs):
        return {"ok": False, "status": 401, "error": "unauthorized"}

    monkeypatch.setattr("ananke.plexus.lifecycle.adapters.json_request", fake_request)
    issue = app.lifecycle_transition_issue("PROJ-4", "Done", "idem-r3", mode="remote-live")

    assert issue.ok
    assert str(issue.details["status"]) == "remote_live_error"
    assert str(issue.details["http_status"]) == "401"


def test_lifecycle_uses_command_secret_provider(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="",
        jira_token_cmd="printf jira-token-from-cmd",  # noqa: S106 - fixture command
        bitbucket_base_url="https://api.bitbucket.org",
        bitbucket_workspace="acme",
        bitbucket_repo_slug="ananke",
        bitbucket_username="bb-user",
        bitbucket_app_password="",
        bitbucket_app_password_cmd="printf bb-pass-from-cmd",  # noqa: S106 - fixture command
    )

    issue = app.lifecycle_transition_issue("PROJ-7", "Done", "idem-cmd-1", mode="auto")
    pr = app.lifecycle_create_pr("Cmd Token", "feature/proj-7", "idem-cmd-2", mode="auto")

    assert issue.ok
    assert pr.ok
    assert str(issue.details["mode"]) == "remote-dry-run"
    assert str(pr.details["mode"]) == "remote-dry-run"


def test_remote_live_circuit_breaker(tmp_path: Path, monkeypatch) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        remote_live_enabled="true",
        remote_live_services="jira",
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",  # noqa: S106 - fixture value
    )

    def failing_request(**kwargs):
        return {"ok": False, "status": 503, "error": "upstream down"}

    monkeypatch.setattr("ananke.plexus.lifecycle.adapters.json_request", failing_request)

    app.lifecycle_transition_issue("PROJ-8", "Done", "idem-cb-1", mode="remote-live")
    app.lifecycle_transition_issue("PROJ-8", "Done", "idem-cb-2", mode="remote-live")
    app.lifecycle_transition_issue("PROJ-8", "Done", "idem-cb-3", mode="remote-live")
    blocked = app.lifecycle_transition_issue("PROJ-8", "Done", "idem-cb-4", mode="remote-live")

    assert blocked.ok
    assert str(blocked.details["status"]) == "circuit_open"
    telemetry = app.lifecycle_telemetry()
    assert telemetry.ok
    assert int(telemetry.details["jira_consecutive_failures"]) >= 3


def test_remote_live_policy_blocked_when_disabled(tmp_path: Path, monkeypatch) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        remote_live_enabled="false",
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",  # noqa: S106 - fixture value
    )

    def should_not_run(**kwargs):
        raise AssertionError("network call should be blocked by policy")

    monkeypatch.setattr("ananke.plexus.lifecycle.adapters.json_request", should_not_run)
    blocked = app.lifecycle_transition_issue("PROJ-9", "Done", "idem-pol-1", mode="remote-live")
    assert blocked.ok
    assert str(blocked.details["status"]) == "policy_blocked"

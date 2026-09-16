from pathlib import Path

from ananke.plexus.api import Ananke


def test_confluence_upsert_remote_dry_run(tmp_path: Path) -> None:
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
        confluence_base_url="https://jira.example.com/wiki",
        confluence_email="dev@example.com",
        confluence_token="token-2",  # noqa: S106 - fixture value
    )

    content_file = tmp_path / "page.md"
    content_file.write_text("# Demo", encoding="utf-8")

    result = app.lifecycle_confluence_upsert(
        "ENG",
        "Release Notes",
        str(content_file),
        "idem-conf-1",
        mode="auto",
    )

    assert result.ok
    assert str(result.details["mode"]) == "remote-dry-run"
    assert str(result.details["service"]) == "confluence"
    assert "/rest/api/content" in str(result.details["endpoint"])


def test_confluence_upsert_remote_live_success(tmp_path: Path, monkeypatch) -> None:
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
        confluence_base_url="https://jira.example.com/wiki",
        confluence_email="dev@example.com",
        confluence_token="token-2",  # noqa: S106 - fixture value
    )

    content_file = tmp_path / "page-live.md"
    content_file.write_text("# Live", encoding="utf-8")

    def fake_request(**kwargs):
        return {"ok": True, "status": 200, "body": '{"id":"1"}'}

    monkeypatch.setattr("ananke.plexus.lifecycle.confluence.json_request", fake_request)
    result = app.lifecycle_confluence_upsert(
        "ENG",
        "Release Live",
        str(content_file),
        "idem-conf-live",
        mode="remote-live",
    )

    assert result.ok
    assert str(result.details["status"]) == "remote_live_success"

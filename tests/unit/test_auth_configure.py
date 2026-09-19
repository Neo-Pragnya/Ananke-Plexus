from pathlib import Path

from ananke.plexus.api import Ananke


def test_configure_auth_updates_credentials_store(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    result = app.configure_auth(
        jira_base_url="https://jira.example.com",
        jira_email="dev@example.com",
        jira_token="token-1",
        confluence_base_url="https://jira.example.com/wiki",
        confluence_email="wiki@example.com",
        confluence_token="token-2",
        bitbucket_base_url="https://api.bitbucket.org",
        bitbucket_workspace="acme",
        bitbucket_repo_slug="ananke",
        bitbucket_dest_branch="main",
        bitbucket_username="bb-user",
        bitbucket_app_password="bb-pass",
        mcp_http_token="mcp-token",
    )

    assert result.ok
    assert int(result.details["updated_keys"]) >= 1

    cred_path = tmp_path / ".ananke" / "secrets" / "adapters.env"
    content = cred_path.read_text(encoding="utf-8")
    assert "ANANKE_JIRA_BASE_URL=https://jira.example.com" in content
    assert "ANANKE_CONFLUENCE_EMAIL=wiki@example.com" in content
    assert "ANANKE_BITBUCKET_WORKSPACE=acme" in content
    assert "ANANKE_BITBUCKET_REPO_SLUG=ananke" in content
    assert "ANANKE_BITBUCKET_USERNAME=bb-user" in content
    assert "ANANKE_MCP_HTTP_TOKEN=mcp-token" in content


def test_configure_auth_no_updates_keeps_store(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    result = app.configure_auth()
    assert result.ok
    assert int(result.details["updated_keys"]) == 0


def test_configure_auth_values_masks_secrets(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        jira_email="dev@example.com",
        jira_token="token-1",
        bitbucket_app_password="bb-pass",
        jira_token_cmd="printf token",
        jira_bearer_token_cmd="printf bearer",
        confluence_bearer_token_cmd="printf confbearer",
        bitbucket_bearer_token_cmd="printf bbbearer",
    )

    values = app.configure_auth_values()
    assert values.ok
    assert values.details["ANANKE_JIRA_EMAIL"] == "dev@example.com"
    assert values.details["ANANKE_JIRA_TOKEN"] == "***"
    assert values.details["ANANKE_BITBUCKET_APP_PASSWORD"] == "***"
    assert values.details["ANANKE_JIRA_TOKEN_CMD"] == "<configured>"
    assert values.details["ANANKE_JIRA_BEARER_TOKEN_CMD"] == "<configured>"
    assert values.details["ANANKE_CONFLUENCE_BEARER_TOKEN_CMD"] == "<configured>"
    assert values.details["ANANKE_BITBUCKET_BEARER_TOKEN_CMD"] == "<configured>"


def test_configure_auth_export_zsh_snippet(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    export_result = app.configure_auth_export("zsh")
    assert export_result.ok
    snippet = str(export_result.details["snippet"])
    assert "set -a" in snippet
    assert 'source "' in snippet
    assert "adapters.env" in snippet


def test_configure_auth_export_cmd_and_docker(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    cmd_export = app.configure_auth_export("cmd")
    docker_export = app.configure_auth_export("docker-env")

    assert cmd_export.ok
    assert docker_export.ok
    assert "for /f" in str(cmd_export.details["snippet"])
    assert "--env-file" in str(docker_export.details["snippet"])


def test_configure_auth_validate_ready_with_bearer_cmd_only(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()
    app.configure_auth(
        jira_base_url="https://jira.example.com",
        jira_bearer_token_cmd="printf jira-bearer",
        confluence_base_url="https://jira.example.com/wiki",
        confluence_bearer_token_cmd="printf conf-bearer",
        bitbucket_base_url="https://api.bitbucket.org",
        bitbucket_workspace="acme",
        bitbucket_repo_slug="ananke",
        bitbucket_bearer_token_cmd="printf bb-bearer",
    )

    validate = app.configure_auth_validate()
    assert validate.ok
    assert validate.details["ready"] is True
    assert validate.details["jira_ready"] is True
    assert validate.details["confluence_ready"] is True
    assert validate.details["bitbucket_ready"] is True

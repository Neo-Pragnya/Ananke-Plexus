"""Lifecycle adapter execution helpers for local and remote-dry-run modes."""

from base64 import b64encode
from pathlib import Path

from ananke.plexus.config.credentials import (
    credential_validation_report,
    read_credentials_store,
    resolve_secret_value,
)
from ananke.plexus.lifecycle.evidence import append_remote_event
from ananke.plexus.lifecycle.http_client import json_request
from ananke.plexus.lifecycle.telemetry import can_execute_remote, record_remote_result

VALID_MODES = {"local", "auto", "remote-dry-run", "remote-live"}


def _status_code(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _remote_live_policy(values: dict[str, str], service: str) -> tuple[bool, str]:
    enabled = values.get("ANANKE_REMOTE_LIVE_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False, "remote-live-disabled"
    raw_services = values.get("ANANKE_REMOTE_LIVE_SERVICES", "")
    allowed = {item.strip() for item in raw_services.split(",") if item.strip()}
    if allowed and service not in allowed:
        return False, "service-not-allowed"
    return True, "allowed"


def _basic_auth_header(username: str, token: str) -> str:
    raw = f"{username}:{token}".encode()
    return f"Basic {b64encode(raw).decode('ascii')}"


def resolve_mode(repository_root: Path, requested_mode: str) -> tuple[str, str]:
    mode = requested_mode.strip().lower()
    if mode not in VALID_MODES:
        return "local", "invalid-mode-fallback"
    if mode != "auto":
        return mode, "explicit"

    report = credential_validation_report(repository_root)
    services = report.get("services", {})
    if not isinstance(services, dict):
        return "local", "invalid-services-report"
    jira = services.get("jira", {})
    bitbucket = services.get("bitbucket", {})
    jira_ready = isinstance(jira, dict) and bool(jira.get("ready", False))
    bitbucket_ready = isinstance(bitbucket, dict) and bool(bitbucket.get("ready", False))
    if jira_ready and bitbucket_ready:
        return "remote-dry-run", "all-adapters-ready"
    return "local", "missing-adapter-credentials"


def remote_transition_issue(
    repository_root: Path,
    issue_key: str,
    target_state: str,
    execution_mode: str = "remote-dry-run",
) -> dict[str, str]:
    values = read_credentials_store(repository_root)
    missing = []
    for key in ["ANANKE_JIRA_BASE_URL"]:
        if not values.get(key, "").strip():
            missing.append(key)
    jira_bearer = resolve_secret_value(
        values,
        "ANANKE_JIRA_BEARER_TOKEN",
        "ANANKE_JIRA_BEARER_TOKEN_CMD",
    )
    jira_token = resolve_secret_value(values, "ANANKE_JIRA_TOKEN", "ANANKE_JIRA_TOKEN_CMD")
    jira_email = values.get("ANANKE_JIRA_EMAIL", "").strip()
    if not jira_bearer and not (jira_email and jira_token):
        missing.append(
            "ANANKE_JIRA_BEARER_TOKEN|ANANKE_JIRA_BEARER_TOKEN_CMD|"
            "(ANANKE_JIRA_EMAIL+ANANKE_JIRA_TOKEN|ANANKE_JIRA_TOKEN_CMD)"
        )
    if missing:
        return {
            "status": "missing_credentials",
            "service": "jira",
            "missing": ",".join(missing),
        }

    base_url = values["ANANKE_JIRA_BASE_URL"].rstrip("/")
    endpoint = f"{base_url}/rest/api/3/issue/{issue_key}/transitions"
    if execution_mode == "remote-live":
        policy_allowed, policy_reason = _remote_live_policy(values, "jira")
        if not policy_allowed:
            result = {
                "status": "policy_blocked",
                "service": "jira",
                "reason": policy_reason,
            }
            append_remote_event(repository_root, "jira", "issue-transition", execution_mode, result)
            return result
        allowed, wait_seconds = can_execute_remote(repository_root, "jira")
        if not allowed:
            result = {
                "status": "circuit_open",
                "service": "jira",
                "retry_after_seconds": str(wait_seconds),
            }
            append_remote_event(repository_root, "jira", "issue-transition", execution_mode, result)
            return result
        auth_header = (
            f"Bearer {jira_bearer}"
            if jira_bearer
            else _basic_auth_header(values["ANANKE_JIRA_EMAIL"], jira_token)
        )
        response = json_request(
            url=endpoint,
            method="POST",
            payload={"transition": {"id": target_state}},
            auth_header=auth_header,
        )
        attempts = str(response.get("attempts", 1))
        elapsed_ms = str(response.get("elapsed_ms", 0))
        retry_delays = str(response.get("retry_delays", []))
        if bool(response.get("ok", False)):
            record_remote_result(
                repository_root,
                "jira",
                execution_mode,
                "remote_live_success",
                204,
            )
            result = {
                "status": "remote_live_success",
                "service": "jira",
                "issue": issue_key,
                "state": target_state,
                "endpoint": endpoint,
                "method": "POST",
                "attempts": attempts,
                "elapsed_ms": elapsed_ms,
                "retry_delays": retry_delays,
            }
            append_remote_event(repository_root, "jira", "issue-transition", execution_mode, result)
            return result
        status_code = _status_code(response.get("status", 0))
        record_remote_result(
            repository_root,
            "jira",
            execution_mode,
            "remote_live_error",
            status_code,
        )
        result = {
            "status": "remote_live_error",
            "service": "jira",
            "issue": issue_key,
            "state": target_state,
            "endpoint": endpoint,
            "http_status": str(status_code),
            "error": str(response.get("error", "request failed")),
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
            "retry_delays": retry_delays,
        }
        append_remote_event(repository_root, "jira", "issue-transition", execution_mode, result)
        return result
    return {
        "status": "remote_dry_run",
        "service": "jira",
        "issue": issue_key,
        "state": target_state,
        "endpoint": endpoint,
        "method": "POST",
    }


def remote_create_pr(
    repository_root: Path,
    title: str,
    branch: str,
    execution_mode: str = "remote-dry-run",
) -> dict[str, str]:
    values = read_credentials_store(repository_root)
    missing = []
    for key in [
        "ANANKE_BITBUCKET_BASE_URL",
        "ANANKE_BITBUCKET_WORKSPACE",
        "ANANKE_BITBUCKET_REPO_SLUG",
    ]:
        if not values.get(key, "").strip():
            missing.append(key)
    bitbucket_bearer = resolve_secret_value(
        values,
        "ANANKE_BITBUCKET_BEARER_TOKEN",
        "ANANKE_BITBUCKET_BEARER_TOKEN_CMD",
    )
    app_password = resolve_secret_value(
        values,
        "ANANKE_BITBUCKET_APP_PASSWORD",
        "ANANKE_BITBUCKET_APP_PASSWORD_CMD",
    )
    bitbucket_username = values.get("ANANKE_BITBUCKET_USERNAME", "").strip()
    if not bitbucket_bearer and not (bitbucket_username and app_password):
        missing.append(
            "ANANKE_BITBUCKET_BEARER_TOKEN|ANANKE_BITBUCKET_BEARER_TOKEN_CMD|"
            "(ANANKE_BITBUCKET_USERNAME+ANANKE_BITBUCKET_APP_PASSWORD|ANANKE_BITBUCKET_APP_PASSWORD_CMD)"
        )
    if missing:
        return {
            "status": "missing_credentials",
            "service": "bitbucket",
            "missing": ",".join(missing),
        }

    base_url = values["ANANKE_BITBUCKET_BASE_URL"].rstrip("/")
    workspace = values["ANANKE_BITBUCKET_WORKSPACE"].strip()
    repo_slug = values["ANANKE_BITBUCKET_REPO_SLUG"].strip()
    endpoint = f"{base_url}/2.0/repositories/{workspace}/{repo_slug}/pullrequests"
    if execution_mode == "remote-live":
        policy_allowed, policy_reason = _remote_live_policy(values, "bitbucket")
        if not policy_allowed:
            result = {
                "status": "policy_blocked",
                "service": "bitbucket",
                "reason": policy_reason,
            }
            append_remote_event(repository_root, "bitbucket", "pr-create", execution_mode, result)
            return result
        allowed, wait_seconds = can_execute_remote(repository_root, "bitbucket")
        if not allowed:
            result = {
                "status": "circuit_open",
                "service": "bitbucket",
                "retry_after_seconds": str(wait_seconds),
            }
            append_remote_event(repository_root, "bitbucket", "pr-create", execution_mode, result)
            return result
        auth_header = (
            f"Bearer {bitbucket_bearer}"
            if bitbucket_bearer
            else _basic_auth_header(values["ANANKE_BITBUCKET_USERNAME"], app_password)
        )
        response = json_request(
            url=endpoint,
            method="POST",
            payload={
                "title": title,
                "source": {"branch": {"name": branch}},
                "destination": {
                    "branch": {"name": values.get("ANANKE_BITBUCKET_DEST_BRANCH", "main")}
                },
            },
            auth_header=auth_header,
        )
        attempts = str(response.get("attempts", 1))
        elapsed_ms = str(response.get("elapsed_ms", 0))
        retry_delays = str(response.get("retry_delays", []))
        if bool(response.get("ok", False)):
            record_remote_result(
                repository_root,
                "bitbucket",
                execution_mode,
                "remote_live_success",
                201,
            )
            result = {
                "status": "remote_live_success",
                "service": "bitbucket",
                "title": title,
                "branch": branch,
                "endpoint": endpoint,
                "method": "POST",
                "attempts": attempts,
                "elapsed_ms": elapsed_ms,
                "retry_delays": retry_delays,
            }
            append_remote_event(repository_root, "bitbucket", "pr-create", execution_mode, result)
            return result
        status_code = _status_code(response.get("status", 0))
        record_remote_result(
            repository_root,
            "bitbucket",
            execution_mode,
            "remote_live_error",
            status_code,
        )
        result = {
            "status": "remote_live_error",
            "service": "bitbucket",
            "title": title,
            "branch": branch,
            "endpoint": endpoint,
            "http_status": str(status_code),
            "error": str(response.get("error", "request failed")),
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
            "retry_delays": retry_delays,
        }
        append_remote_event(repository_root, "bitbucket", "pr-create", execution_mode, result)
        return result
    return {
        "status": "remote_dry_run",
        "service": "bitbucket",
        "title": title,
        "branch": branch,
        "endpoint": endpoint,
        "method": "POST",
    }

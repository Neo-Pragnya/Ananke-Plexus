"""Confluence lifecycle baseline with remote-dry-run adapter support."""

from base64 import b64encode
from pathlib import Path

from ananke.plexus.config.credentials import read_credentials_store, resolve_secret_value
from ananke.plexus.lifecycle.adapters import resolve_mode
from ananke.plexus.lifecycle.evidence import append_remote_event
from ananke.plexus.lifecycle.http_client import json_request
from ananke.plexus.lifecycle.store import read_store, write_store
from ananke.plexus.lifecycle.telemetry import can_execute_remote, record_remote_result


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


def _remote_upsert_page(
    repository_root: Path,
    space_key: str,
    title: str,
    content_path: str,
    execution_mode: str = "remote-dry-run",
) -> dict[str, str]:
    values = read_credentials_store(repository_root)
    missing = []
    for key in [
        "ANANKE_CONFLUENCE_BASE_URL",
    ]:
        if not values.get(key, "").strip():
            missing.append(key)
    confluence_bearer = resolve_secret_value(
        values,
        "ANANKE_CONFLUENCE_BEARER_TOKEN",
        "ANANKE_CONFLUENCE_BEARER_TOKEN_CMD",
    )
    confluence_token = resolve_secret_value(
        values,
        "ANANKE_CONFLUENCE_TOKEN",
        "ANANKE_CONFLUENCE_TOKEN_CMD",
    )
    confluence_email = values.get("ANANKE_CONFLUENCE_EMAIL", "").strip()
    if not confluence_bearer and not (confluence_email and confluence_token):
        missing.append(
            "ANANKE_CONFLUENCE_BEARER_TOKEN|ANANKE_CONFLUENCE_BEARER_TOKEN_CMD|"
            "(ANANKE_CONFLUENCE_EMAIL+ANANKE_CONFLUENCE_TOKEN|ANANKE_CONFLUENCE_TOKEN_CMD)"
        )
    if missing:
        return {
            "status": "missing_credentials",
            "service": "confluence",
            "missing": ",".join(missing),
        }

    base_url = values["ANANKE_CONFLUENCE_BASE_URL"].rstrip("/")
    endpoint = f"{base_url}/rest/api/content"
    if execution_mode == "remote-live":
        policy_allowed, policy_reason = _remote_live_policy(values, "confluence")
        if not policy_allowed:
            result = {
                "status": "policy_blocked",
                "service": "confluence",
                "reason": policy_reason,
            }
            append_remote_event(
                repository_root,
                "confluence",
                "confluence-upsert",
                execution_mode,
                result,
            )
            return result
        allowed, wait_seconds = can_execute_remote(repository_root, "confluence")
        if not allowed:
            result = {
                "status": "circuit_open",
                "service": "confluence",
                "retry_after_seconds": str(wait_seconds),
            }
            append_remote_event(
                repository_root,
                "confluence",
                "confluence-upsert",
                execution_mode,
                result,
            )
            return result
        page_text = ""
        path_obj = Path(content_path)
        if path_obj.exists() and path_obj.is_file():
            page_text = path_obj.read_text(encoding="utf-8")
        auth_header = (
            f"Bearer {confluence_bearer}"
            if confluence_bearer
            else _basic_auth_header(values["ANANKE_CONFLUENCE_EMAIL"], confluence_token)
        )
        response = json_request(
            url=endpoint,
            method="POST",
            payload={
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {
                    "storage": {
                        "value": page_text,
                        "representation": "storage",
                    }
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
                "confluence",
                execution_mode,
                "remote_live_success",
                200,
            )
            result = {
                "status": "remote_live_success",
                "service": "confluence",
                "space": space_key,
                "title": title,
                "endpoint": endpoint,
                "method": "POST",
                "attempts": attempts,
                "elapsed_ms": elapsed_ms,
                "retry_delays": retry_delays,
            }
            append_remote_event(
                repository_root,
                "confluence",
                "confluence-upsert",
                execution_mode,
                result,
            )
            return result
        status_code = _status_code(response.get("status", 0))
        record_remote_result(
            repository_root,
            "confluence",
            execution_mode,
            "remote_live_error",
            status_code,
        )
        result = {
            "status": "remote_live_error",
            "service": "confluence",
            "space": space_key,
            "title": title,
            "endpoint": endpoint,
            "http_status": str(status_code),
            "error": str(response.get("error", "request failed")),
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
            "retry_delays": retry_delays,
        }
        append_remote_event(
            repository_root,
            "confluence",
            "confluence-upsert",
            execution_mode,
            result,
        )
        return result
    return {
        "status": "remote_dry_run",
        "service": "confluence",
        "space": space_key,
        "title": title,
        "endpoint": endpoint,
        "method": "POST",
    }


def upsert_page(
    repository_root: Path,
    space_key: str,
    title: str,
    content_path: str,
    idem_key: str,
    mode: str = "auto",
) -> dict[str, str]:
    payload = read_store(repository_root)
    operations = payload.get("operations", {})
    if not isinstance(operations, dict):
        operations = {}

    if idem_key in operations:
        return {
            "status": "idempotent_replay",
            "service": "confluence",
            "space": space_key,
            "title": title,
        }

    selected_mode, mode_reason = resolve_mode(repository_root, mode)
    if selected_mode in {"remote-dry-run", "remote-live"}:
        result = _remote_upsert_page(
            repository_root,
            space_key,
            title,
            content_path,
            execution_mode=selected_mode,
        )
    else:
        result = {
            "status": "upserted",
            "service": "confluence",
            "space": space_key,
            "title": title,
            "content": content_path,
        }

    latest = read_store(repository_root)
    latest_operations = latest.get("operations", {}) if isinstance(latest, dict) else {}
    if not isinstance(latest_operations, dict):
        latest_operations = {}

    latest_operations[idem_key] = {
        "kind": "confluence_upsert",
        "mode": selected_mode,
        "mode_reason": mode_reason,
        "space": space_key,
        "title": title,
        "content": content_path,
        "result": result,
    }
    latest["operations"] = latest_operations
    write_store(repository_root, latest)

    result["mode"] = selected_mode
    result["mode_reason"] = mode_reason
    return result

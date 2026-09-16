"""Telemetry and circuit-breaker state for lifecycle remote operations."""

from __future__ import annotations

from pathlib import Path
from time import time
from typing import cast

from ananke.plexus.lifecycle.store import read_store, write_store

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 120


def _to_int(value: object, default: int = 0) -> int:
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
            return default
    return default


def _to_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _get_service_state(payload: dict[str, object], service: str) -> dict[str, object]:
    telemetry = payload.get("telemetry", {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    services = telemetry.get("services", {})
    if not isinstance(services, dict):
        services = {}
    state = services.get(service, {})
    if not isinstance(state, dict):
        state = {}
    return cast(dict[str, object], state)


def _set_service_state(payload: dict[str, object], service: str, state: dict[str, object]) -> None:
    telemetry = payload.get("telemetry", {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    services = telemetry.get("services", {})
    if not isinstance(services, dict):
        services = {}
    services[service] = state
    telemetry["services"] = services
    payload["telemetry"] = telemetry


def can_execute_remote(repository_root: Path, service: str) -> tuple[bool, int]:
    payload = read_store(repository_root)
    state = _get_service_state(payload, service)

    consecutive_failures = _to_int(state.get("consecutive_failures", 0))
    last_failure_ts = _to_float(state.get("last_failure_ts", 0.0))
    now_ts = time()

    if consecutive_failures < FAILURE_THRESHOLD:
        return True, 0
    remaining = int(COOLDOWN_SECONDS - (now_ts - last_failure_ts))
    if remaining <= 0:
        return True, 0
    return False, remaining


def record_remote_result(
    repository_root: Path,
    service: str,
    mode: str,
    status: str,
    http_status: int,
) -> None:
    payload = read_store(repository_root)
    state = _get_service_state(payload, service)
    now_ts = time()

    attempts = _to_int(state.get("attempts", 0)) + 1
    consecutive_failures = _to_int(state.get("consecutive_failures", 0))
    success = status == "remote_live_success"

    if success:
        consecutive_failures = 0
    else:
        consecutive_failures += 1
        state["last_failure_ts"] = now_ts

    state["attempts"] = attempts
    state["consecutive_failures"] = consecutive_failures
    state["last_mode"] = mode
    state["last_status"] = status
    state["last_http_status"] = http_status
    state["updated_at_ts"] = now_ts
    _set_service_state(payload, service, state)
    write_store(repository_root, payload)


def telemetry_snapshot(repository_root: Path) -> dict[str, object]:
    payload = read_store(repository_root)
    telemetry = payload.get("telemetry", {})
    if not isinstance(telemetry, dict):
        return {"services": {}}
    return telemetry

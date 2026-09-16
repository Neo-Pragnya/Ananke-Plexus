"""Evidence log helpers for remote lifecycle operations."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _to_int(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_ts(value: object) -> datetime | None:
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def derive_event_severity(status: str, http_status: str = "") -> str:
    normalized = status.strip().lower()
    status_code = _to_int(http_status)

    if normalized == "remote_live_success":
        return "info"
    if normalized in {"policy_blocked", "remote_live_degraded"}:
        return "warning"
    if normalized in {"remote_live_error", "circuit_open"}:
        return "critical"
    if status_code >= 500:
        return "critical"
    if status_code >= 400:
        return "warning"
    return "info"


def _log_path(repository_root: Path) -> Path:
    path = repository_root / ".ananke" / "evidence" / "lifecycle-remote-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_remote_event(
    repository_root: Path,
    service: str,
    operation: str,
    mode: str,
    result: Mapping[str, object],
) -> Path:
    attempts_raw = result.get("attempts", 0)
    attempts = int(attempts_raw) if isinstance(attempts_raw, (int, float, str)) else 0
    elapsed_raw = result.get("elapsed_ms", 0)
    elapsed_ms = int(elapsed_raw) if isinstance(elapsed_raw, (int, float, str)) else 0

    event = {
        "ts": datetime.now(UTC).isoformat(),
        "service": service,
        "operation": operation,
        "mode": mode,
        "status": str(result.get("status", "unknown")),
        "endpoint": str(result.get("endpoint", "")),
        "http_status": str(result.get("http_status", "")),
        "severity": derive_event_severity(
            str(result.get("status", "unknown")),
            str(result.get("http_status", "")),
        ),
        "attempts": attempts,
        "elapsed_ms": elapsed_ms,
        "retry_delays": result.get("retry_delays", []),
        "mode_reason": str(result.get("mode_reason", "")),
    }
    path = _log_path(repository_root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    return path


def read_remote_events(
    repository_root: Path,
    service: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 20,
    since_hours: float | None = None,
) -> list[dict[str, object]]:
    path = _log_path(repository_root)
    if not path.exists():
        return []

    events: list[dict[str, object]] = []
    requested_service = (service or "").strip()
    requested_status = (status or "").strip()
    requested_severity = (severity or "").strip().lower()
    threshold: datetime | None = None
    if since_hours is not None and since_hours > 0:
        threshold = datetime.now(UTC) - timedelta(hours=since_hours)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        event_service = str(parsed.get("service", "")).strip()
        event_status = str(parsed.get("status", "")).strip()
        event_severity = str(parsed.get("severity", "")).strip().lower()
        if not event_severity:
            event_severity = derive_event_severity(
                event_status,
                str(parsed.get("http_status", "")),
            )
            parsed["severity"] = event_severity
        if requested_service and event_service != requested_service:
            continue
        if requested_status and event_status != requested_status:
            continue
        if requested_severity and event_severity != requested_severity:
            continue
        if threshold is not None:
            parsed_ts = _parse_ts(parsed.get("ts", ""))
            if parsed_ts is None:
                continue
            if parsed_ts < threshold:
                continue
        events.append(parsed)

    keep = max(0, limit)
    if keep == 0:
        return events
    return events[-keep:]


def summarize_remote_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], int] = defaultdict(int)
    for event in events:
        service = str(event.get("service", "")).strip() or "unknown"
        status = str(event.get("status", "")).strip() or "unknown"
        grouped[(service, status)] += 1

    rows: list[dict[str, object]] = []
    for service, status in sorted(grouped.keys()):
        rows.append(
            {
                "service": service,
                "status": status,
                "count": grouped[(service, status)],
            }
        )
    return rows


def summarize_remote_event_trends(
    events: list[dict[str, object]],
    bucket: str,
) -> list[dict[str, object]]:
    use_day = bucket == "day"
    grouped: dict[tuple[str, str, str, str], int] = defaultdict(int)

    for event in events:
        parsed_ts = _parse_ts(event.get("ts", ""))
        if parsed_ts is None:
            continue
        slot = parsed_ts.strftime("%Y-%m-%d") if use_day else parsed_ts.strftime("%Y-%m-%d %H:00")
        service = str(event.get("service", "")).strip() or "unknown"
        status = str(event.get("status", "")).strip() or "unknown"
        severity = str(event.get("severity", "")).strip() or derive_event_severity(
            status,
            str(event.get("http_status", "")),
        )
        grouped[(slot, service, status, severity)] += 1

    rows: list[dict[str, object]] = []
    for slot, service, status, severity in sorted(grouped.keys()):
        rows.append(
            {
                "bucket": slot,
                "service": service,
                "status": status,
                "severity": severity,
                "count": grouped[(slot, service, status, severity)],
            }
        )
    return rows


def write_remote_events_csv(
    rows: list[dict[str, object]],
    csv_path: Path,
    aggregate: bool = False,
    trend: bool = False,
) -> Path:
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if trend:
        fieldnames = ["bucket", "service", "status", "severity", "count"]
    elif aggregate:
        fieldnames = ["service", "status", "count"]
    else:
        fieldnames = [
            "ts",
            "service",
            "operation",
            "mode",
            "status",
            "severity",
            "endpoint",
            "http_status",
            "attempts",
            "elapsed_ms",
            "retry_delays",
            "mode_reason",
        ]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            if trend:
                writer.writerow(
                    {
                        "bucket": str(row.get("bucket", "")),
                        "service": str(row.get("service", "")),
                        "status": str(row.get("status", "")),
                        "severity": str(row.get("severity", "")),
                        "count": _to_int(row.get("count", 0)),
                    }
                )
                continue
            if aggregate:
                writer.writerow(
                    {
                        "service": str(row.get("service", "")),
                        "status": str(row.get("status", "")),
                        "count": _to_int(row.get("count", 0)),
                    }
                )
                continue
            retry_delays = row.get("retry_delays", "")
            retry_text = (
                json.dumps(retry_delays) if isinstance(retry_delays, list) else str(retry_delays)
            )
            writer.writerow(
                {
                    "ts": str(row.get("ts", "")),
                    "service": str(row.get("service", "")),
                    "operation": str(row.get("operation", "")),
                    "mode": str(row.get("mode", "")),
                    "status": str(row.get("status", "")),
                    "severity": str(row.get("severity", "")),
                    "endpoint": str(row.get("endpoint", "")),
                    "http_status": str(row.get("http_status", "")),
                    "attempts": _to_int(row.get("attempts", 0)),
                    "elapsed_ms": _to_int(row.get("elapsed_ms", 0)),
                    "retry_delays": retry_text,
                    "mode_reason": str(row.get("mode_reason", "")),
                }
            )
    return csv_path

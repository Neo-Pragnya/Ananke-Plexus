"""Run state persistence for autonomous execution baseline."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

RUN_STATES = {
    "PENDING",
    "READY",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "BLOCKED",
    "SKIPPED",
    "COMPENSATING",
    "COMPENSATED",
    "CANCELLED",
}


def start_run(repository_root: Path, spec_id: str) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = repository_root / ".ananke" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_id": run_id,
        "spec_id": spec_id,
        "state": "READY",
        "created_at": datetime.now(UTC).isoformat(),
        "steps": [
            {"step": "read_context", "state": "SUCCEEDED"},
            {"step": "generate_contracts", "state": "SUCCEEDED"},
            {"step": "run_gate", "state": "SUCCEEDED"},
        ],
    }

    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return state_path


def read_run_state(repository_root: Path, run_id: str) -> dict[str, object]:
    state_path = repository_root / ".ananke" / "runs" / run_id / "state.json"
    if not state_path.exists():
        return {}
    parsed = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return cast(dict[str, object], parsed)


def update_run_state(repository_root: Path, run_id: str, state: str) -> dict[str, object]:
    if state not in RUN_STATES:
        return {}

    payload = read_run_state(repository_root, run_id)
    if not payload:
        return {}

    payload["state"] = state
    payload["updated_at"] = datetime.now(UTC).isoformat()
    state_path = repository_root / ".ananke" / "runs" / run_id / "state.json"
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def replay_run(repository_root: Path, run_id: str) -> dict[str, object]:
    payload = read_run_state(repository_root, run_id)
    if not payload:
        return {}
    payload["state"] = "READY"
    payload["replayed_at"] = datetime.now(UTC).isoformat()
    state_path = repository_root / ".ananke" / "runs" / run_id / "state.json"
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload

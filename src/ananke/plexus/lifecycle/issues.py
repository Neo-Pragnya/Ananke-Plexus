"""Issue tracker lifecycle baseline (local idempotent simulation)."""

from pathlib import Path

from ananke.plexus.lifecycle.adapters import remote_transition_issue, resolve_mode
from ananke.plexus.lifecycle.store import read_store, write_store


def transition_issue(
    repository_root: Path,
    issue_key: str,
    target_state: str,
    idem_key: str,
    mode: str = "auto",
) -> dict[str, str]:
    payload = read_store(repository_root)
    operations = payload.get("operations", {})
    if not isinstance(operations, dict):
        operations = {}

    if idem_key in operations:
        return {"status": "idempotent_replay", "issue": issue_key, "state": target_state}

    selected_mode, mode_reason = resolve_mode(repository_root, mode)
    result: dict[str, str]
    if selected_mode in {"remote-dry-run", "remote-live"}:
        result = remote_transition_issue(
            repository_root,
            issue_key,
            target_state,
            execution_mode=selected_mode,
        )
    else:
        result = {"status": "transitioned", "issue": issue_key, "state": target_state}

    latest = read_store(repository_root)
    latest_operations = latest.get("operations", {}) if isinstance(latest, dict) else {}
    if not isinstance(latest_operations, dict):
        latest_operations = {}

    latest_operations[idem_key] = {
        "kind": "issue_transition",
        "mode": selected_mode,
        "mode_reason": mode_reason,
        "issue": issue_key,
        "state": target_state,
        "result": result,
    }
    latest["operations"] = latest_operations
    write_store(repository_root, latest)
    result["mode"] = selected_mode
    result["mode_reason"] = mode_reason
    return result

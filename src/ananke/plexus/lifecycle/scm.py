"""SCM lifecycle baseline (local idempotent simulation)."""

from pathlib import Path

from ananke.plexus.lifecycle.adapters import remote_create_pr, resolve_mode
from ananke.plexus.lifecycle.store import read_store, write_store


def create_pr(
    repository_root: Path,
    title: str,
    branch: str,
    idem_key: str,
    mode: str = "auto",
) -> dict[str, str]:
    payload = read_store(repository_root)
    operations = payload.get("operations", {})
    if not isinstance(operations, dict):
        operations = {}

    if idem_key in operations:
        return {"status": "idempotent_replay", "title": title, "branch": branch}

    selected_mode, mode_reason = resolve_mode(repository_root, mode)
    result: dict[str, str]
    if selected_mode in {"remote-dry-run", "remote-live"}:
        result = remote_create_pr(
            repository_root,
            title,
            branch,
            execution_mode=selected_mode,
        )
    else:
        pr_id = f"PR-{len(operations) + 1}"
        result = {"status": "created", "pr_id": pr_id, "title": title, "branch": branch}

    latest = read_store(repository_root)
    latest_operations = latest.get("operations", {}) if isinstance(latest, dict) else {}
    if not isinstance(latest_operations, dict):
        latest_operations = {}

    latest_operations[idem_key] = {
        "kind": "create_pr",
        "mode": selected_mode,
        "mode_reason": mode_reason,
        "title": title,
        "branch": branch,
        "result": result,
    }
    latest["operations"] = latest_operations
    write_store(repository_root, latest)
    result["mode"] = selected_mode
    result["mode_reason"] = mode_reason
    return result

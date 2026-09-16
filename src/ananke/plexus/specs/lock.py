"""Spec lock and drift detection helpers."""

import json
from hashlib import sha256
from pathlib import Path
from typing import cast

LOCK_FILE = "spec.lock.json"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_spec_lock(feature_dir: Path) -> Path:
    requirement_md = feature_dir / "requirement.md"
    spec_md = feature_dir / "spec.md"
    plan_md = feature_dir / "plan.md"
    tasks_md = feature_dir / "tasks.md"
    bmad_yaml = feature_dir / "bmad.yaml"

    files = {
        "requirement.md": _sha(requirement_md) if requirement_md.exists() else "",
        "spec.md": _sha(spec_md) if spec_md.exists() else "",
        "plan.md": _sha(plan_md) if plan_md.exists() else "",
        "tasks.md": _sha(tasks_md) if tasks_md.exists() else "",
        "bmad.yaml": _sha(bmad_yaml) if bmad_yaml.exists() else "",
    }

    joined = "".join(f"{name}:{digest}" for name, digest in sorted(files.items()))
    content_hash = sha256(joined.encode("utf-8")).hexdigest()

    payload = {
        "feature_dir": feature_dir.name,
        "content_hash": content_hash,
        "files": files,
    }

    lock = feature_dir / LOCK_FILE
    lock.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return lock


def load_spec_lock(feature_dir: Path) -> dict[str, object]:
    lock = feature_dir / LOCK_FILE
    if not lock.exists():
        return {}
    parsed = json.loads(lock.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return cast(dict[str, object], parsed)


def detect_drift(feature_dir: Path) -> list[str]:
    lock_data = load_spec_lock(feature_dir)
    if not lock_data:
        return ["LOCK_MISSING"]

    locked_files = lock_data.get("files", {})
    if not isinstance(locked_files, dict):
        return ["LOCK_INVALID"]

    drift: list[str] = []
    for filename, expected_hash in locked_files.items():
        if not isinstance(filename, str) or not isinstance(expected_hash, str):
            drift.append("LOCK_INVALID")
            continue
        file_path = feature_dir / filename
        if not file_path.exists():
            drift.append(f"MISSING:{filename}")
            continue
        actual_hash = _sha(file_path)
        if expected_hash and actual_hash != expected_hash:
            drift.append(f"HASH_MISMATCH:{filename}")

    return drift

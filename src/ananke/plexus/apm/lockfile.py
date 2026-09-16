"""APM lockfile management."""

import json
from pathlib import Path
from typing import cast

LOCK_NAME = "apm.lock"


def read_lock(repository_root: Path) -> dict[str, object]:
    path = repository_root / ".ananke" / LOCK_NAME
    if not path.exists():
        return {"packages": []}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return {"packages": []}
    return cast(dict[str, object], parsed)


def write_lock(repository_root: Path, payload: dict[str, object]) -> Path:
    path = repository_root / ".ananke" / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path

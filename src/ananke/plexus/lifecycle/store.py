"""Idempotency store for lifecycle mutations."""

import json
from pathlib import Path
from typing import cast

STORE_FILE = "lifecycle.json"


def _path(repository_root: Path) -> Path:
    path = repository_root / ".ananke" / "state" / STORE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_store(repository_root: Path) -> dict[str, object]:
    path = _path(repository_root)
    if not path.exists():
        return {"operations": {}}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return {"operations": {}}
    return cast(dict[str, object], parsed)


def write_store(repository_root: Path, payload: dict[str, object]) -> Path:
    path = _path(repository_root)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path

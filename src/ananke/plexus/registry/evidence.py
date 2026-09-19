"""Registry ↔ evidence integration (spec §139).

Every governed run can record *exactly* which capability versions were in play: agent and
skill URIs, versions, payload digests, the registry snapshot and the lockfile hash — enough
for evidence to reconstruct the capability graph later. The payload is derived from
``ananke.lock`` alone, so no registry database is needed to write or verify it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ananke.plexus.registry.lockfile import LOCK_NAME, dump_lock, load_lock, lock_hash
from ananke.plexus.registry.models import ArtifactKind, parse_ref

EVIDENCE_FILE = "registry-capabilities.json"


def project_capability_evidence(
    project_root: Path, lock_path: Path | None = None
) -> dict[str, Any] | None:
    path = lock_path or (project_root / LOCK_NAME)
    if not path.is_file():
        return None
    lock = load_lock(path)
    agents = [e.ref for e in lock.entries if parse_ref(e.id).kind is ArtifactKind.AGENT]
    return {
        "schema": 1,
        "lockfile": path.name,
        "lock_hash": lock_hash(dump_lock(lock)),
        "registry_snapshot": lock.registry_snapshot,
        "resolution_mode": lock.mode,
        "agents": agents,
        "artifacts": [
            {
                "uri": e.id,
                "version": e.version,
                "digest": e.digest,
                "source": e.source,
                "dirty": e.dirty,
            }
            for e in lock.entries
        ],
    }


def write_run_evidence(
    project_root: Path, run_dir: Path, lock_path: Path | None = None
) -> Path | None:
    payload = project_capability_evidence(project_root, lock_path)
    if payload is None:
        return None
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / EVIDENCE_FILE
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target

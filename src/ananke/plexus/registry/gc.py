"""Garbage collection of unreachable blobs (spec §95).

Reachable = blobs of every registered version (active, yanked, deprecated, quarantined,
archived — history is never collected), blobs pinned by recorded lockfiles, and blobs named
in run evidence. Only *unreachable* blobs older than the grace period are deleted.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


class GcReport(BaseModel):
    dry_run: bool
    unreachable: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    within_grace: int = 0
    reachable: int = 0
    bytes_freed: int = 0
    materialized_removed: list[str] = Field(default_factory=list)
    temp_files_removed: int = 0


def _evidence_digests(project: Path) -> set[str]:
    out: set[str] = set()
    for f in (project / ".ananke" / "evidence").glob("*/registry-capabilities.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for a in data.get("artifacts", []):
            digest = str(a.get("digest", ""))
            out.add(digest.split(":", 1)[-1])
    return out


def gc(
    registry: Registry,
    *,
    dry_run: bool = True,
    grace_seconds: float = 7 * 86400,
    project_roots: list[Path] | None = None,
) -> GcReport:
    reachable = {r.digest_sha256 for r in registry.store.list_records()}
    reachable |= registry.store.locked_digests()
    roots = list(project_roots or [])
    if registry.project_root is not None:
        roots.append(registry.project_root)
    for root in roots:
        reachable |= _evidence_digests(root)
    report = GcReport(dry_run=dry_run, reachable=len(reachable))
    now = time.time()
    for digest in registry.cas.iter_digests():
        if digest in reachable:
            continue
        report.unreachable.append(digest)
        if now - registry.cas.mtime(digest) < grace_seconds:
            report.within_grace += 1
            continue
        report.bytes_freed += registry.cas.size(digest)
        if not dry_run:
            registry.cas.delete(digest)
            report.deleted.append(digest)
    activated = {a["digest"] for a in registry.store.list_activations()}
    mat = registry.root / "materialized"
    if mat.is_dir():
        for d in sorted(mat.iterdir()):
            if d.is_dir() and d.name not in activated and not d.name.startswith("."):
                report.materialized_removed.append(d.name)
                if not dry_run:
                    shutil.rmtree(d, ignore_errors=True)
    for tmp in registry.cas.stale_temp_files():
        report.temp_files_removed += 1
        if not dry_run:
            tmp.unlink(missing_ok=True)
    return report

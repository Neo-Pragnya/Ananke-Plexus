"""Evidence retention operations."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.core.paths import project_ananke_dir

RUN_DIR_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
RETENTION_PATTERN = re.compile(r"^(\d+)([dhm])$")


def parse_retention_duration(value: str) -> int:
    match = RETENTION_PATTERN.match(value.strip().lower())
    if not match:
        raise ValueError("Invalid duration. Use values like 30d, 12h, or 90m.")
    amount = int(match.group(1))
    unit = match.group(2)
    factors = {"d": 86400, "h": 3600, "m": 60}
    return amount * factors[unit]


def _is_git_tracked(repository_root: Path, target_path: Path) -> bool:
    git_dir = repository_root / ".git"
    if not git_dir.exists():
        return False
    git_bin = shutil.which("git")
    if not git_bin:
        return False
    try:
        rel = target_path.relative_to(repository_root).as_posix()
    except ValueError:
        return False
    result = subprocess.run(  # noqa: S603 - only used for local git-tracked file detection
        [git_bin, "-C", str(repository_root), "ls-files", "--error-unmatch", rel],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def prune_evidence(
    repository_root: Path,
    older_than: str,
    apply: bool = False,
) -> dict[str, object]:
    evidence_dir = project_ananke_dir(repository_root) / "evidence"
    if not evidence_dir.exists():
        return {
            "ok": True,
            "mode": "apply" if apply else "dry-run",
            "older_than": older_than,
            "candidate_count": 0,
            "prunable_count": 0,
            "pruned_count": 0,
            "skipped_tracked_count": 0,
        }

    cutoff_seconds = parse_retention_duration(older_than)
    now = datetime.now(UTC).timestamp()

    candidates: list[Path] = []
    skipped_tracked = 0
    for entry in evidence_dir.iterdir():
        if not entry.is_dir() or not RUN_DIR_PATTERN.match(entry.name):
            continue
        age_seconds = max(0.0, now - entry.stat().st_mtime)
        if age_seconds < cutoff_seconds:
            continue
        if _is_git_tracked(repository_root, entry):
            skipped_tracked += 1
            continue
        candidates.append(entry)

    pruned_count = 0
    if apply:
        for path in candidates:
            shutil.rmtree(path)
            pruned_count += 1

    details: dict[str, object] = {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "older_than": older_than,
        "candidate_count": len(candidates),
        "prunable_count": len(candidates),
        "pruned_count": pruned_count,
        "skipped_tracked_count": skipped_tracked,
    }
    for index, path in enumerate(candidates[:20], start=1):
        details[f"target_{index}"] = str(path)
    return details

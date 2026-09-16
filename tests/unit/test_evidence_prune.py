import os
import time
from pathlib import Path

from ananke.plexus.api import Ananke


def test_evidence_prune_dry_run_and_apply(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    evidence_dir = tmp_path / ".ananke" / "evidence"
    old_dir = evidence_dir / "20250801T010101Z-1234abcd"
    new_dir = evidence_dir / "20260916T010101Z-deadbeef"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)

    stale = time.time() - (45 * 86400)
    os.utime(old_dir, (stale, stale))

    dry_run = app.evidence_prune("30d", apply=False)
    assert dry_run.ok
    assert int(dry_run.details["prunable_count"]) == 1
    assert old_dir.exists()

    applied = app.evidence_prune("30d", apply=True)
    assert applied.ok
    assert int(applied.details["pruned_count"]) == 1
    assert not old_dir.exists()
    assert new_dir.exists()


def test_evidence_prune_invalid_duration(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    result = app.evidence_prune("xyz", apply=False)
    assert not result.ok
    assert "Invalid duration" in str(result.details.get("error", ""))

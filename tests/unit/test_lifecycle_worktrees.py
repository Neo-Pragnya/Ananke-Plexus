from pathlib import Path

from ananke.plexus.api import Ananke


def test_run_start_creates_worktree_and_lists(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    started = app.run_start("SPEC-1")
    assert started.ok

    listed = app.lifecycle_worktrees()
    assert listed.ok
    assert int(listed.details["count"]) >= 1

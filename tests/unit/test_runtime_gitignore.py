"""Credentials and run state must never be picked up by `git add -A`."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.core.paths import RUNTIME_IGNORES, ensure_runtime_gitignore


def test_init_ignores_secrets_and_runtime_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    Ananke.open(tmp_path).init_project()
    (tmp_path / ".ananke" / "secrets" / "adapters.env").write_text("ANANKE_JIRA_TOKEN=x\n")
    (tmp_path / ".ananke" / "runs" / "r1" / "worktree").mkdir(parents=True)
    (tmp_path / ".ananke" / "runs" / "r1" / "worktree" / "f.txt").write_text("x")
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "secrets/" not in out and "config.local.toml\n" not in out and "runs/" not in out
    assert ".ananke/config.toml" in out  # shared config stays trackable


def test_existing_ignore_file_is_extended_not_replaced(tmp_path: Path) -> None:
    ignore = tmp_path / ".ananke" / ".gitignore"
    ignore.parent.mkdir()
    ignore.write_text("custom-entry/\nruns/\n")
    ensure_runtime_gitignore(tmp_path)
    lines = ignore.read_text().splitlines()
    assert lines[0] == "custom-entry/"
    assert all(entry in lines for entry in RUNTIME_IGNORES)
    assert lines.count("runs/") == 1

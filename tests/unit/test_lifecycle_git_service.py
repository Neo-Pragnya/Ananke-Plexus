"""Tests for the git service (J1)."""

import subprocess
from pathlib import Path

from ananke.plexus.lifecycle.git import (
    current_branch,
    current_commit,
    git_diff_stat,
    make_branch_name,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=False)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=False)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=False)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"], capture_output=True, check=False
    )


def test_make_branch_name_basic():
    assert make_branch_name("feature", "X-1", "my feature") == "feature/X-1-my-feature"


def test_current_branch_in_git_repo(tmp_path):
    _init_git_repo(tmp_path)
    branch = current_branch(tmp_path)
    assert branch in ("main", "master", "HEAD")


def test_current_commit_in_git_repo(tmp_path):
    _init_git_repo(tmp_path)
    commit = current_commit(tmp_path)
    assert len(commit) == 40 or commit == "unknown"


def test_current_branch_no_git(tmp_path):
    branch = current_branch(tmp_path)
    assert branch == "unknown"


def test_current_commit_no_git(tmp_path):
    commit = current_commit(tmp_path)
    assert commit == "unknown"


def test_git_diff_stat_empty(tmp_path):
    # no git repo
    result = git_diff_stat(tmp_path)
    assert result == []


def test_git_diff_stat_in_repo(tmp_path):
    _init_git_repo(tmp_path)
    result = git_diff_stat(tmp_path)
    assert isinstance(result, list)

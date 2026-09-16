"""Worktree lifecycle with real git worktree isolation (J2)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def create_isolated_worktree(
    repository_root: Path,
    run_id: str,
    *,
    branch_name: str | None = None,
    use_git_worktree: bool = True,
) -> Path:
    """Create an isolated worktree for a run.

    When ``use_git_worktree`` is True and git is available, uses ``git worktree add``
    so the run operates in a proper, separately-checked-out branch.
    Falls back to a plain directory otherwise.
    """
    worktree_path = repository_root / ".ananke" / "runs" / run_id / "worktree"

    if use_git_worktree:
        branch = branch_name or f"ananke-run-{run_id}"
        result = subprocess.run(  # noqa: S603
            ["git", "worktree", "add", "-b", branch, str(worktree_path)],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return worktree_path

    worktree_path.mkdir(parents=True, exist_ok=True)
    return worktree_path


def remove_isolated_worktree(repository_root: Path, run_id: str) -> bool:
    """Remove the git worktree for a completed/cancelled run."""
    worktree_path = repository_root / ".ananke" / "runs" / run_id / "worktree"
    if not worktree_path.exists():
        return True

    result = subprocess.run(  # noqa: S603
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def list_worktrees(repository_root: Path) -> list[str]:
    """List all Ananke-managed run worktrees."""
    runs_root = repository_root / ".ananke" / "runs"
    if not runs_root.exists():
        return []
    result: list[str] = []
    for run_dir in sorted(item for item in runs_root.iterdir() if item.is_dir()):
        candidate = run_dir / "worktree"
        if candidate.exists():
            result.append(str(candidate))
    return result


def list_git_worktrees(repository_root: Path) -> list[dict[str, str]]:
    """Query git for all known worktrees."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            current["commit"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "":
            if current:
                worktrees.append(current)
                current = {}
    if current:
        worktrees.append(current)
    return worktrees

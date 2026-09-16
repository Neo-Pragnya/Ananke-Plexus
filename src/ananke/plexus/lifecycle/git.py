"""Git lifecycle service (J1).

All git operations use subprocess with static argument lists — never
build shell commands from untrusted strings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def make_branch_name(change_type: str, ticket: str, slug: str) -> str:
    safe_slug = "-".join(slug.lower().split())
    return f"{change_type}/{ticket}-{safe_slug}"


def create_branch(
    repository_root: Path,
    branch_name: str,
    *,
    from_ref: str = "HEAD",
    push: bool = False,
    remote: str = "origin",
) -> dict[str, object]:
    """Create a local git branch and optionally push it."""
    result = subprocess.run(  # noqa: S603
        ["git", "checkout", "-b", branch_name, from_ref],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    ok = result.returncode == 0
    output = (result.stdout + result.stderr).strip()
    if not ok:
        return {"ok": False, "branch": branch_name, "error": output[:300]}

    push_result: dict[str, object] = {}
    if push:
        push_result = push_branch(repository_root, branch_name, remote=remote)

    return {
        "ok": ok,
        "branch": branch_name,
        "output": output[:300],
        "pushed": push_result.get("ok", False) if push else None,
    }


def push_branch(
    repository_root: Path,
    branch_name: str,
    *,
    remote: str = "origin",
) -> dict[str, object]:
    result = subprocess.run(  # noqa: S603
        ["git", "push", "--set-upstream", remote, branch_name],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    ok = result.returncode == 0
    return {
        "ok": ok,
        "branch": branch_name,
        "remote": remote,
        "output": (result.stdout + result.stderr).strip()[:300],
    }


def current_branch(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def current_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def git_diff_stat(repository_root: Path, *, staged: bool = False) -> list[str]:
    args = ["git", "diff", "--name-only"]
    if staged:
        args.append("--cached")
    result = subprocess.run(  # noqa: S603
        args,
        cwd=str(repository_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]

"""Git hook installation, uninstallation, and chain management.

Rules enforced (Section 18):
- Never silently overwrite an existing unmanaged hook.
- Installation is reversible.
- Hooks never create a second commit.
- Post-commit may refresh ignored caches but must not mutate tracked files.
"""

from __future__ import annotations

import stat
from pathlib import Path

from ananke.plexus.hooks.models import HookMode, HookStage, HookStatus

_MANAGED_HEADER = "# ananke-managed-hook"

_WRAPPER_TEMPLATE = """\
# ananke-managed-hook
#!/usr/bin/env sh
exec ananke hooks run {stage} -- "$@"
"""

_CHAIN_TEMPLATE = """\
# ananke-managed-hook
#!/usr/bin/env sh
# Original hook preserved as {backup}
if [ -x "{backup}" ]; then
  "{backup}" "$@" || exit $?
fi
exec ananke hooks run {stage} -- "$@"
"""


def _hook_path(git_dir: Path, stage: HookStage) -> Path:
    return git_dir / "hooks" / stage


def _backup_path(git_dir: Path, stage: HookStage) -> Path:
    return git_dir / "hooks" / f"{stage}.ananke-backup"


def _find_git_dir(repository_root: Path) -> Path | None:
    candidate = repository_root / ".git"
    if candidate.is_dir():
        return candidate
    if candidate.is_file():
        ref = candidate.read_text(encoding="utf-8").strip()
        if ref.startswith("gitdir: "):
            return Path(ref[len("gitdir: ") :])
    return None


def _is_managed(hook_path: Path) -> bool:
    if not hook_path.exists():
        return False
    try:
        return hook_path.read_text(encoding="utf-8").startswith(_MANAGED_HEADER)
    except OSError:
        return False


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_hook(
    repository_root: Path,
    stage: HookStage,
    *,
    mode: HookMode = "native",
    chain: bool = False,
) -> HookStatus:
    git_dir = _find_git_dir(repository_root)
    if git_dir is None:
        return HookStatus(
            stage=stage,
            installed=False,
            managed=False,
            path="",
            mode=mode,
            chained=False,
        )

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = _hook_path(git_dir, stage)
    backup = _backup_path(git_dir, stage)

    if hook_path.exists() and not _is_managed(hook_path):
        if not chain:
            return HookStatus(
                stage=stage,
                installed=False,
                managed=False,
                path=str(hook_path),
                mode=mode,
                chained=False,
            )
        hook_path.rename(backup)
        content = _CHAIN_TEMPLATE.format(stage=stage, backup=str(backup))
    else:
        content = _WRAPPER_TEMPLATE.format(stage=stage)

    _write_executable(hook_path, content)
    return HookStatus(
        stage=stage,
        installed=True,
        managed=True,
        path=str(hook_path),
        mode=mode,
        chained=backup.exists(),
    )


def uninstall_hook(repository_root: Path, stage: HookStage) -> HookStatus:
    git_dir = _find_git_dir(repository_root)
    if git_dir is None:
        return HookStatus(stage=stage, installed=False, managed=False, path="", mode="native")

    hook_path = _hook_path(git_dir, stage)
    backup = _backup_path(git_dir, stage)

    if not _is_managed(hook_path):
        return HookStatus(
            stage=stage,
            installed=hook_path.exists(),
            managed=False,
            path=str(hook_path),
            mode="native",
        )

    hook_path.unlink(missing_ok=True)
    if backup.exists():
        backup.rename(hook_path)

    return HookStatus(
        stage=stage,
        installed=False,
        managed=False,
        path=str(hook_path),
        mode="native",
        chained=False,
    )


def hook_status(repository_root: Path, stage: HookStage) -> HookStatus:
    git_dir = _find_git_dir(repository_root)
    if git_dir is None:
        return HookStatus(stage=stage, installed=False, managed=False, path="", mode="native")

    hook_path = _hook_path(git_dir, stage)
    backup = _backup_path(git_dir, stage)
    managed = _is_managed(hook_path)
    return HookStatus(
        stage=stage,
        installed=hook_path.exists(),
        managed=managed,
        path=str(hook_path),
        mode="native",
        chained=backup.exists(),
    )


def all_hook_statuses(repository_root: Path) -> list[HookStatus]:
    stages: list[HookStage] = ["pre-commit", "post-commit", "pre-push"]
    return [hook_status(repository_root, s) for s in stages]


# ---------------------------------------------------------------------------
# G7 — pre-commit-framework and delegated modes
# ---------------------------------------------------------------------------

_PRE_COMMIT_HOOK_ENTRY = """\
- repo: local
  hooks:
    - id: ananke-pre-commit
      name: Ananke pre-commit checks
      language: system
      entry: ananke hooks run pre-commit
      pass_filenames: false
    - id: ananke-pre-push
      name: Ananke pre-push checks
      language: system
      entry: ananke hooks run pre-push
      pass_filenames: false
      stages: [push]
"""


def install_pre_commit_framework(repository_root: Path) -> dict[str, object]:
    """Add Ananke hooks to .pre-commit-config.yaml (G7 framework mode)."""
    config_path = repository_root / ".pre-commit-config.yaml"
    if config_path.exists():
        existing = config_path.read_text(encoding="utf-8")
        if "ananke-pre-commit" in existing:
            return {"installed": False, "reason": "already present", "path": str(config_path)}
        config_path.write_text(
            existing.rstrip() + "\n\n" + _PRE_COMMIT_HOOK_ENTRY, encoding="utf-8"
        )
    else:
        config_path.write_text(
            "repos:\n" + _PRE_COMMIT_HOOK_ENTRY,
            encoding="utf-8",
        )
    return {"installed": True, "path": str(config_path), "mode": "pre-commit-framework"}


def install_delegated(
    repository_root: Path,
    stage: HookStage,
    delegate_command: str,
) -> HookStatus:
    """Install a hook that delegates to an external command (G7 delegated mode)."""
    git_dir = _find_git_dir(repository_root)
    if git_dir is None:
        return HookStatus(stage=stage, installed=False, managed=False, path="", mode="delegated")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = _hook_path(git_dir, stage)

    content = (
        f"{_MANAGED_HEADER}\n"
        f"#!/usr/bin/env sh\n"
        f"# delegated to: {delegate_command}\n"
        f'exec {delegate_command} "$@"\n'
    )
    _write_executable(hook_path, content)
    return HookStatus(
        stage=stage,
        installed=True,
        managed=True,
        path=str(hook_path),
        mode="delegated",
    )

"""Tests for the git hooks engine (Epic G)."""

from pathlib import Path

from ananke.plexus.hooks.manager import (
    all_hook_statuses,
    hook_status,
    install_hook,
    install_pre_commit_framework,
    uninstall_hook,
)
from ananke.plexus.hooks.models import HookRunResult
from ananke.plexus.hooks.stages import run_stage


def _fake_git_repo(tmp_path: Path) -> Path:
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    return tmp_path


def test_hook_status_no_git(tmp_path):
    status = hook_status(tmp_path, "pre-commit")
    assert status.installed is False
    assert status.managed is False


def test_install_hook_native(tmp_path):
    repo = _fake_git_repo(tmp_path)
    result = install_hook(repo, "pre-commit")
    assert result.installed is True
    assert result.managed is True
    hook_file = repo / ".git" / "hooks" / "pre-commit"
    assert hook_file.exists()
    content = hook_file.read_text(encoding="utf-8")
    assert "ananke-managed-hook" in content
    assert "ananke hooks run pre-commit" in content


def test_install_hook_refuses_unmanaged(tmp_path):
    repo = _fake_git_repo(tmp_path)
    existing = repo / ".git" / "hooks" / "pre-commit"
    existing.write_text("#!/bin/sh\necho custom hook\n")

    result = install_hook(repo, "pre-commit")
    assert result.installed is False

    # chain=True should preserve backup
    result2 = install_hook(repo, "pre-commit", chain=True)
    assert result2.installed is True
    backup = repo / ".git" / "hooks" / "pre-commit.ananke-backup"
    assert backup.exists()


def test_uninstall_managed_hook(tmp_path):
    repo = _fake_git_repo(tmp_path)
    install_hook(repo, "pre-commit")
    result = uninstall_hook(repo, "pre-commit")
    assert result.installed is False
    hook_file = repo / ".git" / "hooks" / "pre-commit"
    assert not hook_file.exists()


def test_uninstall_restores_backup(tmp_path):
    repo = _fake_git_repo(tmp_path)
    existing = repo / ".git" / "hooks" / "pre-commit"
    existing.write_text("#!/bin/sh\necho original\n")
    install_hook(repo, "pre-commit", chain=True)
    uninstall_hook(repo, "pre-commit")

    restored = repo / ".git" / "hooks" / "pre-commit"
    assert restored.exists()
    assert "original" in restored.read_text(encoding="utf-8")


def test_all_hook_statuses(tmp_path):
    repo = _fake_git_repo(tmp_path)
    statuses = all_hook_statuses(repo)
    assert len(statuses) == 3
    stages = {s.stage for s in statuses}
    assert stages == {"pre-commit", "post-commit", "pre-push"}


def test_run_stage_pre_commit_returns_result(tmp_path):
    result = run_stage(tmp_path, "pre-commit")
    assert isinstance(result, HookRunResult)
    assert result.stage == "pre-commit"


def test_run_stage_post_commit_no_checks(tmp_path):
    result = run_stage(tmp_path, "post-commit")
    assert result.ok is True
    assert result.checks == []


def test_install_pre_commit_framework_creates_file(tmp_path):
    install_pre_commit_framework(tmp_path)
    cfg = tmp_path / ".pre-commit-config.yaml"
    assert cfg.exists()
    assert "ananke-pre-commit" in cfg.read_text(encoding="utf-8")


def test_install_pre_commit_framework_idempotent(tmp_path):
    install_pre_commit_framework(tmp_path)
    result = install_pre_commit_framework(tmp_path)
    assert result["installed"] is False
    assert "already present" in str(result.get("reason", ""))

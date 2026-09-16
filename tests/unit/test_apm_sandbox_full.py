"""Tests for the full APM sandbox enforcement (I5/I6)."""

import pytest

from ananke.plexus.apm.manifest import PermissionSection, SkillManifest
from ananke.plexus.apm.sandbox import (
    SandboxViolation,
    assert_shell,
    assert_write,
    check_network,
    check_read,
    check_shell,
    check_write,
    permission_allows_network,
    permission_allows_shell,
    permission_allows_write,
)


def _manifest(shell=None, network=None, fs_read=None, fs_write=None) -> SkillManifest:
    from ananke.plexus.apm.manifest import SkillSection

    return SkillManifest(
        skill=SkillSection(name="test-skill", version="1.0.0", description="test"),
        permissions=PermissionSection(
            shell=shell or [],
            network=network or [],
            filesystem_read=fs_read or [],
            filesystem_write=fs_write or [],
        ),
    )


# ---------- Shell ----------


def test_shell_allowed():
    m = _manifest(shell=["ananke *", "git diff"])
    ok, _reason = check_shell(m, "ananke graph build")
    assert ok is True


def test_shell_not_in_allowed():
    m = _manifest(shell=["ananke *"])
    ok, _reason = check_shell(m, "curl https://evil.com")
    assert ok is False


def test_shell_blocked_pattern_git():
    m = _manifest(shell=["git *"])
    ok, reason = check_shell(m, "git push --force")
    assert ok is False
    assert "blocked shell pattern" in reason


def test_shell_blocked_pattern_curl():
    m = _manifest(shell=["curl *"])
    ok, _reason = check_shell(m, "curl http://example.com")
    assert ok is False


def test_shell_no_permissions():
    m = _manifest()
    ok, _reason = check_shell(m, "echo hello")
    assert ok is False


def test_assert_shell_raises():
    m = _manifest()
    with pytest.raises(SandboxViolation, match="Shell sandbox violation"):
        assert_shell(m, "rm -rf /")


# ---------- Network ----------


def test_network_allowed():
    m = _manifest(network=["api.github.com"])
    ok, _reason = check_network(m, "api.github.com")
    assert ok is True


def test_network_denied():
    m = _manifest(network=["api.github.com"])
    ok, _reason = check_network(m, "evil.example.com")
    assert ok is False


def test_network_empty():
    m = _manifest()
    ok, _reason = check_network(m, "anything.com")
    assert ok is False


# ---------- Write ----------


def test_write_allowed():
    m = _manifest(fs_write=[".ananke/evidence/**"])
    ok, _reason = check_write(m, ".ananke/evidence/run-1/manifest.json")
    assert ok is True


def test_write_blocked_secret_path():
    m = _manifest(fs_write=[".ananke/secrets/**"])
    ok, reason = check_write(m, ".ananke/secrets/adapters.env")
    assert ok is False
    assert "blocked" in reason


def test_write_blocked_config():
    m = _manifest(fs_write=[".ananke/**"])
    ok, _reason = check_write(m, ".ananke/config.toml")
    assert ok is False


def test_assert_write_raises():
    m = _manifest()
    with pytest.raises(SandboxViolation):
        assert_write(m, "src/main.py")


# ---------- Read ----------


def test_read_allowed():
    m = _manifest(fs_read=["src/**", "tests/**"])
    ok, _reason = check_read(m, "src/main.py")
    assert ok is True


def test_read_secret_blocked():
    m = _manifest(fs_read=["**"])
    ok, _reason = check_read(m, ".ananke/secrets/token")
    assert ok is False


def test_read_config_local_blocked():
    m = _manifest(fs_read=["**"])
    ok, _reason = check_read(m, ".ananke/config.local.toml")
    assert ok is False


# ---------- Legacy compat ----------


def test_legacy_permission_allows_shell():
    m = _manifest(shell=["ananke *"])
    assert permission_allows_shell(m, "ananke verify") is True
    assert permission_allows_shell(m, "curl x") is False


def test_legacy_permission_allows_network():
    m = _manifest(network=["api.jira.com"])
    assert permission_allows_network(m, "api.jira.com") is True


def test_legacy_permission_allows_write():
    m = _manifest(fs_write=[".ananke/evidence/**"])
    assert permission_allows_write(m, ".ananke/evidence/x.json") is True

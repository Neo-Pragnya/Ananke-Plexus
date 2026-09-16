"""Tests for APM resolver (I3)."""

from ananke.plexus.apm.resolver import resolve_skill_reference


def test_resolve_not_found(tmp_path):
    result = resolve_skill_reference(tmp_path, "no-such-skill")
    assert result.ok is False
    assert result.kind == "unknown"


def test_resolve_installed(tmp_path):
    skill_dir = tmp_path / ".ananke" / "skills" / "installed" / "my-skill"
    skill_dir.mkdir(parents=True)
    result = resolve_skill_reference(tmp_path, "my-skill")
    assert result.ok is True
    assert result.kind == "installed"


def test_resolve_by_path(tmp_path):
    skill_dir = tmp_path / "local-skill"
    skill_dir.mkdir()
    result = resolve_skill_reference(tmp_path, str(skill_dir))
    assert result.ok is True
    assert result.kind == "path"

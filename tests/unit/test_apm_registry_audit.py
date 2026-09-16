"""Tests for APM registry and audit modules."""

from pathlib import Path

from ananke.plexus.apm.audit import audit_manifest
from ananke.plexus.apm.registry import installed_skills_dir, list_installed_skills


def _make_skill(tmp: Path, name: str = "test-skill") -> Path:
    skill_dir = tmp / ".ananke" / "skills" / "installed" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "ananke-skill.toml").write_text(
        f"[skill]\nname = '{name}'\nversion = '1.0.0'\ndescription = 'test'\n\n"
        "[compatibility]\nananke = '>=0.1'\nskill_api = '1'\n",
        encoding="utf-8",
    )
    return skill_dir


def test_list_installed_empty(tmp_path):
    result = list_installed_skills(tmp_path)
    assert result == []


def test_list_installed_with_skill(tmp_path):
    _make_skill(tmp_path, "my-skill")
    result = list_installed_skills(tmp_path)
    assert "my-skill" in result


def test_installed_skills_dir(tmp_path):
    d = installed_skills_dir(tmp_path)
    assert str(d).endswith("installed")


def test_audit_manifest_narrow(tmp_path):
    skill_dir = _make_skill(tmp_path, "audit-target")
    findings = audit_manifest(skill_dir / "ananke-skill.toml")
    assert any("PASS" in f for f in findings)


def test_audit_manifest_network_warning(tmp_path):
    skill_dir = tmp_path / ".ananke" / "skills" / "installed" / "net-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "ananke-skill.toml").write_text(
        "[skill]\nname = 'net-skill'\nversion = '1.0.0'\ndescription = 'test'\n\n"
        "[permissions]\nnetwork = ['api.example.com']\n",
        encoding="utf-8",
    )
    findings = audit_manifest(skill_dir / "ananke-skill.toml")
    assert any("network" in f.lower() for f in findings)

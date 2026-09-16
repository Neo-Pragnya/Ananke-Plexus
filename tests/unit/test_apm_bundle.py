import json
from pathlib import Path

from ananke.plexus.apm.bundle import export_bundle, install_bundle, list_bundle_members
from ananke.plexus.apm.installer import install_local_skill
from ananke.plexus.apm.lockfile import read_lock


def test_apm_bundle_export_list_install(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    skill_source = Path(__file__).parents[1] / "fixtures" / "sample-skill"

    installed_name, _ = install_local_skill(project, skill_source)
    bundle_path = export_bundle(project, [installed_name])

    assert bundle_path.exists()
    members = list_bundle_members(bundle_path)
    assert members == [installed_name]

    second_project = tmp_path / "project-2"
    second_project.mkdir(parents=True, exist_ok=True)
    installed = install_bundle(second_project, bundle_path)
    assert installed == [installed_name]

    lock = read_lock(second_project)
    packages = lock.get("packages", [])
    assert isinstance(packages, list)
    assert packages
    first = packages[0]
    assert isinstance(first, dict)
    assert first.get("bundle_members") == [installed_name]
    assert first.get("permission_hash")


def test_apm_bundle_cli_json_manifest_contents(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    skill_source = Path(__file__).parents[1] / "fixtures" / "sample-skill"

    installed_name, _ = install_local_skill(project, skill_source)
    bundle_path = export_bundle(project, [installed_name])
    members = list_bundle_members(bundle_path)
    assert json.loads(json.dumps({"members": members}))["members"] == [installed_name]

"""A skill that came from the registry can be exported as a bundle and installed elsewhere, and a bundle can't lie its way out."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from ananke.plexus.apm.bundle import export_bundle, install_bundle
from ananke.plexus.apm.lockfile import read_lock
from ananke.plexus.core.paths import project_ananke_dir


def installed(project: Path, name: str = "local.good@1.0.0") -> Path:
    folder = project_ananke_dir(project) / "skills" / "installed" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: good\n---\n")
    (folder / "ananke.registry.json").write_text(
        json.dumps(
            {
                "kind": "skill",
                "namespace": "local",
                "name": "good",
                "version": "1.0.0",
                "license": {"spdx": "MIT"},
            }
        )
    )
    return folder


def test_a_registry_skill_round_trips_through_a_bundle(tmp_path: Path) -> None:
    source, other = tmp_path / "a", tmp_path / "b"
    other.mkdir()
    installed(source)
    bundle = export_bundle(source, ["local.good@1.0.0"], tmp_path / "team.tar.gz")
    assert install_bundle(other, bundle) == ["local.good@1.0.0"]
    assert (
        project_ananke_dir(other) / "skills" / "installed" / "local.good@1.0.0" / "SKILL.md"
    ).is_file()
    entry = read_lock(other)["packages"][0]
    assert entry["name"] == "local.good" and entry["license"] == "MIT"


@pytest.mark.parametrize("field", ["namespace", "name", "version"])
@pytest.mark.parametrize("value", ["../../evil", "..", "a/b", "", 7])
def test_a_bundle_cant_name_its_way_out_of_the_install_folder(
    tmp_path: Path, field: str, value: object
) -> None:
    other = tmp_path / "b"
    other.mkdir()
    manifest = {
        "kind": "skill",
        "namespace": "local",
        "name": "good",
        "version": "1.0.0",
        field: value,
    }
    staging = tmp_path / "skills" / "m"
    staging.mkdir(parents=True)
    (staging / "ananke.registry.json").write_text(json.dumps(manifest))
    (tmp_path / "ananke-bundle.json").write_text(json.dumps({"members": ["m"]}))
    bundle = tmp_path / "evil.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(tmp_path / "ananke-bundle.json", arcname="ananke-bundle.json")
        archive.add(tmp_path / "skills", arcname="skills")
    with pytest.raises(ValueError, match="plain names"):
        install_bundle(other, bundle)
    assert not list(tmp_path.glob("**/evil*/SKILL.md"))

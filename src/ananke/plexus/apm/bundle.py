"""APM bundle export and install helpers."""

from __future__ import annotations

import json
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.apm.installer import MANIFEST_NAME, install_local_skill
from ananke.plexus.apm.lockfile import read_lock, write_lock
from ananke.plexus.apm.registry import installed_skills_dir

BUNDLE_MANIFEST = "ananke-bundle.json"


def export_bundle(
    repository_root: Path,
    installed_names: list[str],
    output_path: Path | None = None,
) -> Path:
    installed_dir = installed_skills_dir(repository_root)
    members = sorted(dict.fromkeys(installed_names))
    if not members:
        raise ValueError("At least one installed skill name is required.")

    for name in members:
        if not (installed_dir / name).exists():
            raise FileNotFoundError(f"Installed skill not found: {name}")

    bundle_dir = repository_root / ".ananke" / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    resolved_output = (
        output_path or bundle_dir / f"bundle-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "members": members,
    }
    with tarfile.open(resolved_output, "w:gz") as archive:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / BUNDLE_MANIFEST
            manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            archive.add(manifest_path, arcname=BUNDLE_MANIFEST)
        for name in members:
            archive.add(installed_dir / name, arcname=f"skills/{name}")
    return resolved_output


def list_bundle_members(bundle_path: Path) -> list[str]:
    with tarfile.open(bundle_path, "r:gz") as archive:
        manifest = archive.extractfile(BUNDLE_MANIFEST)
        if manifest is None:
            return []
        payload = json.loads(manifest.read().decode("utf-8"))
    members = payload.get("members", []) if isinstance(payload, dict) else []
    return [str(item) for item in members] if isinstance(members, list) else []


def install_bundle(repository_root: Path, bundle_path: Path) -> list[str]:
    members = list_bundle_members(bundle_path)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with tarfile.open(bundle_path, "r:gz") as archive:
            archive.extractall(temp_root)

        installed: list[str] = []
        for name in members:
            skill_dir = temp_root / "skills" / name
            if not (skill_dir / MANIFEST_NAME).exists():
                raise FileNotFoundError(f"Bundle member missing manifest: {name}")
            installed_name, _ = install_local_skill(repository_root, skill_dir)
            installed.append(installed_name)

    lock = read_lock(repository_root)
    packages = lock.get("packages", [])
    if isinstance(packages, list):
        for item in packages:
            if not isinstance(item, dict):
                continue
            if f"{item.get('name', '')}@{item.get('version', '')}" not in installed:
                continue
            item["bundle_members"] = installed
        write_lock(repository_root, {"packages": packages})
    return installed

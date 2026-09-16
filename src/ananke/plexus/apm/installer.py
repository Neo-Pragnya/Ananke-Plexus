"""APM installer for local skill packages."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.apm.lockfile import read_lock, write_lock
from ananke.plexus.apm.manifest import load_manifest
from ananke.plexus.apm.provenance import hash_file
from ananke.plexus.apm.registry import installed_skills_dir

MANIFEST_NAME = "ananke-skill.toml"


def _permission_hash(manifest_permissions: dict[str, object]) -> str:
    payload = json.dumps(manifest_permissions, sort_keys=True)
    Path("permissions.json")
    return hash_file_content(payload)


def hash_file_content(value: str) -> str:
    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()


def install_local_skill(repository_root: Path, source_dir: Path) -> tuple[str, Path]:
    manifest_path = source_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {MANIFEST_NAME} in {source_dir}")

    manifest = load_manifest(manifest_path)
    target_name = f"{manifest.skill.name}@{manifest.skill.version}"
    target_dir = installed_skills_dir(repository_root) / target_name

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)

    lock = read_lock(repository_root)
    packages = lock.get("packages", [])
    if not isinstance(packages, list):
        packages = []

    packages = [
        item
        for item in packages
        if isinstance(item, dict) and item.get("name") != manifest.skill.name
    ]
    packages.append(
        {
            "name": manifest.skill.name,
            "version": manifest.skill.version,
            "source": str(source_dir),
            "digest": hash_file(manifest_path),
            "source_revision": manifest.provenance.revision,
            "license": manifest.skill.license,
            "installed_at": datetime.now(UTC).isoformat(),
            "permissions": manifest.permissions.model_dump(),
            "permission_hash": _permission_hash(manifest.permissions.model_dump()),
            "bundle_members": [],
        }
    )
    write_lock(repository_root, {"packages": packages})

    return target_name, target_dir

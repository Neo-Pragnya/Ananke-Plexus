"""APM installer for local skill packages."""

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.apm.lockfile import read_lock, write_lock
from ananke.plexus.apm.manifest import load_manifest
from ananke.plexus.apm.provenance import hash_file
from ananke.plexus.apm.registry import installed_skills_dir

MANIFEST_NAME = "ananke-skill.toml"
REGISTRY_MANIFEST_NAME = "ananke.registry.json"
_SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")


def _permission_hash(manifest_permissions: dict[str, object]) -> str:
    payload = json.dumps(manifest_permissions, sort_keys=True)
    Path("permissions.json")
    return hash_file_content(payload)


def hash_file_content(value: str) -> str:
    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()


def _record(
    repository_root: Path,
    *,
    name: str,
    version: str,
    source: str,
    digest: str,
    revision: str,
    license_name: str,
    permissions: dict[str, object],
) -> None:
    lock = read_lock(repository_root)
    packages = lock.get("packages", [])
    if not isinstance(packages, list):
        packages = []

    packages = [item for item in packages if isinstance(item, dict) and item.get("name") != name]
    packages.append(
        {
            "name": name,
            "version": version,
            "source": source,
            "digest": digest,
            "source_revision": revision,
            "license": license_name,
            "installed_at": datetime.now(UTC).isoformat(),
            "permissions": permissions,
            "permission_hash": _permission_hash(permissions),
            "bundle_members": [],
        }
    )
    write_lock(repository_root, {"packages": packages})


def install_registry_copy(repository_root: Path, source_dir: Path) -> tuple[str, Path]:
    """Install a skill that came from the registry, as a bundle carries it. A bundle may come from anyone, so the
    target's name is built from validated manifest fields, never from the folder's name."""
    manifest_path = source_dir / REGISTRY_MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    parts = [
        data.get(k) if isinstance(data, dict) else None for k in ("namespace", "name", "version")
    ]
    if not all(isinstance(p, str) and _SAFE_PART.fullmatch(p) and ".." not in p for p in parts):
        raise ValueError(f"{manifest_path.name}: namespace, name and version must be plain names")
    namespace, name, version = parts
    target_name = f"{namespace}.{name}@{version}"
    target_dir = installed_skills_dir(repository_root) / target_name

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)

    license_info = data.get("license")
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    _record(
        repository_root,
        name=f"{namespace}.{name}",
        version=str(version),
        source=str(source_dir),
        digest=hash_file(manifest_path),
        revision="",
        license_name=str(license_info.get("spdx", "")) if isinstance(license_info, dict) else "",
        permissions=permissions,
    )
    return target_name, target_dir


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

    _record(
        repository_root,
        name=manifest.skill.name,
        version=manifest.skill.version,
        source=str(source_dir),
        digest=hash_file(manifest_path),
        revision=manifest.provenance.revision,
        license_name=manifest.skill.license,
        permissions=manifest.permissions.model_dump(),
    )

    return target_name, target_dir

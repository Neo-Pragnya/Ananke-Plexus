"""Materialization and activation (spec §85-§88).

``registered != materialized != activated``: discovery never makes a capability
executable. Materialization extracts a verified payload into
``<registry>/materialized/<sha256>/``; activation exposes it to a project (copy or
symlink, per policy) using the same layout APM already understands.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ananke.plexus.registry.errors import QuarantinedError
from ananke.plexus.registry.models import (
    ArtifactKind,
    LifecycleStatus,
    VersionRecord,
    parse_ref,
)
from ananke.plexus.registry.payload import extract_to
from ananke.plexus.registry.tomlw import dumps

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

MARKER = ".ananke-materialized"
PROFILE_NAME = "activation.toml"


class ActivationResult(BaseModel):
    uri: str
    version: str
    digest: str
    path: str
    state: str = "activated"
    mode: str = "copy"


def install_dir_name(rec: VersionRecord) -> str:
    return f"{rec.namespace}.{rec.name}@{rec.version}"


def materialize(registry: Registry, rec: VersionRecord, *, allow_yanked: bool = True) -> Path:
    """Extract ``rec`` into the content-addressed materialization directory (idempotent)."""
    if rec.lifecycle is LifecycleStatus.QUARANTINED:
        raise QuarantinedError(f"{rec.version_uri} is quarantined; materialization refused")
    if rec.lifecycle is LifecycleStatus.YANKED and not allow_yanked:
        raise QuarantinedError(f"{rec.version_uri} is yanked; pass allow_yanked to materialize")
    target = registry.root / "materialized" / rec.digest_sha256
    marker = target / MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == rec.digest_sha256:
        return target
    payload = registry.payload(rec)  # verifies the blob hash
    tmp = target.with_name(f".{rec.digest_sha256}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    extract_to(payload, tmp)
    (tmp / MARKER).write_text(rec.digest_sha256 + "\n", encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(tmp, target)
    return target


def _kind_dirs(project: Path, rec: VersionRecord) -> tuple[Path, Path]:
    base = project / ".ananke"
    if rec.kind is ArtifactKind.SKILL:
        return base / "skills" / "installed", base / "skills" / "active"
    if rec.kind is ArtifactKind.AGENT:
        return base / "agents" / "installed", base / "agents" / "active"
    return (
        base / "registry" / "activated" / rec.kind.value / "installed",
        base / "registry" / "activated" / rec.kind.value / "active",
    )


def _project_key(project: Path) -> str:
    return str(project.resolve())


def _place(source: Path, dest: Path, mode: str) -> str:
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode == "link":
        try:
            dest.symlink_to(source, target_is_directory=True)
            return "link"
        except OSError:
            pass
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(MARKER))
    return "copy"


def _profile_path(project: Path) -> Path:
    return project / ".ananke" / PROFILE_NAME


def read_profile(project: Path) -> dict[str, dict[str, str]]:
    path = _profile_path(project)
    if not path.is_file():
        return {"skills": {}, "agent": {}, "other": {}}
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return {k: dict(raw.get(k, {})) for k in ("skills", "agent", "other")}


def _write_profile(project: Path, profile: dict[str, dict[str, str]]) -> None:
    data: dict[str, Any] = {"activation": {"project": project.resolve().name}}
    for key in ("skills", "agent", "other"):
        if profile.get(key):
            data[key] = dict(sorted(profile[key].items()))
    path = _profile_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data, header="# Ananke activation profile (spec §87)"), encoding="utf-8")


def _profile_section(rec: VersionRecord) -> tuple[str, str]:
    if rec.kind is ArtifactKind.SKILL:
        return "skills", f"{rec.namespace}/{rec.name}"
    if rec.kind is ArtifactKind.AGENT:
        return "agent", f"{rec.namespace}/{rec.name}"
    return "other", f"{rec.kind.value}:{rec.namespace}/{rec.name}"


def _update_legacy_lock(
    project: Path,
    rec: VersionRecord,
    requirement: str | None,
    *,
    remove: bool = False,
    root: bool = True,
) -> None:
    """Keep ``.ananke/apm.lock`` (used by ``apm info/verify``) in step with registry installs."""
    from ananke.plexus.apm.lockfile import read_lock, write_lock

    lock = read_lock(project)
    packages = lock.get("packages", [])
    packages = [p for p in packages if isinstance(p, dict)] if isinstance(packages, list) else []
    name = f"{rec.namespace}.{rec.name}"
    kept = [p for p in packages if p.get("registry_uri") != rec.uri]
    prior = next((p for p in packages if p.get("registry_uri") == rec.uri), {})
    if not remove:
        # a dependency install must never become a root requirement (it would freeze upgrades)
        origin = prior.get("origin") or ("registry" if root else "registry-dependency")
        if root and origin == "registry-dependency":
            origin = "registry"
        kept.append(
            {
                "name": name,
                "version": rec.version,
                "source": "registry",
                "digest": rec.digest_sha256,
                "license": rec.license.expression or "",
                "registry_uri": rec.uri,
                "origin": origin,
                "requirement": (requirement or prior.get("requirement") or f"={rec.version}")
                if origin == "registry"
                else "",
                "permissions": rec.manifest.permissions.model_dump(),
                "bundle_members": [],
            }
        )
    write_lock(project, {"packages": kept})


def activate(
    registry: Registry,
    project_root: Path,
    ref: str,
    *,
    mode: str | None = None,
    requirement: str | None = None,
    allow_yanked: bool = False,
    kind: ArtifactKind | str | None = None,
    root: bool = True,
) -> ActivationResult:
    """Materialize and activate an exact version for a project."""
    project = project_root.resolve()
    rec = registry.exact_version(ref, kind)
    source = materialize(registry, rec, allow_yanked=allow_yanked)
    installed, active = _kind_dirs(project, rec)
    dest = installed / install_dir_name(rec)
    used = _place(source, dest, mode or registry.policy.activation_mode)
    active.mkdir(parents=True, exist_ok=True)
    # only one active version of an artifact per project
    for marker in active.glob(f"{rec.namespace}.{rec.name}@*.active"):
        marker.unlink()
    (active / f"{install_dir_name(rec)}.active").write_text(
        install_dir_name(rec) + "\n", encoding="utf-8"
    )
    registry.store.upsert_activation(
        _project_key(project), rec, "activated", str(dest), registry.now()
    )
    profile = read_profile(project)
    section, key = _profile_section(rec)
    profile[section][key] = rec.version
    _write_profile(project, profile)
    _update_legacy_lock(project, rec, requirement, root=root)
    registry.emit(
        "activation.changed",
        rec.uri,
        {"version": rec.version, "project": project.name, "state": "activated"},
    )
    registry.usage("activate", rec, project.name)
    return ActivationResult(
        uri=rec.uri, version=rec.version, digest=rec.digest, path=str(dest), mode=used
    )


def install(
    registry: Registry,
    project_root: Path,
    ref: str,
    *,
    mode: str | None = None,
    requirement: str | None = None,
    activate_after: bool = False,
    allow_yanked: bool = False,
    root: bool = True,
) -> ActivationResult:
    """APM-style install: materialize into ``installed/`` without activating."""
    project = project_root.resolve()
    rec = registry.exact_version(ref)
    source = materialize(registry, rec, allow_yanked=allow_yanked)
    installed, _active = _kind_dirs(project, rec)
    dest = installed / install_dir_name(rec)
    used = _place(source, dest, mode or registry.policy.activation_mode)
    registry.store.upsert_activation(
        _project_key(project), rec, "materialized", str(dest), registry.now()
    )
    _update_legacy_lock(project, rec, requirement, root=root)
    if activate_after:
        return activate(
            registry,
            project,
            ref,
            mode=mode,
            requirement=requirement,
            allow_yanked=allow_yanked,
            root=root,
        )
    return ActivationResult(
        uri=rec.uri,
        version=rec.version,
        digest=rec.digest,
        path=str(dest),
        state="materialized",
        mode=used,
    )


def deactivate(
    registry: Registry, project_root: Path, ref: str, *, kind: ArtifactKind | str | None = None
) -> bool:
    project = project_root.resolve()
    r = parse_ref(ref, kind)
    k, ns, name = registry.locate(r, kind)
    rows = [
        a
        for a in registry.store.list_activations(_project_key(project))
        if (a["kind"], a["namespace"], a["name"]) == (k.value, ns, name)
    ]
    if not rows:
        return False
    row = rows[0]
    rec = registry.exact_version(f"ananke://{k.value}/{ns}/{name}@{row['version']}")
    _installed, active = _kind_dirs(project, rec)
    with contextlib.suppress(OSError):
        (active / f"{install_dir_name(rec)}.active").unlink()
    registry.store.upsert_activation(
        _project_key(project), rec, "materialized", row["path"], registry.now()
    )
    profile = read_profile(project)
    section, key = _profile_section(rec)
    profile[section].pop(key, None)
    _write_profile(project, profile)
    registry.emit(
        "activation.changed",
        rec.uri,
        {"version": rec.version, "project": project.name, "state": "deactivated"},
    )
    return True


def uninstall(
    registry: Registry, project_root: Path, ref: str, *, kind: ArtifactKind | str | None = None
) -> bool:
    project = project_root.resolve()
    deactivate(registry, project, ref, kind=kind)
    k, ns, name = registry.locate(ref, kind)
    rows = [
        a
        for a in registry.store.list_activations(_project_key(project))
        if (a["kind"], a["namespace"], a["name"]) == (k.value, ns, name)
    ]
    if not rows:
        return False
    rec = registry.exact_version(f"ananke://{k.value}/{ns}/{name}@{rows[0]['version']}")
    dest = Path(rows[0]["path"]) if rows[0]["path"] else None
    if dest is not None:
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
    registry.store.delete_activation(_project_key(project), k, ns, name)
    _update_legacy_lock(project, rec, None, remove=True)
    return True


def apply_profile(registry: Registry, project_root: Path) -> list[ActivationResult]:
    """Activate everything listed in ``.ananke/activation.toml`` at exact versions."""
    profile = read_profile(project_root)
    results: list[ActivationResult] = []
    for section, kind in (("skills", ArtifactKind.SKILL), ("agent", ArtifactKind.AGENT)):
        for key, version in sorted(profile.get(section, {}).items()):
            results.append(activate(registry, project_root, f"{key}@{version}", kind=kind))
    for key, version in sorted(profile.get("other", {}).items()):
        kind_name, _, rest = key.partition(":")
        results.append(
            activate(registry, project_root, f"{rest}@{version}", kind=ArtifactKind(kind_name))
        )
    return results


def list_installed(registry: Registry, project_root: Path) -> list[dict[str, Any]]:
    return registry.store.list_activations(_project_key(project_root.resolve()))

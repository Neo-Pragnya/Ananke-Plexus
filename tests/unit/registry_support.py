"""Shared helpers for registry tests (no filesystem needed for most cases)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ananke.plexus.registry.importers.base import ImportedArtifact
from ananke.plexus.registry.models import ArtifactManifest, Provenance, TrustStatus
from ananke.plexus.registry.policy import RegistryPolicy
from ananke.plexus.registry.registry import Registry


def open_registry(tmp_path: Path, policy: RegistryPolicy | None = None, **kw: Any) -> Registry:
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"2026-01-01T00:00:{counter['n'] % 60:02d}+00:00"

    reg = Registry.open(
        tmp_path / "registry", create=True, policy=policy, actor="tester", clock=clock, **kw
    )
    reg.init()
    return reg


def artifact(
    name: str = "graph-review",
    version: str = "1.0.0",
    *,
    kind: str = "skill",
    namespace: str = "core",
    files: dict[str, bytes] | None = None,
    **fields: Any,
) -> ImportedArtifact:
    data: dict[str, Any] = {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "version": version,
        "summary": f"{name} summary",
        "license": {"expression": "Apache-2.0"},
        "capabilities": ["graph.query"],
    }
    data.update(fields)
    manifest = ArtifactManifest.model_validate(data)
    return ImportedArtifact(
        manifest=manifest,
        files=files if files is not None else {"README.md": f"# {name} {version}\n".encode()},
        provenance=Provenance(source_type="test", source_name=name),
    )


def add(
    reg: Registry,
    name: str = "graph-review",
    version: str = "1.0.0",
    *,
    trust: TrustStatus | None = None,
    channel: str | None = None,
    approve: bool = False,
    **kw: Any,
) -> str:
    """Register a version; optionally promote it to approved/stable. Returns ``ns/name@version``."""
    res = reg.register(artifact(name, version, **kw), channel=channel, trust=trust)
    ref = f"{kw.get('namespace', 'core')}/{name}@{res.version}"
    if approve:
        reg.promote(ref, trust=TrustStatus.APPROVED, channel="stable", run_gate=False)
    return ref

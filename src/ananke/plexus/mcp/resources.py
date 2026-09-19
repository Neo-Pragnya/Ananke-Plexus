"""MCP resource handlers (H2) — static and dynamic resource registry."""

from __future__ import annotations

import json
from pathlib import Path

_STATIC_RESOURCES = [
    "ananke://project/status",
    "ananke://architecture/system",
    "ananke://spec/index",
    "ananke://graph/snapshot",
    "ananke://policy/index",
    "ananke://evidence/index",
    "ananke://run/index",
]


def list_resources() -> list[str]:
    return [*_STATIC_RESOURCES, "ananke://registry/skills", "ananke://registry/agents"]


def list_dynamic_resources(repository_root: Path) -> list[str]:
    """Discover per-spec, per-run, and per-policy resources from the workspace."""
    resources = list_resources()

    from ananke.plexus.registry.mcp_interface import list_registry_resources

    resources += [u for u in list_registry_resources(repository_root) if u not in resources]

    specs_root = repository_root / ".ananke" / "specs"
    if specs_root.exists():
        for item in sorted(specs_root.iterdir()):
            if item.is_dir():
                resources.append(f"ananke://spec/{item.name}")

    evidence_root = repository_root / ".ananke" / "evidence"
    if evidence_root.exists():
        for item in sorted(evidence_root.iterdir()):
            if item.is_dir():
                resources.append(f"ananke://run/{item.name}/evidence")

    policy_dir = repository_root / ".ananke" / "policy"
    if policy_dir.exists():
        for item in sorted(policy_dir.glob("*.toml")):
            resources.append(f"ananke://policy/{item.stem}")

    return resources


def read_resource(repository_root: Path, resource_uri: str) -> str:
    if resource_uri.startswith("ananke://registry/"):
        from ananke.plexus.registry.mcp_interface import read_registry_resource

        return read_registry_resource(repository_root, resource_uri)

    if resource_uri == "ananke://project/status":
        config = repository_root / ".ananke" / "config.toml"
        return config.read_text(encoding="utf-8") if config.exists() else "project not initialized"

    if resource_uri == "ananke://architecture/system":
        calm = repository_root / ".ananke" / "architecture" / "system.calm.json"
        return calm.read_text(encoding="utf-8") if calm.exists() else "architecture not initialized"

    if resource_uri == "ananke://spec/index":
        specs_root = repository_root / ".ananke" / "specs"
        if not specs_root.exists():
            return "no specs"
        items = sorted(item.name for item in specs_root.iterdir() if item.is_dir())
        return json.dumps({"specs": items})

    if resource_uri == "ananke://graph/snapshot":
        graph_meta = repository_root / ".ananke" / "graph" / "metadata.json"
        return graph_meta.read_text(encoding="utf-8") if graph_meta.exists() else "graph not built"

    if resource_uri == "ananke://policy/index":
        policy_dir = repository_root / ".ananke" / "policy"
        if not policy_dir.exists():
            return "no policies"
        packs = sorted(p.stem for p in policy_dir.glob("*.toml"))
        return json.dumps({"packs": packs})

    if resource_uri == "ananke://evidence/index":
        evidence_dir = repository_root / ".ananke" / "evidence"
        if not evidence_dir.exists():
            return "no evidence"
        runs = sorted(item.name for item in evidence_dir.iterdir() if item.is_dir())
        return json.dumps({"runs": runs})

    if resource_uri == "ananke://run/index":
        runs_dir = repository_root / ".ananke" / "runs"
        if not runs_dir.exists():
            return "no runs"
        runs = sorted(item.name for item in runs_dir.iterdir() if item.is_dir())
        return json.dumps({"runs": runs})

    if resource_uri.startswith("ananke://spec/") and resource_uri != "ananke://spec/index":
        spec_name = resource_uri.removeprefix("ananke://spec/")
        spec_dir = repository_root / ".ananke" / "specs" / spec_name
        if not spec_dir.exists():
            return f"spec not found: {spec_name}"
        files = {
            f.name: f.read_text(encoding="utf-8") for f in sorted(spec_dir.iterdir()) if f.is_file()
        }
        return json.dumps(files)

    if resource_uri.startswith("ananke://run/") and resource_uri.endswith("/evidence"):
        run_id = resource_uri.removeprefix("ananke://run/").removesuffix("/evidence")
        manifest = repository_root / ".ananke" / "evidence" / run_id / "manifest.json"
        return (
            manifest.read_text(encoding="utf-8")
            if manifest.exists()
            else f"evidence not found: {run_id}"
        )

    if resource_uri.startswith("ananke://policy/") and resource_uri != "ananke://policy/index":
        pack_name = resource_uri.removeprefix("ananke://policy/")
        pack_path = repository_root / ".ananke" / "policy" / f"{pack_name}.toml"
        return (
            pack_path.read_text(encoding="utf-8")
            if pack_path.exists()
            else f"policy not found: {pack_name}"
        )

    return "resource not found"

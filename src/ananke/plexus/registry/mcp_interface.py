"""Registry exposed through MCP tools and resources (spec §84).

Read-only. Agents discover capabilities through the same source of truth as the CLI.
Tool names follow the specification verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ananke.plexus.registry.diff import render_diff_text
from ananke.plexus.registry.errors import NotInitializedError, RegistryError
from ananke.plexus.registry.models import ArtifactKind
from ananke.plexus.registry.present import record_to_dict
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    Resolver,
)
from ananke.plexus.registry.search import search

REGISTRY_TOOLS = frozenset(
    {
        "registry_search",
        "registry_get_skill",
        "registry_get_agent",
        "registry_resolve",
        "registry_compare_versions",
        "registry_list_capabilities",
    }
)
REGISTRY_RESOURCES = ("ananke://registry/skills", "ananke://registry/agents")


def _open(project_root: Path) -> Registry:
    try:
        return Registry.for_project(project_root)
    except NotInitializedError:
        raise


def _fail(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", "ERROR")
    return {"ok": False, "summary": str(exc), "details": {"code": code}}


def call_registry_tool(project_root: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        with _open(project_root) as reg:
            if name == "registry_search":
                hits = search(
                    reg,
                    str(args.get("query", "")),
                    kind=args.get("kind"),
                    capability=args.get("capability"),
                    runtime=args.get("runtime"),
                    trust=args.get("trust"),
                    channel=args.get("channel"),
                    limit=min(int(args.get("limit", 20)), 100),
                    semantic=bool(args.get("semantic", False)),
                )
                return {
                    "ok": True,
                    "summary": f"{len(hits)} result(s)",
                    "details": {"results": [h.model_dump(mode="json") for h in hits]},
                }
            if name in {"registry_get_skill", "registry_get_agent"}:
                kind = ArtifactKind.SKILL if name.endswith("skill") else ArtifactKind.AGENT
                ref = str(args.get("ref", ""))
                if not ref:
                    return {"ok": False, "summary": "ref required", "details": {}}
                rec = reg.kind_view(kind).get(ref, args.get("version"))
                return {"ok": True, "summary": rec.version_uri, "details": record_to_dict(rec, reg)}
            if name == "registry_resolve":
                ref = str(args.get("ref", ""))
                if not ref:
                    return {"ok": False, "summary": "ref required", "details": {}}
                env = ResolutionEnvironment.detect(runtime=args.get("runtime"))
                opts = (
                    ResolutionOptions(mode=args.get("mode"))
                    if args.get("mode")
                    else ResolutionOptions()
                )
                result = Resolver(reg, env=env, options=opts).resolve(
                    Requirement.parse(ref, args.get("kind"), args.get("version"))
                )
                return {
                    "ok": result.ok,
                    "summary": result.selected.ref if result.selected else "unresolved",
                    "details": {
                        "explanation": result.explain(),
                        "selected": result.selected.model_dump() if result.selected else None,
                        "nodes": [n.ref.model_dump() for n in result.nodes],
                    },
                }
            if name == "registry_compare_versions":
                a, b = str(args.get("a", "")), str(args.get("b", ""))
                if not a or not b:
                    return {"ok": False, "summary": "a and b required", "details": {}}
                diff = reg.diff(a, b, with_text=False)
                return {
                    "ok": True,
                    "summary": f"suggested bump: {diff.suggested_bump}",
                    "details": {
                        "diff": diff.model_dump(mode="json"),
                        "text": render_diff_text(diff),
                    },
                }
            if name == "registry_list_capabilities":
                index = reg.store.capability_index()
                caps: dict[str, list[str]] = {}
                for cap, vids in sorted(index.items()):
                    caps[cap] = sorted({reg.store.record_by_id(v).version_uri for v in vids})
                return {
                    "ok": True,
                    "summary": f"{len(caps)} capabilities",
                    "details": {"capabilities": caps},
                }
    except (RegistryError, ValueError, KeyError) as exc:
        return _fail(exc)
    return {"ok": False, "summary": "unsupported tool", "details": {"tool": name}}


def list_registry_resources(project_root: Path) -> list[str]:
    out = list(REGISTRY_RESOURCES)
    try:
        with _open(project_root) as reg:
            for rec in reg.store.list_records():
                if rec.kind in {ArtifactKind.SKILL, ArtifactKind.AGENT}:
                    out.append(
                        f"ananke://registry/{rec.kind.value}/{rec.namespace}/{rec.name}/{rec.version}"
                    )
    except RegistryError:
        pass
    return out


def read_registry_resource(project_root: Path, uri: str) -> str:
    try:
        with _open(project_root) as reg:
            if uri in {"ananke://registry/skills", "ananke://registry/agents"}:
                kind = ArtifactKind.SKILL if uri.endswith("skills") else ArtifactKind.AGENT
                return json.dumps(
                    {
                        "items": [
                            record_to_dict(r, None, detail=False) for r in reg.list_versions(kind)
                        ]
                    }
                )
            parts = uri.removeprefix("ananke://registry/").split("/")
            if len(parts) == 4 and parts[0] in {"skill", "agent"}:
                rec = reg.exact_version(f"ananke://{parts[0]}/{parts[1]}/{parts[2]}@{parts[3]}")
                return json.dumps(record_to_dict(rec, reg))
    except RegistryError as exc:
        return f"registry error: {exc}"
    return "resource not found"

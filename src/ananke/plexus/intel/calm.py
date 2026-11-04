"""An inferred CALM model of the repository: its components, and the links with evidence between them.

It's built from the overview, compose files and imports that cross component folders. It's never written anywhere. A declared
model at ``.ananke/architecture/system.calm.json`` is read, without creating one, and what differs is reported as ``drift``.
"""

from __future__ import annotations

import json
from typing import Any

from ananke.plexus.intel import overview
from ananke.plexus.intel._common import deepest_folder, read_text
from ananke.plexus.intel.context import RepoContext

DECLARED_PATH = ".ananke/architecture/system.calm.json"
_NOT_RUNTIME = {"tests"}


def _module_files(files: list[str]) -> dict[str, str]:
    """Dotted module names, by each file's every suffix, for when an edge has no resolved target."""
    modules: dict[str, str | None] = {}
    for path in files:
        if not path.endswith(".py"):
            continue
        stem = path[:-3].removesuffix("/__init__")
        parts = stem.split("/")
        for start in range(len(parts)):
            dotted = ".".join(parts[start:])
            modules[dotted] = None if dotted in modules and modules[dotted] != path else path
    return {name: file for name, file in modules.items() if file}


def _import_links(ctx: RepoContext, components: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Pairs of components joined by an import, with one import as evidence."""
    links: dict[tuple[str, str], str] = {}
    modules = _module_files(ctx.files)
    by_path = {c["path"]: c for c in components if c["path"]}
    for edge in sorted(ctx.graph.edges, key=lambda e: (e.source, e.target, e.source_location)):
        if edge.kind != "imports" or not edge.source.startswith("file:"):
            continue
        resolved = str(getattr(edge, "resolved_target", "") or "")
        if resolved.startswith("file:"):
            target_file: str | None = resolved.removeprefix("file:")
        elif edge.target.startswith("module:"):
            # Only a whole name inside a repository: ``yaml`` mustn't match ``docs/yaml.py``.
            target_file = (
                modules.get(edge.target.removeprefix("module:")) if "." in edge.target else None
            )
        else:
            target_file = None
        if not target_file:
            continue
        source_folder = deepest_folder(edge.source.removeprefix("file:"), by_path)
        target_folder = deepest_folder(target_file, by_path)
        if source_folder is None or target_folder is None or source_folder == target_folder:
            continue
        source, target = by_path[source_folder], by_path[target_folder]
        key = (source["name"], target["name"])
        links.setdefault(key, edge.source_location or edge.source.removeprefix("file:"))
    return links


def _relation_type(target_kind: str, source_kind: str) -> str:
    if target_kind == "database":
        return "data"
    if source_kind == "frontend":
        return "http"
    return "depends_on"


def _declared(ctx: RepoContext, limits: list[str]) -> dict[str, Any] | None:
    if not (ctx.root / DECLARED_PATH).is_file():
        return None
    text = read_text(ctx, DECLARED_PATH)
    if text is None:
        limits.append(f"{DECLARED_PATH} couldn't be read: too large, or outside the repository")
        return None
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError) as exc:
        limits.append(f"{DECLARED_PATH} couldn't be read: {type(exc).__name__}")
        return None
    if not isinstance(payload, dict):
        limits.append(f"{DECLARED_PATH} couldn't be read: not an object")
        return None
    return payload


def _drift(declared: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    def names(payload: dict[str, Any]) -> set[str]:
        items = payload.get("components")
        return {
            str(c.get("name", c.get("id", ""))).strip()
            for c in (items if isinstance(items, list) else [])
            if isinstance(c, dict)
        } - {""}

    def pairs(payload: dict[str, Any]) -> set[tuple[str, str]]:
        items = payload.get("relationships")
        return {
            (str(r.get("source", "")).strip(), str(r.get("target", "")).strip())
            for r in (items if isinstance(items, list) else [])
            if isinstance(r, dict)
        }

    declared_names, inferred_names = names(declared), names(model)
    declared_pairs, inferred_pairs = pairs(declared), pairs(model)
    return {
        "declared_path": DECLARED_PATH,
        "components_only_declared": sorted(declared_names - inferred_names),
        "components_only_inferred": sorted(inferred_names - declared_names),
        "relationships_only_declared": [list(p) for p in sorted(declared_pairs - inferred_pairs)],
        "relationships_only_inferred": [list(p) for p in sorted(inferred_pairs - declared_pairs)],
    }


def analyze(ctx: RepoContext) -> dict[str, Any]:
    summary = ctx.sections.get("overview")
    if summary is None or "components" not in summary:
        summary = overview.analyze(ctx)
    limits: list[str] = []
    every = summary.get("components", [])
    # Tests aren't architecture. Notebooks are only when they're all there is.
    running = [c for c in every if c["kind"] not in _NOT_RUNTIME and c["kind"] != "notebooks"]
    components = running or [c for c in every if c["kind"] == "notebooks"]
    if not components:
        declared = _declared(ctx, limits)
        if declared is None:
            limits.append("no components were found to model")
            return {"status": "none", "origin": "inferred", "limits": limits}

    by_name = {c["name"]: c for c in components}
    relationships: dict[tuple[str, str], dict[str, Any]] = {}

    services, _ = overview.compose_services(ctx)
    alias = {c["path"]: c["name"] for c in components if c["path"]}
    for service in services:
        source = service.name if service.name in by_name else alias.get(service.path or "")
        if source is None:
            continue
        for dependency, line in service.depends_on:
            target = dependency if dependency in by_name else None
            if target is None:
                twin = next((s for s in services if s.name == dependency and s.path), None)
                target = alias.get(twin.path or "") if twin else None
            if target is None or target == source:
                continue
            kind = _relation_type(by_name[target]["kind"], by_name[source]["kind"])
            relationships.setdefault(
                (source, target),
                {
                    "source": source,
                    "target": target,
                    "type": kind,
                    "kind": kind,
                    "evidence": f"{service.file}:{line}",
                },
            )

    for (source, target), evidence in _import_links(ctx, components).items():
        relationships.setdefault(
            (source, target),
            {
                "source": source,
                "target": target,
                "type": "imports",
                "kind": "imports",
                "evidence": evidence,
            },
        )

    model = {
        "schema_version": "0.1",
        "system": {"name": ctx.root.name or "repository"},
        "components": [
            {
                "id": c["name"],
                "name": c["name"],
                "type": c["kind"],
                "evidence": c.get("evidence", ""),
            }
            for c in components
        ],
        "relationships": [relationships[key] for key in sorted(relationships)],
    }
    answer: dict[str, Any] = {
        "status": "found",
        "origin": "inferred",
        "limits": limits,
        "model": model,
    }
    declared = _declared(ctx, limits)
    if declared is not None:
        answer["drift"] = _drift(declared, model)
    return answer

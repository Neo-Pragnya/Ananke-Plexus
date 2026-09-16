"""CALM architecture helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ananke.plexus.core.paths import ensure_project_layout
from ananke.plexus.graph.service import build_graph, load_graph, persist_graph


def _calm_path(repository_root: Path) -> Path:
    layout = ensure_project_layout(repository_root)
    return layout["architecture"] / "system.calm.json"


def _default_calm() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "system": {"name": "ananke-system"},
        "components": [],
        "relationships": [],
    }


def init_calm(repository_root: Path) -> Path:
    path = _calm_path(repository_root)
    if not path.exists():
        path.write_text(json.dumps(_default_calm(), indent=2) + "\n", encoding="utf-8")
    return path


def load_calm(repository_root: Path) -> dict[str, object]:
    path = init_calm(repository_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else _default_calm()


def _component_name(component: object) -> str:
    if not isinstance(component, dict):
        return ""
    raw_name = component.get("name", component.get("id", ""))
    return str(raw_name).strip()


def validate_calm(repository_root: Path) -> dict[str, object]:
    payload = load_calm(repository_root)
    issues: list[str] = []

    schema_version = str(payload.get("schema_version", "")).strip()
    if not schema_version:
        issues.append("schema_version missing")

    system = payload.get("system", {})
    system_name = str(system.get("name", "")).strip() if isinstance(system, dict) else ""
    if not system_name:
        issues.append("system.name missing")

    components = payload.get("components", [])
    if not isinstance(components, list):
        issues.append("components must be a list")
        components = []
    relationships = payload.get("relationships", [])
    if not isinstance(relationships, list):
        issues.append("relationships must be a list")
        relationships = []

    component_names: list[str] = []
    for index, component in enumerate(components, start=1):
        name = _component_name(component)
        if not name:
            issues.append(f"component[{index}] missing name")
            continue
        component_names.append(name)

    duplicates = sorted({name for name in component_names if component_names.count(name) > 1})
    for name in duplicates:
        issues.append(f"duplicate component: {name}")

    known = set(component_names)
    for index, relation in enumerate(relationships, start=1):
        if not isinstance(relation, dict):
            issues.append(f"relationship[{index}] must be an object")
            continue
        source = str(relation.get("source", "")).strip()
        target = str(relation.get("target", "")).strip()
        kind = str(relation.get("kind", "")).strip()
        if not source or not target or not kind:
            issues.append(f"relationship[{index}] missing source/target/kind")
            continue
        if source not in known:
            issues.append(f"relationship[{index}] unknown source: {source}")
        if target not in known:
            issues.append(f"relationship[{index}] unknown target: {target}")

    return {
        "ok": len(issues) == 0,
        "schema_version": schema_version,
        "system_name": system_name,
        "component_count": len(component_names),
        "relationship_count": len(relationships),
        "issue_count": len(issues),
        "issues": issues,
    }


def _graph_component_candidates(repository_root: Path) -> list[str]:
    snapshot = load_graph(repository_root)
    if snapshot is None:
        snapshot = build_graph(repository_root)
        persist_graph(repository_root, snapshot)
    roots: set[str] = set()
    for node in snapshot.nodes:
        if not node.path:
            continue
        part = node.path.split("/", 1)[0].strip()
        if part:
            roots.add(part)
    return sorted(roots)


def diff_calm(repository_root: Path) -> dict[str, object]:
    payload = load_calm(repository_root)
    components = payload.get("components", [])
    component_names: list[str] = []
    if isinstance(components, list):
        for component in components:
            name = _component_name(component)
            if name:
                component_names.append(name)
    component_names = sorted(component_names)
    graph_names = _graph_component_candidates(repository_root)
    missing_from_calm = sorted(name for name in graph_names if name not in component_names)
    missing_from_graph = sorted(name for name in component_names if name not in graph_names)
    return {
        "ok": len(missing_from_calm) == 0 and len(missing_from_graph) == 0,
        "calm_components": component_names,
        "graph_components": graph_names,
        "missing_from_calm": missing_from_calm,
        "missing_from_graph": missing_from_graph,
    }


def reconcile_calm(repository_root: Path, apply: bool = False) -> dict[str, object]:
    payload = load_calm(repository_root)
    diff = diff_calm(repository_root)
    missing_from_calm = diff.get("missing_from_calm", [])
    if not isinstance(missing_from_calm, list):
        missing_from_calm = []

    if not apply:
        return {
            "ok": True,
            "applied": False,
            "added_count": len(missing_from_calm),
            "added": missing_from_calm,
        }

    components_obj = payload.get("components", [])
    components = components_obj if isinstance(components_obj, list) else []
    for name in missing_from_calm:
        components.append({"name": name, "kind": "graph-overlay", "path": name})
    payload["components"] = components
    path = _calm_path(repository_root)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "applied": True,
        "added_count": len(missing_from_calm),
        "added": missing_from_calm,
        "path": str(path),
    }


def render_calm(repository_root: Path) -> dict[str, object]:
    payload = load_calm(repository_root)
    diff = diff_calm(repository_root)
    missing_from_calm = diff.get("missing_from_calm", [])
    missing_from_graph = diff.get("missing_from_graph", [])
    graph_components = diff.get("graph_components", [])
    missing_from_calm_count = len(missing_from_calm) if isinstance(missing_from_calm, list) else 0
    missing_from_graph_count = (
        len(missing_from_graph) if isinstance(missing_from_graph, list) else 0
    )
    components = payload.get("components", [])
    relationships = payload.get("relationships", [])
    system_name = "ananke-system"
    system = payload.get("system", {})
    if isinstance(system, dict):
        system_name = str(system.get("name", system_name))

    lines = [
        f"# Architecture Render: {system_name}",
        "",
        "## Validation",
        f"- Missing from CALM: {missing_from_calm_count}",
        f"- Missing from Graph: {missing_from_graph_count}",
        "",
        "## Mermaid",
        "```mermaid",
        "flowchart LR",
    ]

    component_names: list[str] = []
    if isinstance(components, list):
        for component in components:
            name = _component_name(component)
            if not name:
                continue
            component_names.append(name)
            alias = name.replace("-", "_").replace(" ", "_")
            lines.append(f"    {alias}[{name}]")

    if isinstance(relationships, list):
        for relation in relationships:
            if not isinstance(relation, dict):
                continue
            source = str(relation.get("source", "")).replace("-", "_").replace(" ", "_")
            target = str(relation.get("target", "")).replace("-", "_").replace(" ", "_")
            kind = str(relation.get("kind", "uses"))
            if source and target:
                lines.append(f"    {source} -->|{kind}| {target}")

    lines.extend(["```", "", "## Graph Overlay"])
    if isinstance(graph_components, list):
        for name in graph_components:
            label = str(name)
            status = "present" if label in component_names else "missing"
            lines.append(f"- {label}: {status}")

    render_path = ensure_project_layout(repository_root)["architecture"] / "diagrams.md"
    render_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "path": str(render_path),
        "component_count": len(component_names),
        "overlay_count": len(graph_components) if isinstance(graph_components, list) else 0,
    }

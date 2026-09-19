"""Published JSON Schemas for IDE validation (spec §161, §162)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ananke.plexus.registry.lockfile import Lockfile
from ananke.plexus.registry.models import KNOWN_CAPABILITIES, ArtifactKind, ArtifactManifest

SCHEMA_ID_BASE = "https://neo-pragnya.github.io/Ananke-Plexus/schemas"


def _manifest_schema(kind: ArtifactKind | None) -> dict[str, Any]:
    schema = ArtifactManifest.model_json_schema(by_alias=True)
    props = schema.setdefault("properties", {})
    if kind is not None:
        props["kind"] = {
            "const": kind.value,
            "type": "string",
            "description": f"Artifact kind: {kind.value}",
        }
    caps = props.get("capabilities", {})
    caps["items"] = {
        "anyOf": [
            {"enum": list(KNOWN_CAPABILITIES)},
            {"type": "string", "pattern": r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"},
        ],
        "description": "Hierarchical capability id; the enum lists the canonical taxonomy, custom ids are allowed.",
    }
    props["capabilities"] = caps
    return schema


def registry_json_schemas() -> dict[str, dict[str, Any]]:
    """name -> JSON Schema for skill/agent/bundle manifests, the export manifest and the lockfile."""
    out: dict[str, dict[str, Any]] = {}
    for name, kind in (
        ("skill-manifest", ArtifactKind.SKILL),
        ("agent-manifest", ArtifactKind.AGENT),
        ("bundle-manifest", ArtifactKind.BUNDLE),
    ):
        s = _manifest_schema(kind)
        s["$id"] = f"{SCHEMA_ID_BASE}/{name}.schema.json"
        s["title"] = f"Ananke {kind.value} manifest"
        out[name] = s
    out["registry-export"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_ID_BASE}/registry-export.schema.json",
        "title": "Ananke registry export manifest",
        "type": "object",
        "required": ["format", "schema", "snapshot", "counts"],
        "properties": {
            "format": {"const": "ananke-registry-export"},
            "schema": {"type": "integer", "minimum": 1},
            "registry_id": {"type": "string"},
            "snapshot": {"type": "string"},
            "exported_at": {"type": "string"},
            "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
        },
    }
    lock = Lockfile.model_json_schema()
    lock["$id"] = f"{SCHEMA_ID_BASE}/lockfile.schema.json"
    lock["title"] = "ananke.lock"
    out["lockfile"] = lock
    return out


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in registry_json_schemas().items():
        p = directory / f"{name}.schema.json"
        p.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(p)
    return written

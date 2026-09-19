"""JSON-friendly views of registry records for the HTTP API, MCP tools and the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ananke.plexus.registry.models import VersionRecord
from ananke.plexus.registry.quality import badges

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


def record_to_dict(
    rec: VersionRecord, registry: Registry | None = None, *, detail: bool = True
) -> dict[str, Any]:
    m = rec.manifest
    out: dict[str, Any] = {
        "uri": rec.uri,
        "ref": f"{rec.namespace}/{rec.name}@{rec.version}",
        "kind": rec.kind.value,
        "namespace": rec.namespace,
        "name": rec.name,
        "version": rec.version,
        "summary": rec.summary,
        "lifecycle": rec.lifecycle.value,
        "channel": rec.channel,
        "trust": rec.trust.value,
        "digest": rec.digest,
        "revision": rec.revision,
        "license": rec.license.expression,
        "capabilities": list(m.capabilities),
        "runtimes": list(m.runtime.supported),
        "tags": list(m.metadata.tags),
        "badges": badges(rec, registry is not None and registry.is_signed(rec)),
    }
    if detail:
        out.update(
            {
                "description": rec.description,
                "tools": [t.model_dump(mode="json", exclude_none=True) for t in m.tools],
                "inputs": m.inputs.json_schema if m.inputs else None,
                "outputs": m.outputs.json_schema if m.outputs else None,
                "permissions": m.permissions.model_dump(mode="json"),
                "dependencies": [
                    d.model_dump(mode="json", exclude_none=True) for d in m.all_dependencies()
                ],
                "compatibility": m.compatibility.model_dump(mode="json", exclude_defaults=True),
                "provenance": rec.provenance.model_dump(
                    mode="json", exclude_none=True, exclude={"native_schema"}
                ),
                "quality": rec.quality.model_dump(mode="json"),
                "security": rec.security.model_dump(mode="json"),
                "created_at": rec.created_at,
                "replacement": rec.replacement,
                "lifecycle_message": rec.lifecycle_message,
            }
        )
        if registry is not None:
            out["versions"] = [
                v.version for v in registry.store.versions_of(rec.kind, rec.namespace, rec.name)
            ]
    return out

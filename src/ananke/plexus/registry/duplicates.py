"""Potential-duplicate detection (spec §119). Humans/policy decide merge or alias."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from ananke.plexus.registry.hashing import canonical_json, sha256_hex

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


class DuplicateGroup(BaseModel):
    reason: str
    artifacts: list[str]


def find_duplicates(registry: Registry) -> list[DuplicateGroup]:
    same_digest: dict[str, set[str]] = {}
    same_source: dict[tuple[str, str, str], set[str]] = {}
    same_tools: dict[str, set[str]] = {}
    same_caps: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for rec in registry.store.list_records():
        same_digest.setdefault(rec.digest_sha256, set()).add(rec.uri)
        p = rec.provenance
        if p.source_name and p.native_id:
            same_source.setdefault((p.source_type, p.source_name, p.native_id), set()).add(rec.uri)
        if rec.manifest.tools:
            key = sha256_hex(
                canonical_json(
                    [t.model_dump(mode="json", exclude_none=True) for t in rec.manifest.tools]
                )
            )
            same_tools.setdefault(key, set()).add(rec.uri)
        if rec.manifest.capabilities:
            same_caps.setdefault((rec.kind.value, tuple(rec.manifest.capabilities)), set()).add(
                rec.uri
            )
    groups: list[DuplicateGroup] = []
    for name, index in (
        ("same payload digest", {k: v for k, v in same_digest.items()}),
        ("same source identifier", {str(k): v for k, v in same_source.items()}),
        ("same tool schemas", same_tools),
        ("same capability set (low confidence)", {str(k): v for k, v in same_caps.items()}),
    ):
        for uris in index.values():
            if len(uris) > 1:
                groups.append(DuplicateGroup(reason=name, artifacts=sorted(uris)))
    return sorted(groups, key=lambda g: (g.reason, g.artifacts))

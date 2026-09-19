"""Lexical search over the registry (spec §13, §70, §106, §107, §123, §124).

FTS5 is used when SQLite provides it; otherwise a pure-Python scorer gives the same
results. Optional similarity search lives in :mod:`semantic` and is off unless policy enables it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ananke.plexus.registry.models import (
    TRUST_RANK,
    ArtifactKind,
    LifecycleStatus,
    TrustStatus,
    VersionRecord,
)

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_FILTER_KEYS = {
    "kind",
    "capability",
    "cap",
    "runtime",
    "trust",
    "channel",
    "license",
    "tag",
    "namespace",
    "ns",
    "lifecycle",
}
_HIDDEN = {LifecycleStatus.YANKED, LifecycleStatus.QUARANTINED, LifecycleStatus.ARCHIVED}


class ParsedQuery(BaseModel):
    terms: list[str] = Field(default_factory=list)
    filters: dict[str, list[str]] = Field(default_factory=dict)


def parse_query(text: str) -> ParsedQuery:
    """Registry query DSL: ``kind:skill capability:graph.query runtime:pydantic graph review``."""
    parsed = ParsedQuery()
    for token in text.split():
        key, sep, value = token.partition(":")
        if sep and key.lower() in _FILTER_KEYS and value:
            k = {"cap": "capability", "ns": "namespace"}.get(key.lower(), key.lower())
            parsed.filters.setdefault(k, []).append(value)
        else:
            parsed.terms.append(token)
    return parsed


class SearchHit(BaseModel):
    uri: str
    kind: ArtifactKind
    namespace: str
    name: str
    version: str
    summary: str = ""
    score: float = 0.0
    trust: TrustStatus
    channel: str
    lifecycle: LifecycleStatus
    capabilities: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)
    license: str | None = None
    tags: list[str] = Field(default_factory=list)
    versions_count: int = 1

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}@{self.version}"


def _cap_matches(have: list[str], want: str) -> bool:
    if want.endswith(".*"):
        want = want[:-2]
    return any(c == want or c.startswith(want + ".") for c in have)


def _passes(rec: VersionRecord, filters: dict[str, list[str]]) -> bool:
    m = rec.manifest
    for value in filters.get("kind", []):
        if rec.kind.value != value:
            return False
    for value in filters.get("namespace", []):
        if rec.namespace != value:
            return False
    for value in filters.get("capability", []):
        if not _cap_matches(m.capabilities, value):
            return False
    for value in filters.get("runtime", []):
        supported = set(m.runtime.supported) | (
            {m.runtime.provider} if m.runtime.provider else set()
        )
        if value not in supported:
            return False
    for value in filters.get("trust", []):
        if rec.trust.value != value:
            return False
    for value in filters.get("channel", []):
        if rec.channel != value:
            return False
    for value in filters.get("license", []):
        if value.lower() not in (rec.license.expression or "").lower():
            return False
    for value in filters.get("tag", []):
        if value.lower() not in m.metadata.tags:
            return False
    return all(rec.lifecycle.value == value for value in filters.get("lifecycle", []))


def _python_score(rec: VersionRecord, terms: list[str]) -> float:
    if not terms:
        return 0.0
    m = rec.manifest
    fields = [
        (rec.name.replace("-", " ").replace("_", " ").lower(), 10.0),
        (" ".join(m.metadata.tags).lower(), 6.0),
        (" ".join(m.capabilities).replace(".", " ").lower(), 5.0),
        (" ".join(f"{t.name} {t.description}" for t in m.tools).lower(), 2.0),
        (rec.summary.lower(), 4.0),
        (rec.description.lower(), 1.0),
        (rec.namespace.lower(), 1.0),
    ]
    total = 0.0
    for raw in terms:
        term = raw.lower()
        hit = False
        for text, weight in fields:
            if term in text:
                total += weight
                hit = True
        if not hit:
            return -1.0
    return total


def _boost(rec: VersionRecord, terms: list[str]) -> float:
    boost = 0.0
    joined = " ".join(t.lower() for t in terms)
    if joined and joined in {rec.name, f"{rec.namespace}/{rec.name}"}:
        boost += 100.0
    for t in terms:
        low = t.lower()
        if rec.name.startswith(low):
            boost += 5.0
        if low in rec.manifest.metadata.tags:
            boost += 8.0
        if any(low == c or low in c.split(".") for c in rec.manifest.capabilities):
            boost += 4.0
    return boost


def _fts_expr(terms: list[str], op: str) -> str:
    toks = [t.lower() for term in terms for t in _TOKEN.findall(term)]
    return f" {op} ".join(f'"{t}"*' for t in toks)


def _hit(rec: VersionRecord, score: float, count: int) -> SearchHit:
    return SearchHit(
        uri=rec.uri,
        kind=rec.kind,
        namespace=rec.namespace,
        name=rec.name,
        version=rec.version,
        summary=rec.summary,
        score=round(score, 3),
        trust=rec.trust,
        channel=rec.channel,
        lifecycle=rec.lifecycle,
        capabilities=list(rec.manifest.capabilities),
        runtimes=list(rec.manifest.runtime.supported),
        license=rec.license.expression,
        tags=list(rec.manifest.metadata.tags),
        versions_count=count,
    )


def search(
    registry: Registry,
    query: str = "",
    *,
    kind: ArtifactKind | str | None = None,
    capability: str | list[str] | None = None,
    runtime: str | None = None,
    trust: str | None = None,
    channel: str | None = None,
    license: str | None = None,
    tag: str | None = None,
    namespace: str | None = None,
    limit: int = 20,
    all_versions: bool = False,
    include_inactive: bool = False,
    semantic: bool = False,
) -> list[SearchHit]:
    parsed = parse_query(query)
    filters = {k: list(v) for k, v in parsed.filters.items()}

    def add(key: str, value: str | list[str] | None) -> None:
        if value:
            filters.setdefault(key, []).extend([value] if isinstance(value, str) else value)

    add("kind", kind.value if isinstance(kind, ArtifactKind) else kind)
    add("capability", capability)
    add("runtime", runtime)
    add("trust", trust)
    add("channel", channel)
    add("license", license)
    add("tag", tag)
    add("namespace", namespace)
    terms = parsed.terms
    store = registry.store

    if semantic:
        from ananke.plexus.registry.semantic import semantic_search

        return semantic_search(
            registry,
            " ".join(terms),
            filters=filters,
            include_inactive=include_inactive,
            limit=limit,
            all_versions=all_versions,
        )

    scored: dict[int, float] = {}
    records: dict[int, VersionRecord] = {}
    if terms and store.fts_available:
        for op in ("AND", "OR"):
            expr = _fts_expr(terms, op)
            if not expr:
                break
            rows = store.fts_match(expr, 2000)
            if rows:
                scored = {vid: -rank for vid, rank in rows}
                break
        for vid in scored:
            records[vid] = store.record_by_id(vid)
    else:
        for rec in store.list_records():
            records[rec.db_id] = rec
            if not terms:
                scored[rec.db_id] = 0.0
                continue
            s = _python_score(rec, terms)
            if s >= 0:
                scored[rec.db_id] = s

    hits: dict[tuple[str, str, str], list[tuple[float, VersionRecord]]] = {}
    for vid, base in scored.items():
        rec = records[vid]
        if not include_inactive and rec.lifecycle in _HIDDEN:
            continue
        if not _passes(rec, filters):
            continue
        hits.setdefault(rec.key, []).append((base + _boost(rec, terms), rec))

    out: list[SearchHit] = []
    for group in hits.values():
        if all_versions:
            out.extend(_hit(rec, s, len(group)) for s, rec in group)
        else:
            best = max(group, key=lambda t: (t[1].semver.sort_key, TRUST_RANK[t[1].trust]))
            score = max(s for s, _ in group)
            out.append(_hit(best[1], score, len(group)))
    out.sort(key=lambda h: (-h.score, h.namespace, h.name, h.version))
    return out[:limit]


def latest_per_artifact(registry: Registry, kind: ArtifactKind | str) -> list[VersionRecord]:
    latest: list[VersionRecord] = []
    for art in registry.store.list_artifacts(kind):
        recs = [
            r
            for r in registry.store.versions_of(art.kind, art.namespace, art.name)
            if r.lifecycle not in _HIDDEN
        ]
        if recs:
            latest.append(recs[-1])
    return latest


def search_agents(
    registry: Registry,
    *,
    skill: str | None = None,
    runtime: str | None = None,
    capability: str | None = None,
) -> list[SearchHit]:
    """Find agents composing a skill or providing a capability (spec §123)."""
    out: list[SearchHit] = []
    skill_index: dict[str, set[str]] = {}
    for rec in latest_per_artifact(registry, ArtifactKind.SKILL):
        skill_index[f"{rec.namespace}/{rec.name}"] = set(rec.manifest.capabilities)
    for agent in latest_per_artifact(registry, ArtifactKind.AGENT):
        m = agent.manifest
        refs = {s.ref.split("@")[0] for s in m.skills}
        if skill:
            want = skill.split("@")[0]
            if not any(r == want or r.endswith("/" + want) for r in refs):
                continue
        if runtime:
            supported = set(m.runtime.supported) | (
                {m.runtime.provider} if m.runtime.provider else set()
            )
            if runtime not in supported:
                continue
        if capability:
            caps = set(m.capabilities)
            for r in refs:
                caps |= skill_index.get(r, set())
            if not _cap_matches(sorted(caps), capability):
                continue
        out.append(_hit(agent, 0.0, 1))
    return sorted(out, key=lambda h: (h.namespace, h.name))


def recommend_skills(registry: Registry, capability: str, limit: int = 5) -> list[SearchHit]:
    """Policy-eligible skills providing ``capability`` (spec §124). Deterministic order."""
    policy = registry.policy
    min_rank = TRUST_RANK[policy.resolve.minimum_trust]
    out: list[VersionRecord] = []
    for rec in latest_per_artifact(registry, ArtifactKind.SKILL):
        if not _cap_matches(rec.manifest.capabilities, capability):
            continue
        if rec.lifecycle is not LifecycleStatus.ACTIVE:
            continue
        if TRUST_RANK[rec.trust] < min_rank or rec.channel not in policy.resolve.channel:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.uri)
    out.sort(key=lambda r: (TRUST_RANK[r.trust], r.semver.sort_key), reverse=True)
    return [_hit(r, 0.0, 1) for r in out[:limit]]

"""Optional similarity search (spec §14).

Off by default and never required: lexical search is complete without it. Enabled by policy
(``[semantic] enabled = true``). The built-in provider, ``local-hash``, is a deterministic
feature-hashing embedder (word + character-trigram features, sublinear tf, L2-normalised). It is
*not* a neural model: it finds artifacts that share vocabulary, word forms and spelling with the
query, which catches typos and morphology that exact-term search misses, but it does not know that
"lint" and "static analysis" mean the same thing. For real semantics plug in an embedder through
the ``ananke.registry.embedders`` entry-point group.

Governance (spec §14): providers that send text off-machine declare ``remote = True`` and are
refused unless ``[semantic] allow_remote = true``. Vectors are derived state kept in the rebuildable
cache keyed by content digest and embedder version; they are never authoritative and never part of
an export.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Protocol

from ananke.plexus.registry.cache import KVCache
from ananke.plexus.registry.errors import PolicyViolationError, RegistryError
from ananke.plexus.registry.models import VersionRecord

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry
    from ananke.plexus.registry.search import SearchHit

LOCAL_PROVIDER = "local-hash"
MIN_SCORE = 0.08
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")
_SUFFIXES = ("ing", "ed", "es", "ly", "s")


class Embedder(Protocol):
    model: str
    version: str
    remote: bool

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(h, "little")
    return n % dim, (1.0 if (n >> 63) & 1 else -1.0)


class LocalHashEmbedder:
    model = LOCAL_PROVIDER
    version = "2"
    remote = False

    def __init__(self, dim: int = 2048) -> None:
        self.dim = dim

    def tokens(self, text: str) -> list[str]:
        return [_stem(w) for w in _WORD.findall(_CAMEL.sub(" ", text).lower())]

    def embed_one(self, text: str) -> list[float]:
        counts: Counter[str] = Counter()
        for tok in self.tokens(text):
            counts[f"w:{tok}"] += 1
            padded = f"^{tok}$"
            for n in (2, 3):
                for i in range(len(padded) - n + 1):
                    counts[f"c{n}:{padded[i : i + n]}"] += 1
        vec = [0.0] * self.dim
        for feature, n in counts.items():
            idx, sign = _bucket(feature, self.dim)
            weight = 1.0 if feature.startswith("w:") else 0.4
            vec[idx] += sign * weight * (1.0 + math.log(n))
        norm = math.sqrt(sum(v * v for v in vec))
        return [round(v / norm, 6) for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


def record_text(rec: VersionRecord) -> str:
    m = rec.manifest
    parts = [
        rec.name.replace("-", " ").replace("_", " "),
        rec.namespace,
        rec.summary,
        rec.description,
        " ".join(c.replace(".", " ") for c in m.capabilities),
        " ".join(m.metadata.tags),
        " ".join(f"{t.name} {t.description}" for t in m.tools),
    ]
    return " . ".join(p for p in parts if p)


def get_embedder(registry: Registry) -> Embedder:
    policy = registry.policy.semantic
    if not policy.enabled:
        raise PolicyViolationError(
            "semantic search is disabled by policy (set [semantic] enabled = true)",
            code="SEMANTIC_DISABLED",
        )
    if policy.provider == LOCAL_PROVIDER:
        return LocalHashEmbedder()
    for ep in importlib.metadata.entry_points().select(group="ananke.registry.embedders"):
        if ep.name != policy.provider:
            continue
        obj = ep.load()
        embedder: Embedder = obj() if isinstance(obj, type) else obj
        if embedder.remote and not policy.allow_remote:
            raise PolicyViolationError(
                f"embedder {policy.provider!r} sends text to a remote service; "
                "set [semantic] allow_remote = true to permit it",
                code="SEMANTIC_REMOTE_DENIED",
            )
        return embedder
    raise RegistryError(
        f"unknown embedding provider {policy.provider!r} "
        f"(built in: {LOCAL_PROVIDER}; plugins: entry-point group ananke.registry.embedders)",
        code="SEMANTIC_PROVIDER_UNKNOWN",
    )


def _encode(vec: list[float]) -> str:
    """Sparse JSON: embeddings of short texts are mostly zeros."""
    idx = [i for i, v in enumerate(vec) if v]
    return json.dumps({"n": len(vec), "i": idx, "v": [vec[i] for i in idx]}, separators=(",", ":"))


def _decode(raw: str) -> list[float] | None:
    try:
        data = json.loads(raw)
        vec = [0.0] * int(data["n"])
        for i, v in zip(data["i"], data["v"], strict=True):
            vec[int(i)] = float(v)
    except (ValueError, TypeError, KeyError):
        return None
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def _vectors(
    registry: Registry, embedder: Embedder, records: list[VersionRecord]
) -> list[list[float]]:
    """Vectors keyed by a hash of the embedded *text* (summary/description are mutable metadata,
    so the payload digest alone would go stale)."""
    cache = KVCache(registry.root / "cache.db")
    prefix = f"emb:{embedder.model}:{embedder.version}:"
    texts = [record_text(rec) for rec in records]
    keys = [prefix + hashlib.sha256(t.encode("utf-8")).hexdigest()[:32] for t in texts]
    out: list[list[float] | None] = []
    for key in keys:
        vec: list[float] | None = None
        cached = cache.get(key)
        if cached is not None:
            vec = _decode(cached)
        out.append(vec)
    missing = [i for i, v in enumerate(out) if v is None]
    if missing:
        fresh = embedder.embed([texts[i] for i in missing])
        for i, vec in zip(missing, fresh, strict=True):
            out[i] = vec
            cache.set(keys[i], _encode(vec))
    return [v or [] for v in out]


def semantic_search(
    registry: Registry,
    query: str,
    *,
    filters: dict[str, list[str]],
    include_inactive: bool,
    limit: int,
    all_versions: bool,
) -> list[SearchHit]:
    from ananke.plexus.registry.search import _HIDDEN, _hit, _passes

    embedder = get_embedder(registry)
    if not query.strip():
        return []
    candidates = [
        rec
        for rec in registry.store.list_records()
        if (include_inactive or rec.lifecycle not in _HIDDEN) and _passes(rec, filters)
    ]
    if not candidates:
        return []
    (qvec,) = embedder.embed([query])
    scores = [_cosine(qvec, v) for v in _vectors(registry, embedder, candidates)]
    by_key: dict[tuple[str, str, str], list[tuple[float, VersionRecord]]] = {}
    for rec, score in zip(candidates, scores, strict=True):
        if score >= MIN_SCORE:
            by_key.setdefault(rec.key, []).append((score, rec))
    hits: list[SearchHit] = []
    for group in by_key.values():
        if all_versions:
            hits.extend(_hit(rec, s, len(group)) for s, rec in group)
        else:
            best = max(group, key=lambda t: (t[1].semver.sort_key, t[0]))
            hits.append(_hit(best[1], max(s for s, _ in group), len(group)))
    hits.sort(key=lambda h: (-h.score, h.namespace, h.name, h.version))
    return hits[:limit]

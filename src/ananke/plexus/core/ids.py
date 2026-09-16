"""Stable ID generation utilities (A1).

All Ananke IDs are content-addressed where possible, otherwise they are
prefixed UUID-hex strings that encode the entity type for readability.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4


def new_id(prefix: str = "id") -> str:
    """Return a unique ID with the given prefix, e.g. ``run-a1b2c3d4``."""
    return f"{prefix}-{uuid4().hex[:8]}"


def run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def spec_id(requirement_id: str) -> str:
    return f"spec-{requirement_id.lower().replace(' ', '-')}-{uuid4().hex[:6]}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def idempotency_key(*parts: str) -> str:
    """Deterministic key from an ordered set of string parts."""
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

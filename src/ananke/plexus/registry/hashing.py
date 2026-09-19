"""Canonical JSON and digests (spec §11, §12).

SHA-256 is the content address (always available, identical everywhere). BLAKE3 is
recorded additionally when the optional ``blake3`` package is installed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blake3_hex(data: bytes) -> str | None:
    try:
        import blake3
    except ImportError:
        return None
    return str(blake3.blake3(data).hexdigest())


def blake3_available() -> bool:
    return blake3_hex(b"") is not None


def digest_pair(data: bytes) -> tuple[str, str | None]:
    return sha256_hex(data), blake3_hex(data)


def strip_algo(digest: str) -> str:
    """``sha256:abc`` -> ``abc``."""
    return digest.split(":", 1)[1] if ":" in digest else digest


def with_algo(hex_digest: str, algo: str = "sha256") -> str:
    return hex_digest if ":" in hex_digest else f"{algo}:{hex_digest}"

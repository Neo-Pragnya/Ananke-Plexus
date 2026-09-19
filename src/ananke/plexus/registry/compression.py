"""Archive compression with graceful fallback (spec §96 names ``tar.zst``).

Zstandard is used when available (Python 3.14 ``compression.zstd`` or the optional
``zstandard`` package); otherwise gzip. Reading sniffs the magic bytes, so archives are
portable regardless of which side had zstd.
"""

from __future__ import annotations

import gzip
import io
from typing import Literal

from ananke.plexus.registry.errors import PayloadError, RegistryError

Algo = Literal["auto", "gzip", "zstd"]
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _zstd_compress(data: bytes) -> bytes | None:
    try:
        from compression import zstd

        return bytes(zstd.compress(data))
    except ImportError:
        pass
    try:
        import zstandard

        return bytes(zstandard.ZstdCompressor(level=10).compress(data))
    except ImportError:
        return None


def _zstd_decompress(data: bytes, limit: int) -> bytes:
    try:
        from compression import zstd

        return bytes(zstd.decompress(data))
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError as exc:
        raise RegistryError(
            "this archive is zstd-compressed; install the 'registry-zstd' extra "
            "(pip install ananke-plexus[registry-zstd]) or use Python 3.14+"
        ) from exc
    # bounded output: a tiny frame must not be able to expand into gigabytes
    return bytes(zstandard.ZstdDecompressor().decompress(data, max_output_size=limit + 1))


def zstd_available() -> bool:
    return _zstd_compress(b"") is not None


def compress(data: bytes, algo: Algo = "auto") -> tuple[bytes, str]:
    """Return ``(bytes, extension)`` where extension is ``zst`` or ``gz``."""
    if algo in {"auto", "zstd"}:
        out = _zstd_compress(data)
        if out is not None:
            return out, "zst"
        if algo == "zstd":
            raise RegistryError(
                "zstd requested but neither compression.zstd nor 'zstandard' is available"
            )
    return gzip.compress(data, mtime=0), "gz"


def decompress(data: bytes, limit: int = 2_000_000_000) -> bytes:
    """Decompress gzip/zstd/plain bytes. Every codec failure (truncation, corruption, bombs) is
    reported as :class:`PayloadError` so callers only ever see registry errors."""
    try:
        if data[:4] == _ZSTD_MAGIC:
            out = _zstd_decompress(data, limit)
        elif data[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                out = gz.read(limit + 1)
        else:
            out = data  # plain tar
    except RegistryError:
        raise
    except Exception as exc:  # zlib.error, BadGzipFile, EOFError, ZstdError, ...
        raise PayloadError(f"corrupt or truncated compressed archive: {exc}") from exc
    if len(out) > limit:
        raise PayloadError("archive expands beyond the size limit")
    return out

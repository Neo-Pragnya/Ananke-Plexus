"""Deterministic payload packing and safe unpacking (spec §11, §130).

A payload is a ``path -> bytes`` mapping packed into a reproducible tar: sorted
entries, zeroed owners/mtimes, normalised modes. The same source therefore always
yields the same digest. Unpacking never trusts archive metadata: no absolute paths,
``..`` segments, links or devices, and every write is re-checked against the target root.
"""

from __future__ import annotations

import io
import os
import posixpath
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ananke.plexus.registry.errors import PayloadError
from ananke.plexus.registry.hashing import sha256_hex

MAX_FILES = 5_000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MANIFEST_FILE = "ananke.registry.json"

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}
_SKIP_FILES = {".DS_Store", "Thumbs.db"}
_SKIP_SUFFIXES = (".pyc", ".pyo")


@dataclass
class CollectReport:
    files: dict[str, bytes] = field(default_factory=dict)
    skipped_symlinks: list[str] = field(default_factory=list)
    skipped_other: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str


def safe_relpath(path: str) -> str:
    """Validate and normalise an in-payload path; raises PayloadError if unsafe."""
    if not path or "\\" in path or path.startswith("/") or "\x00" in path:
        raise PayloadError(f"unsafe payload path: {path!r}")
    norm = posixpath.normpath(path)
    parts = PurePosixPath(norm).parts
    if norm in {".", ""} or ".." in parts or (parts and parts[0].endswith(":")):
        raise PayloadError(f"unsafe payload path: {path!r}")
    return norm


def collect_directory(root: Path) -> CollectReport:
    """Read a source directory into memory, refusing to follow symlinks."""
    root = root.resolve()
    if not root.is_dir():
        raise PayloadError(f"not a directory: {root}")
    report = CollectReport()
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        base = Path(dirpath)
        for d in list(dirnames):
            if (base / d).is_symlink():
                report.skipped_symlinks.append(str((base / d).relative_to(root)))
                dirnames.remove(d)
        for fname in sorted(filenames):
            full = base / fname
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                report.skipped_symlinks.append(rel)
                continue
            if fname in _SKIP_FILES or fname.endswith(_SKIP_SUFFIXES):
                report.skipped_other.append(rel)
                continue
            if not full.is_file():
                report.skipped_other.append(rel)
                continue
            size = full.stat().st_size
            if size > MAX_FILE_BYTES:
                raise PayloadError(f"file too large ({size} bytes): {rel}")
            total += size
            if len(report.files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise PayloadError("payload exceeds size limits")
            report.files[safe_relpath(rel)] = full.read_bytes()
    return report


def pack_files(files: dict[str, bytes]) -> bytes:
    if len(files) > MAX_FILES or sum(len(v) for v in files.values()) > MAX_TOTAL_BYTES:
        raise PayloadError("payload exceeds size limits")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(files):
            rel = safe_relpath(path)
            data = files[path]
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _iter_members(data: bytes) -> list[tuple[tarfile.TarInfo, bytes]]:
    out: list[tuple[tarfile.TarInfo, bytes]] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                if not member.isreg():
                    raise PayloadError(f"non-regular archive member rejected: {member.name!r}")
                safe_relpath(member.name)
                if member.size > MAX_FILE_BYTES:
                    raise PayloadError(f"archive member too large: {member.name!r}")
                total += member.size
                if len(out) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise PayloadError("payload exceeds size limits")
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover - guarded by isreg
                    raise PayloadError(f"unreadable member: {member.name!r}")
                out.append((member, handle.read()))
    except tarfile.TarError as exc:
        raise PayloadError(f"corrupt payload archive: {exc}") from exc
    return out


def unpack_files(data: bytes) -> dict[str, bytes]:
    return {safe_relpath(m.name): content for m, content in _iter_members(data)}


def list_payload(data: bytes) -> list[FileEntry]:
    return [
        FileEntry(safe_relpath(m.name), len(content), sha256_hex(content))
        for m, content in _iter_members(data)
    ]


def extract_to(data: bytes, dest: Path) -> list[str]:
    """Extract ``data`` into ``dest`` (created). Writes files manually so no archive
    metadata (modes, links, owners) is ever applied."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    written: list[str] = []
    for member, content in _iter_members(data):
        rel = safe_relpath(member.name)
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            raise PayloadError(f"archive path escapes destination: {member.name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            handle.write(content)
        written.append(rel)
    return sorted(written)


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root`` refusing traversal and symlink escapes."""
    rel = safe_relpath(relative)
    base = root.resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise PayloadError(f"path escapes source root: {relative!r}")
    return target

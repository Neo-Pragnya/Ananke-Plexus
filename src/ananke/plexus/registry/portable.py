"""Portable export/import and backup/restore (spec §96, §97, §135, §159, §160, §167).

The export archive is database-independent::

    manifest.json  artifacts.jsonl  versions.jsonl  dependencies.jsonl
    aliases.jsonl  events.jsonl     blobs/<sha256>  checksums.txt

Import validates checksums, schema version, blob hashes, version immutability
(collisions with different content abort the *whole* import) and local policy, then applies
everything in one transaction. ``import(export(r))`` reproduces ``r``'s semantic state.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ananke.plexus.registry.compression import Algo, compress, decompress
from ananke.plexus.registry.errors import (
    IntegrityError,
    PayloadError,
    RegistryError,
    SchemaVersionError,
    VersionContentConflictError,
)
from ananke.plexus.registry.hashing import sha256_hex
from ananke.plexus.registry.models import (
    REGISTRY_SCHEMA_VERSION,
    ArtifactKind,
    ArtifactManifest,
    LicenseInfo,
    LifecycleStatus,
    Provenance,
    Quality,
    Security,
    TrustStatus,
    VersionRecord,
)
from ananke.plexus.registry.payload import list_payload, safe_relpath, unpack_files
from ananke.plexus.registry.signing import check_signature, validate_key_id
from ananke.plexus.registry.store import VersionInsert

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

FORMAT = "ananke-registry-export"
_MEMBER_RE = re.compile(
    r"^(manifest\.json|artifacts\.jsonl|versions\.jsonl|dependencies\.jsonl|aliases\.jsonl|"
    r"events\.jsonl|checksums\.txt|blobs/[0-9a-f]{64})$"
)
_MAX_MEMBER = 256 * 1024 * 1024
_MAX_TOTAL = 4 * 1024 * 1024 * 1024


class ExportResult(BaseModel):
    path: str
    versions: int
    blobs: int
    bytes: int
    compression: str
    snapshot: str


class ImportResult(BaseModel):
    versions_added: int = 0
    versions_existing: int = 0
    aliases_added: int = 0
    events_added: int = 0
    blobs_added: int = 0
    warnings: list[str] = Field(default_factory=list)
    schema_version: int = REGISTRY_SCHEMA_VERSION
    snapshot: str = ""


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows
    ).encode()


def _tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(members, key=lambda n: (n == "checksums.txt", n)):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(members[name]))
    return buf.getvalue()


def _version_row(registry: Registry, rec: VersionRecord) -> dict[str, Any]:
    vid = registry.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
    return {
        "kind": rec.kind.value,
        "namespace": rec.namespace,
        "name": rec.name,
        "version": rec.version,
        "digest_sha256": rec.digest_sha256,
        "digest_blake3": rec.digest_blake3,
        "payload_size": rec.payload_size,
        "lifecycle": rec.lifecycle.value,
        "lifecycle_message": rec.lifecycle_message,
        "replacement": rec.replacement,
        "channel": rec.channel,
        "trust": rec.trust.value,
        "summary": rec.summary,
        "description": rec.description,
        "revision": rec.revision,
        "created_at": rec.created_at,
        "manifest": rec.manifest.canonical_dict(),
        "provenance": json.loads(rec.provenance.model_dump_json(exclude_none=True)),
        "license": rec.license.model_dump(mode="json"),
        "quality": rec.quality.model_dump(mode="json"),
        "security": rec.security.model_dump(mode="json"),
        "trust_history": registry.store.trust_history(vid),
        "channel_history": registry.store.channel_history(vid),
        "revisions": registry.store.revisions_of(vid),
        # verification is recomputed by the importing registry against *its* trusted keys
        "signatures": registry.store.signatures_of(vid),
    }


def _build_members(
    registry: Registry,
    records: list[VersionRecord],
    *,
    include_events: bool,
    include_aliases: bool,
    subset: bool = False,
) -> tuple[dict[str, bytes], int, int]:
    store = registry.store
    wanted = {(r.kind, r.namespace, r.name) for r in records}
    artifacts = [
        {
            "kind": a.kind.value,
            "namespace": a.namespace,
            "name": a.name,
            "owners": store.owners_of(a.kind, a.namespace, a.name),
        }
        for a in store.list_artifacts()
        if not subset or (a.kind, a.namespace, a.name) in wanted
    ]
    deps: list[dict[str, Any]] = []
    for rec in records:
        vid = store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        for d in store.dependencies_of(vid):
            deps.append({"from": rec.version_uri, **d})
    members: dict[str, bytes] = {}
    members["artifacts.jsonl"] = _jsonl(artifacts)
    members["versions.jsonl"] = _jsonl([_version_row(registry, r) for r in records])
    members["dependencies.jsonl"] = _jsonl(deps)
    members["aliases.jsonl"] = _jsonl(store.list_aliases() if include_aliases else [])
    members["events.jsonl"] = _jsonl(store.list_events(limit=10_000_000) if include_events else [])
    blob_count = 0
    for digest in sorted({r.digest_sha256 for r in records}):
        members[f"blobs/{digest}"] = registry.cas.get(digest)
        blob_count += 1
    snapshot = registry.snapshot_id()
    members["manifest.json"] = json.dumps(
        {
            "format": FORMAT,
            "schema": REGISTRY_SCHEMA_VERSION,
            "registry_id": registry.registry_id,
            "snapshot": snapshot,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "counts": {
                "artifacts": len(artifacts),
                "versions": len(records),
                "blobs": blob_count,
                "events": members["events.jsonl"].count(b"\n"),
            },
        },
        sort_keys=True,
        indent=1,
    ).encode()
    members["checksums.txt"] = "".join(
        f"{sha256_hex(members[n])}  {n}\n" for n in sorted(members)
    ).encode()
    return members, blob_count, len(records)


def export_bundle(registry: Registry, uris: list[str], *, compression: Algo = "gzip") -> bytes:
    """A portable archive of just the named exact versions (used by the registry server).

    No events and no aliases: a bundle carries content and metadata, not the sender's history.
    """
    records = [registry.exact_version(u) for u in uris]
    members, _blobs, _n = _build_members(
        registry, records, include_events=False, include_aliases=False, subset=True
    )
    data, _ext = compress(_tar(members), compression)
    return data


def export_registry(
    registry: Registry, out: Path | None = None, *, compression: Algo = "auto"
) -> ExportResult:
    records = registry.store.list_records()
    members, blob_count, _n = _build_members(
        registry, records, include_events=True, include_aliases=True
    )
    snapshot = registry.snapshot_id()
    data, ext = compress(_tar(members), compression)
    target = out or (registry.root / f"ananke-registry.tar.{ext}")
    if out is not None and out.is_dir():
        target = out / f"ananke-registry.tar.{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    registry.emit("registry.exported", None, {"versions": len(records), "file": target.name})
    return ExportResult(
        path=str(target),
        versions=len(records),
        blobs=blob_count,
        bytes=len(data),
        compression=ext,
        snapshot=snapshot,
    )


def bundle_digests(archive: Path) -> dict[str, str]:
    """Verify an archive's integrity and return ``{"ananke://…@ver": sha256}`` without importing."""
    members = _read_archive(archive)
    _verify_members(members)
    return {
        f"ananke://{r['kind']}/{r['namespace']}/{r['name']}@{r['version']}": r["digest_sha256"]
        for r in _rows(members, "versions.jsonl")
    }


def _read_archive(path: Path) -> dict[str, bytes]:
    """Read and validate an export archive into memory (names allow-listed, sizes capped)."""
    try:
        raw = decompress(path.read_bytes())
    except OSError as exc:
        raise PayloadError(f"cannot read archive: {exc}") from exc
    members: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for m in tar.getmembers():
                if m.isdir():
                    continue
                if not m.isreg() or not _MEMBER_RE.match(m.name):
                    raise PayloadError(f"unexpected archive member rejected: {m.name!r}")
                if m.size > _MAX_MEMBER:
                    raise PayloadError(f"archive member too large: {m.name!r}")
                total += m.size
                if total > _MAX_TOTAL:
                    raise PayloadError("archive exceeds size limits")
                handle = tar.extractfile(m)
                assert handle is not None  # noqa: S101 - regular file
                members[m.name] = handle.read()
    except tarfile.TarError as exc:
        raise PayloadError(f"corrupt registry archive: {exc}") from exc
    return members


@contextlib.contextmanager
def _malformed() -> Iterator[None]:
    """Archives are untrusted input: structural surprises are integrity errors, not crashes."""
    try:
        yield
    except RegistryError:
        raise
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise IntegrityError(f"malformed registry archive: {exc}") from exc


def _verify_members(members: dict[str, bytes]) -> dict[str, Any]:
    with _malformed():
        return _verify_members_unchecked(members)


def _verify_members_unchecked(members: dict[str, bytes]) -> dict[str, Any]:
    for required in ("manifest.json", "checksums.txt", "versions.jsonl"):
        if required not in members:
            raise IntegrityError(f"archive is missing {required}")
    listed: dict[str, str] = {}
    for line in members["checksums.txt"].decode().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    for name, data in members.items():
        if name == "checksums.txt":
            continue
        if listed.get(name) != sha256_hex(data):
            raise IntegrityError(f"checksum mismatch for {name}")
    for name in listed:
        if name not in members:
            raise IntegrityError(f"checksums.txt lists missing member {name}")
    manifest: dict[str, Any] = json.loads(members["manifest.json"])
    if manifest.get("format") != FORMAT:
        raise IntegrityError("not an Ananke registry export")
    schema = int(manifest.get("schema", 0))
    if schema > REGISTRY_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"export uses schema {schema}; this version supports up to {REGISTRY_SCHEMA_VERSION}"
        )
    if schema < 1:
        raise SchemaVersionError(f"invalid export schema {schema}")
    for name, data in members.items():
        if name.startswith("blobs/") and name[6:] != sha256_hex(data):
            raise IntegrityError(f"blob {name} does not match its content hash")
    return manifest


def _rows(members: dict[str, bytes], name: str) -> list[dict[str, Any]]:
    with _malformed():
        rows = [
            json.loads(line)
            for line in members.get(name, b"").decode().splitlines()
            if line.strip()
        ]
        if not all(isinstance(r, dict) for r in rows):
            raise IntegrityError(f"{name} must contain one JSON object per line")
        return rows


def import_registry(
    registry: Registry,
    archive: Path,
    *,
    preserve_trust: bool = True,
    skip_policy: bool = False,
    dry_run: bool = False,
) -> ImportResult:
    """Import an export archive. All-or-nothing: any problem aborts before writing metadata."""
    with _malformed():
        return _import_registry(
            registry,
            archive,
            preserve_trust=preserve_trust,
            skip_policy=skip_policy,
            dry_run=dry_run,
        )


def _import_registry(
    registry: Registry,
    archive: Path,
    *,
    preserve_trust: bool,
    skip_policy: bool,
    dry_run: bool,
) -> ImportResult:
    members = _read_archive(archive)
    manifest = _verify_members(members)
    result = ImportResult(schema_version=int(manifest["schema"]))
    rows = _rows(members, "versions.jsonl")
    plan: list[tuple[dict[str, Any], ArtifactManifest, dict[str, bytes], bytes]] = []
    for row in rows:
        key = f"{row['kind']}/{row['namespace']}/{row['name']}@{row['version']}"
        digest = row["digest_sha256"]
        blob = members.get(f"blobs/{digest}")
        if blob is None:
            raise IntegrityError(f"archive has no blob for {key}")
        m = ArtifactManifest.model_validate(row["manifest"])
        existing = registry.store.get_record(
            row["kind"], row["namespace"], row["name"], row["version"]
        )
        if existing is not None:
            if existing.digest_sha256 != digest:
                raise VersionContentConflictError(
                    f"import collision: {key} exists locally with different content "
                    "(versions are immutable; nothing was imported)"
                )
            result.versions_existing += 1
            continue
        files = unpack_files(blob)
        if not skip_policy:
            prov = Provenance.model_validate(row["provenance"])
            registry.validate(m, files, prov)
        plan.append((row, m, files, blob))
    if dry_run:
        result.versions_added = len(plan)
        return result

    for _row, _m, _files, blob in plan:
        registry.cas.put(blob)
        result.blobs_added += 1
    known_events = registry.store.event_ids()
    with registry.store.transaction():
        for row, m, _files, blob in plan:
            trust = TrustStatus(row["trust"]) if preserve_trust else TrustStatus.DISCOVERED
            lifecycle = LifecycleStatus(row["lifecycle"])
            channel = row["channel"] if preserve_trust else "candidate"
            if lifecycle is LifecycleStatus.QUARANTINED:
                trust, channel = TrustStatus.QUARANTINED, "quarantined"
            vid = registry.store.insert_version(
                VersionInsert(
                    manifest=m,
                    version=row["version"],
                    digest_sha256=row["digest_sha256"],
                    digest_blake3=row.get("digest_blake3"),
                    payload_size=int(row["payload_size"]),
                    provenance=Provenance.model_validate(row["provenance"]),
                    license=LicenseInfo.model_validate(row["license"]),
                    files=list_payload(blob),
                    schemas=registry.collect_schemas(m),
                    channel=channel,
                    trust=trust,
                    lifecycle=lifecycle,
                    quality=Quality.model_validate(row["quality"]),
                    security=Security.model_validate(row["security"]),
                    created_at=row["created_at"],
                    actor=registry.actor,
                )
            )
            registry.store.restore_state(
                vid,
                summary=row["summary"],
                description=row["description"],
                revision=int(row["revision"]),
                lifecycle_message=row.get("lifecycle_message"),
                replacement=row.get("replacement"),
                trust_history=row["trust_history"] if preserve_trust else [],
                channel_history=row["channel_history"] if preserve_trust else [],
                revisions=row["revisions"],
                quality=Quality.model_validate(row["quality"]),
                security=Security.model_validate(row["security"]),
                license=LicenseInfo.model_validate(row["license"]),
            )
            for sig in row.get("signatures", []):
                registry.store.add_signature(
                    vid,
                    key_id=validate_key_id(sig["key_id"]),
                    algorithm=sig["algorithm"],
                    signature=sig["signature"],
                    signer=sig.get("signer"),
                    created_at=sig["created_at"],
                )
            result.versions_added += 1
        for al in _rows(members, "aliases.jsonl"):
            if registry.store.get_alias(al["alias"]) is None:
                registry.store.set_alias(
                    al["alias"],
                    ArtifactKind(al["kind"]),
                    al["namespace"],
                    al["name"],
                    al["created_at"],
                )
                result.aliases_added += 1
            else:
                result.warnings.append(
                    f"alias {al['alias']!r} already exists locally; kept local value"
                )
        for ev in _rows(members, "events.jsonl"):
            if ev["event_id"] not in known_events:
                registry.store.append_event(
                    ev["event_type"],
                    ev.get("artifact_uri"),
                    ev.get("actor") or "import",
                    ev.get("payload", {}),
                    event_id=ev["event_id"],
                    created_at=ev["created_at"],
                )
                result.events_added += 1
        registry.emit(
            "registry.imported", None, {"versions": result.versions_added, "from": archive.name}
        )
    unverified = 0
    for row in rows:
        uri = f"ananke://{row['kind']}/{row['namespace']}/{row['name']}@{row['version']}"
        if any(
            not check_signature(
                registry.policy.signing,
                version_uri=uri,
                sha256_hex=row["digest_sha256"],
                key_id=s["key_id"],
                signature_b64=s["signature"],
                algorithm=s["algorithm"],
            ).verified
            for s in row.get("signatures", [])
        ):
            unverified += 1
    if unverified:
        result.warnings.append(
            f"{unverified} imported version(s) carry signatures that do not verify against this "
            "registry's trusted keys (they remain untrusted)"
        )
    result.snapshot = registry.snapshot_id()
    return result


# --------------------------------------------------------------------------- backup


def backup(registry: Registry, out_dir: Path | None = None, *, compression: Algo = "auto") -> Path:
    """Consistent filesystem-level backup: SQLite snapshot + reachable blobs + locks + policy."""
    registry.store.wal_checkpoint()
    records = registry.store.list_records()
    with tempfile.TemporaryDirectory(prefix="ananke-backup-") as tmp:
        snap = Path(tmp) / "registry.db"
        registry.store.backup_to(snap)
        members: dict[str, bytes] = {"registry.db": snap.read_bytes()}
    reachable = {r.digest_sha256 for r in records} | registry.store.locked_digests()
    for digest in sorted(reachable):
        if registry.cas.exists(digest):
            members[f"blobs/sha256/{digest[:2]}/{digest}"] = registry.cas.get(digest)
    for name in ("policy.toml", "events.jsonl"):
        f = registry.root / name
        if f.is_file():
            members[name] = f.read_bytes()
    if registry.project_root is not None:
        for rel in ("ananke.lock", ".ananke/apm.lock", ".ananke/activation.toml"):
            f = registry.project_root / rel
            if f.is_file():
                members[f"project/{rel}"] = f.read_bytes()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    members["BACKUP.json"] = json.dumps(
        {
            "format": "ananke-registry-backup",
            "schema": REGISTRY_SCHEMA_VERSION,
            "created": stamp,
            "blobs": len(reachable),
        },
        sort_keys=True,
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(members[name]))
    data, ext = compress(buf.getvalue(), compression)
    target_dir = out_dir or (registry.root / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"registry-backup-{stamp}.tar.{ext}"
    target.write_bytes(data)
    return target


def restore(archive: Path, target_root: Path, *, force: bool = False) -> Path:
    """Restore a backup into ``target_root`` (a registry directory). Refuses non-empty targets."""
    from ananke.plexus.registry.registry import Registry as _Registry

    if target_root.exists() and any(target_root.iterdir()) and not force:
        raise RegistryError(f"{target_root} is not empty; pass force to overwrite")
    raw = decompress(archive.read_bytes())
    if force and target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    root = target_root.resolve()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            names = [m.name for m in tar.getmembers()]
            if "BACKUP.json" not in names or "registry.db" not in names:
                raise IntegrityError("not an Ananke registry backup")
            for m in tar.getmembers():
                if m.isdir():
                    continue
                if not m.isreg() or m.name.startswith("project/"):
                    continue
                rel = safe_relpath(m.name)
                dest = (root / rel).resolve()
                if root != dest and root not in dest.parents:
                    raise PayloadError(f"backup member escapes target: {m.name!r}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                handle = tar.extractfile(m)
                assert handle is not None  # noqa: S101
                dest.write_bytes(handle.read())
    except tarfile.TarError as exc:
        raise PayloadError(f"corrupt backup archive: {exc}") from exc
    conn = sqlite3.connect(root / "registry.db")
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if ok != "ok":
        raise IntegrityError(f"restored database failed integrity check: {ok}")
    _Registry.open(root).close()
    return root

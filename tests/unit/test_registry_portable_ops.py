"""Export/import, backup/restore, verify/doctor, GC, reports, analytics (spec §95-§97, §105, §135, §136, §144-§146, §167)."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.compression import compress, decompress, zstd_available
from ananke.plexus.registry.errors import (
    IntegrityError,
    PayloadError,
    PolicyViolationError,
    RegistryError,
    SchemaVersionError,
    VersionContentConflictError,
)
from ananke.plexus.registry.gc import gc
from ananke.plexus.registry.lockfile import build_lock, write_lock
from ananke.plexus.registry.models import LifecycleStatus, Quality, Security, TrustStatus
from ananke.plexus.registry.policy import RegistryPolicy, enterprise_policy
from ananke.plexus.registry.portable import backup, export_registry, restore
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.reports import REPORTS, build_analytics, report
from ananke.plexus.registry.resolver import Resolver
from ananke.plexus.registry.verify import doctor, verify


def populate(reg) -> None:  # type: ignore[no-untyped-def]
    add(reg, "graph-query", "1.0.0", approve=True)
    add(
        reg,
        "graph-review",
        "1.0.0",
        dependencies=[{"skill": "core/graph-query", "version": "^1"}],
        metadata={"tags": ["graph"]},
        runtime={"supported": ["pydantic"]},
        files={"README.md": b"one", "prompts/p.md": b"prompt"},
    )
    add(
        reg,
        "graph-review",
        "2.0.0",
        capabilities=["graph.query", "graph.write"],
        files={"README.md": b"two"},
    )
    add(
        reg,
        "coder",
        "1.0.0",
        kind="agent",
        namespace="eng",
        skills=[{"ref": "core/graph-review", "version": "^2"}],
    )
    reg.promote(
        "core/graph-review@2.0.0",
        trust=TrustStatus.APPROVED,
        channel="stable",
        run_gate=False,
        reason="ok",
    )
    reg.yank("core/graph-review@1.0.0", "old")
    reg.correct_metadata("core/graph-query@1.0.0", summary="corrected summary")
    reg.update_quality(
        "core/graph-query@1.0.0",
        Quality(tests_status="pass", last_verified_at="2026-01-01T00:00:00+00:00"),
    )
    reg.alias_set("gr", "core/graph-review")


class TestCompression:
    def test_gzip_roundtrip_and_sniffing(self) -> None:
        data, ext = compress(b"hello" * 100, "gzip")
        assert ext == "gz" and decompress(data) == b"hello" * 100
        assert decompress(b"plain tar bytes") == b"plain tar bytes"

    def test_auto_picks_zstd_only_when_available(self) -> None:
        _, ext = compress(b"x", "auto")
        assert ext == ("zst" if zstd_available() else "gz")

    def test_explicit_zstd_without_support_is_an_error(self) -> None:
        if zstd_available():
            pytest.skip("zstd available")
        with pytest.raises(RegistryError, match="zstd"):
            compress(b"x", "zstd")

    def test_decompression_bomb_limit(self) -> None:
        data, _ = compress(b"\0" * 10_000, "gzip")
        with pytest.raises(RegistryError, match="size limit"):
            decompress(data, limit=100)


class TestExportImport:
    def test_round_trip_reproduces_semantic_state(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        populate(src)
        res = src.export_archive(tmp_path / "out.tar.gz", compression="gzip")
        assert res.versions == 4 and res.blobs == 4 and Path(res.path).exists()
        dst = open_registry(tmp_path / "b")
        imp = dst.import_archive(Path(res.path))
        assert imp.versions_added == 4 and imp.aliases_added == 1 and imp.events_added > 0
        assert dst.semantic_state() == src.semantic_state()  # import(export(r)) == r (spec §167)
        assert dst.snapshot_id() == src.snapshot_id()
        assert dst.verify(deep=True).ok

    def test_history_and_revisions_preserved(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        populate(src)
        dst = open_registry(tmp_path / "b")
        dst.import_archive(Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path))
        rec = dst.exact_version("core/graph-review@2.0.0")
        vid = dst.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        assert [t["status"] for t in dst.store.trust_history(vid)] == [
            "discovered",
            "verified",
            "approved",
        ]
        assert dst.revisions("core/graph-review@2.0.0")[0]["reason"] == "ok"
        assert dst.exact_version("core/graph-query@1.0.0").summary == "corrected summary"
        assert dst.exact_version("core/graph-review@1.0.0").lifecycle is LifecycleStatus.YANKED

    def test_import_is_idempotent_and_merges(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        populate(src)
        archive = Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path)
        dst = open_registry(tmp_path / "b")
        dst.import_archive(archive)
        again = dst.import_archive(archive)
        assert (
            again.versions_added == 0 and again.versions_existing == 4 and again.aliases_added == 0
        )
        add(dst, "local-only", "1.0.0")
        assert dst.import_archive(archive).versions_added == 0 and dst.store.count_versions() == 5

    def test_collision_with_different_content_aborts_everything(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        add(src, "x", "1.0.0", files={"README.md": b"from source"})
        add(src, "y", "1.0.0")
        archive = Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path)
        dst = open_registry(tmp_path / "b")
        add(dst, "x", "1.0.0", files={"README.md": b"LOCAL"})
        with pytest.raises(VersionContentConflictError, match="import collision"):
            dst.import_archive(archive)
        assert dst.store.count_versions() == 1  # y was not imported either
        with pytest.raises(RegistryError):
            dst.exact_version("core/y@1.0.0")

    def test_policy_applies_to_imported_versions(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        add(src, "x", "1.0.0", license={"expression": "GPL-3.0-only"})
        archive = Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path)
        dst = open_registry(tmp_path / "b", enterprise_policy())
        with pytest.raises(PolicyViolationError, match="LICENSE_DENIED"):
            dst.import_archive(archive)
        assert dst.store.count_versions() == 0
        assert dst.import_archive(archive, skip_policy=True).versions_added == 1

    def test_preserve_trust_false_resets(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        add(src, "x", "1.0.0", approve=True)
        add(src, "bad", "1.0.0")
        src.quarantine("core/bad@1.0.0", "m")
        dst = open_registry(tmp_path / "b")
        dst.import_archive(
            Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path),
            preserve_trust=False,
        )
        x = dst.exact_version("core/x@1.0.0")
        assert x.trust is TrustStatus.DISCOVERED and x.channel == "candidate"
        assert (
            dst.exact_version("core/bad@1.0.0").trust is TrustStatus.QUARANTINED
        )  # quarantine is never dropped

    def test_dry_run(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        populate(src)
        archive = Path(src.export_archive(tmp_path / "o.tar.gz", compression="gzip").path)
        dst = open_registry(tmp_path / "b")
        assert (
            dst.import_archive(archive, dry_run=True).versions_added == 4
            and dst.store.count_versions() == 0
        )

    def _rewrite(self, archive: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
        members: dict[str, bytes] = {}
        with tarfile.open(archive) as tar:
            for m in tar.getmembers():
                f = tar.extractfile(m)
                if f:
                    members[m.name] = f.read()
        mutate(members)
        out = archive.with_name("mutated.tar")
        with tarfile.open(out, "w") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return out

    def test_tampered_archive_detected(self, tmp_path: Path) -> None:
        src = open_registry(tmp_path / "a")
        add(src, "x", "1.0.0")
        archive = Path(src.export_archive(tmp_path / "o.tar", compression="gzip").path)
        # decompress so we can edit members
        raw = decompress(archive.read_bytes())
        plain = tmp_path / "plain.tar"
        plain.write_bytes(raw)
        dst = open_registry(tmp_path / "b")

        def flip_blob(members: dict[str, bytes]) -> None:
            name = next(n for n in members if n.startswith("blobs/"))
            members[name] = members[name] + b"x"

        with pytest.raises(IntegrityError, match="checksum mismatch"):
            dst.import_archive(self._rewrite(plain, flip_blob))

        def drop_checksums(members: dict[str, bytes]) -> None:
            del members["checksums.txt"]

        with pytest.raises(IntegrityError, match=r"missing checksums\.txt"):
            dst.import_archive(self._rewrite(plain, drop_checksums))

        def future_schema(members: dict[str, bytes]) -> None:
            manifest = json.loads(members["manifest.json"])
            manifest["schema"] = 99
            members["manifest.json"] = json.dumps(manifest).encode()
            from ananke.plexus.registry.hashing import sha256_hex

            lines = [
                ln
                for ln in members["checksums.txt"].decode().splitlines()
                if not ln.endswith("manifest.json")
            ]
            lines.append(f"{sha256_hex(members['manifest.json'])}  manifest.json")
            members["checksums.txt"] = ("\n".join(lines) + "\n").encode()

        with pytest.raises(SchemaVersionError):
            dst.import_archive(self._rewrite(plain, future_schema))
        assert dst.store.count_versions() == 0

    def test_hostile_archive_members_rejected(self, tmp_path: Path) -> None:
        dst = open_registry(tmp_path / "b")
        for name in ("../evil", "/abs", "blobs/../../x", "other.txt"):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name)
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            f = tmp_path / "evil.tar"
            f.write_bytes(buf.getvalue())
            with pytest.raises(PayloadError):
                dst.import_archive(f)
        link_buf = io.BytesIO()
        with tarfile.open(fileobj=link_buf, mode="w") as tar:
            info = tarfile.TarInfo("manifest.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        (tmp_path / "link.tar").write_bytes(link_buf.getvalue())
        with pytest.raises(PayloadError):
            dst.import_archive(tmp_path / "link.tar")

    def test_not_an_export(self, tmp_path: Path) -> None:
        dst = open_registry(tmp_path / "b")
        (tmp_path / "junk").write_bytes(b"junk")
        with pytest.raises(PayloadError):
            dst.import_archive(tmp_path / "junk")

    def test_export_directory_target_and_default_name(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        out_dir = tmp_path / "exports"
        out_dir.mkdir()
        res = export_registry(reg, out_dir, compression="gzip")
        assert Path(res.path).parent == out_dir and res.path.endswith(".tar.gz")
        assert Path(export_registry(reg, compression="gzip").path).parent == reg.root

    def test_export_is_deterministic_apart_from_timestamp(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        a = decompress(
            Path(reg.export_archive(tmp_path / "a.tar.gz", compression="gzip").path).read_bytes()
        )
        b = decompress(
            Path(reg.export_archive(tmp_path / "b.tar.gz", compression="gzip").path).read_bytes()
        )

        def names(raw: bytes) -> list[str]:
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                return sorted(tar.getnames())

        assert names(a) == names(b)
        assert (
            "checksums.txt" in names(a)
            and "manifest.json" in names(a)
            and any(n.startswith("blobs/") for n in names(a))
        )


class TestBackup:
    def test_backup_and_restore(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path / "a")
        populate(reg)
        archive = backup(reg, tmp_path / "bk", compression="gzip")
        assert archive.name.startswith("registry-backup-")
        restored = restore(archive, tmp_path / "restored")
        r2 = Registry.open(restored)
        assert r2.semantic_state() == reg.semantic_state() and r2.verify(deep=True).ok

    def test_restore_refuses_non_empty_target(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path / "a")
        add(reg)
        archive = backup(reg, tmp_path / "bk", compression="gzip")
        target = tmp_path / "t"
        target.mkdir()
        (target / "keep").write_text("x")
        with pytest.raises(RegistryError, match="not empty"):
            restore(archive, target)
        restore(archive, target, force=True)

    def test_restore_rejects_foreign_archive(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path / "a")
        add(reg)
        export = Path(reg.export_archive(tmp_path / "e.tar.gz", compression="gzip").path)
        with pytest.raises(IntegrityError, match="not an Ananke registry backup"):
            restore(export, tmp_path / "x")

    def test_backup_includes_project_locks(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        add(reg)
        (proj / "ananke.lock").write_text("version = 1\n")
        archive = backup(reg, compression="gzip")
        with tarfile.open(fileobj=io.BytesIO(decompress(archive.read_bytes()))) as tar:
            names = tar.getnames()
        assert "project/ananke.lock" in names and "registry.db" in names and "BACKUP.json" in names


class TestVerifyDoctor:
    def test_healthy_registry(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        rep = verify(reg)
        assert rep.ok and {
            "sqlite-integrity",
            "blobs",
            "semver",
            "aliases",
            "search-index",
            "lockfiles",
        } <= {c.name for c in rep.checks}
        d = doctor(reg)
        assert d.ok and {
            "wal-mode",
            "resolver",
            "docs-templates",
            "importers",
            "accelerators",
            "cache-rebuildable",
        } <= {c.name for c in d.checks}
        assert "fts5=" in next(c for c in d.checks if c.name == "accelerators").detail

    def test_detects_missing_and_corrupt_blobs(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        a, b = add(reg, "a", "1.0.0"), add(reg, "b", "1.0.0")
        ra, rb = reg.exact_version(a), reg.exact_version(b)
        reg.cas.delete(ra.digest_sha256)
        blob = reg.cas._path(rb.digest_sha256)
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        rep = verify(reg)
        assert not rep.ok
        detail = next(c for c in rep.checks if c.name == "blobs").detail
        assert "blob missing" in detail and "mismatch" in detail

    def test_detects_index_drift_and_rebuild_repairs(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        reg.store.conn.execute("DELETE FROM artifact_fts")
        rep = verify(reg)
        assert not next(c for c in rep.checks if c.name == "search-index").ok
        assert reg.rebuild_index() == 4
        assert verify(reg).ok and [h.name for h in reg.search("graph review")]

    def test_detects_dangling_alias_and_missing_dependency_target(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "app", "1.0.0", dependencies=[{"skill": "core/ghost", "version": "^1"}])
        reg.store.set_alias("ghosty", reg.locate("core/app")[0], "core", "gone", reg.now())
        rep = verify(reg)
        assert not next(c for c in rep.checks if c.name == "aliases").ok
        dep = next(c for c in rep.checks if c.name == "dependency-targets")
        assert not dep.ok and dep.severity == "warning"

    def test_detects_stale_lockfile(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg, "lib", "1.0.0")
        lock = build_lock(Resolver(reg).resolve("core/lib"), [("ananke://skill/core/lib", "*")])
        write_lock(reg, tmp_path / "ananke.lock", lock)
        assert verify(reg).ok
        reg.quarantine(ref, "bad")
        rep = verify(reg)
        assert (
            not rep.ok
            and "quarantined" in next(c for c in rep.checks if c.name == "lockfiles").detail
        )

    def test_cycle_detection_helper(self) -> None:
        from ananke.plexus.registry.verify import _cycles

        assert _cycles({"a": {"b"}, "b": {"c"}, "c": set()}) == []
        found = _cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}})
        assert found and found[0][0] == found[0][-1] and set(found[0]) == {"a", "b", "c"}
        assert _cycles({"self": {"self"}}) == [["self", "self"]]

    def test_orphan_blobs_reported_as_info(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        reg.cas.put(b"orphan")
        c = next(c for c in verify(reg).checks if c.name == "orphan-blobs")
        assert not c.ok and c.severity == "info"

    def test_verify_of_empty_registry(self, tmp_path: Path) -> None:
        assert verify(open_registry(tmp_path)).ok


class TestGc:
    def test_dry_run_default_deletes_nothing(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        orphan = reg.cas.put(b"orphan")
        rep = gc(reg, grace_seconds=0)
        assert (
            rep.dry_run
            and rep.unreachable == [orphan]
            and reg.cas.exists(orphan)
            and rep.deleted == []
        )

    def test_deletes_only_unreachable_past_grace(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg, "kept", "1.0.0")
        yanked = add(reg, "yanked", "1.0.0")
        reg.yank(yanked)
        rec_kept = reg.exact_version(ref)
        rec_yanked = reg.exact_version(yanked)
        old = reg.cas.put(b"old orphan")
        young = reg.cas.put(b"young orphan")
        import os

        past = time.time() - 30 * 86400
        blob = reg.cas._path(old)
        os.utime(blob, (past, past))
        rep = gc(reg, dry_run=False, grace_seconds=7 * 86400)
        assert (
            rep.deleted == [old] and rep.within_grace == 1 and rep.bytes_freed == len(b"old orphan")
        )
        assert (
            reg.cas.exists(young)
            and reg.cas.exists(rec_kept.digest_sha256)
            and reg.cas.exists(rec_yanked.digest_sha256)
        )

    def test_locked_blobs_are_retained(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg, "lib", "1.0.0")
        lock = build_lock(Resolver(reg).resolve("core/lib"), [("ananke://skill/core/lib", "*")])
        write_lock(reg, tmp_path / "ananke.lock", lock)
        rec = reg.exact_version(ref)
        reg.unregister(
            ref, purge=True, force=True
        )  # version row gone, lock still references the blob
        rep = gc(reg, dry_run=False, grace_seconds=0)
        assert rec.digest_sha256 not in rep.deleted and reg.cas.exists(rec.digest_sha256)

    def test_evidence_referenced_blobs_retained(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        digest = reg.cas.put(b"payload-in-evidence")
        ev = proj / ".ananke/evidence/run-1"
        ev.mkdir(parents=True)
        (ev / "registry-capabilities.json").write_text(
            json.dumps({"artifacts": [{"digest": f"sha256:{digest}"}]})
        )
        rep = gc(reg, dry_run=False, grace_seconds=0)
        assert digest not in rep.deleted and reg.cas.exists(digest)

    def test_materialized_cleanup_keeps_activated(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.activation import activate, materialize

        reg = open_registry(tmp_path)
        a, b = add(reg, "a", "1.0.0"), add(reg, "b", "1.0.0")
        proj = tmp_path / "proj"
        proj.mkdir()
        activate(reg, proj, a)
        materialize(reg, reg.exact_version(b))
        rep = gc(reg, dry_run=False)
        assert rep.materialized_removed == [reg.exact_version(b).digest_sha256]
        assert (reg.root / "materialized" / reg.exact_version(a).digest_sha256).is_dir()


class TestReports:
    def test_all_reports_render(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        for name in REPORTS:
            t = report(reg, name)
            assert t.columns and t.to_csv().splitlines()[0].split(",")[0] == t.columns[0]
            json.loads(t.to_json())

    def test_inventory_and_licenses_and_trust(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        inv = report(reg, "inventory")
        assert len(inv.rows) == 4 and inv.columns[:3] == ["artifact", "version", "lifecycle"]
        assert report(reg, "licenses").rows == [["Apache-2.0", "approved", "4"]]
        assert dict(map(tuple, report(reg, "trust").rows))["approved"] == "2"  # type: ignore[arg-type]

    def test_deprecated_shows_dependents(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        reg.deprecate("core/graph-query@1.0.0", "old", replacement="core/gq2")
        rows = report(reg, "deprecated").rows
        assert any(
            r[0] == "skill/core/graph-query" and "core/graph-review@1.0.0" in r[4] for r in rows
        )

    def test_stale_uses_last_verified(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        rows = report(reg, "stale", stale_days=36500).rows
        assert all(r[0] != "skill/core/graph-query" for r in rows)  # verified recently enough
        assert any(r[2] == "never" for r in rows)

    def test_unused_needs_no_telemetry(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        t = report(reg, "unused")
        assert "telemetry is disabled" in t.note
        assert "skill/core/graph-query" not in [r[0] for r in t.rows]  # has dependents
        assert "agent/eng/coder" in [r[0] for r in t.rows]

    def test_local_usage_telemetry_opt_in(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.policy import TelemetryPolicy

        reg = open_registry(tmp_path, RegistryPolicy(telemetry=TelemetryPolicy(local_usage=True)))
        add(reg, "used", "1.0.0")
        add(reg, "idle", "1.0.0")
        reg.resolve("core/used")
        t = report(reg, "unused")
        assert [r[0] for r in t.rows] == ["skill/core/idle"] and not t.note
        off = open_registry(tmp_path / "off")
        add(off, "used", "1.0.0")
        off.resolve("core/used")
        assert off.store.usage_counts() == {}  # nothing recorded unless enabled

    def test_security_and_compatibility(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "x", "1.0.0", compatibility={"ananke": ">=0", "operating_systems": ["linux"]})
        reg.update_security(
            "core/x@1.0.0",
            Security(status="findings", findings_count=2, scanner="trivy", last_scanned_at="t"),
        )
        assert report(reg, "security").rows[0][2:5] == ["findings", "2", "0"]
        assert report(reg, "compatibility").rows[0][-2] == "linux"

    def test_unknown_report(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryError, match="unknown report"):
            report(open_registry(tmp_path), "nope")


class TestAnalytics:
    def test_jsonl_projection_without_duckdb(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        populate(reg)
        res = build_analytics(reg, tmp_path / "an")
        assert {
            "registry_versions.jsonl",
            "registry_dependencies.jsonl",
            "registry_events.jsonl",
            "registry_permissions.jsonl",
        } <= set(res.files)
        rows = [
            json.loads(ln)
            for ln in (tmp_path / "an/registry_versions.jsonl").read_text().splitlines()
        ]
        assert len(rows) == 4 and {r["trust"] for r in rows} >= {"approved", "discovered"}
        try:
            import duckdb  # noqa: F401
        except ImportError:
            assert res.duckdb is None and "duckdb not installed" in res.note

    def test_default_location(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        assert build_analytics(reg).directory == str(reg.root / "analytics")

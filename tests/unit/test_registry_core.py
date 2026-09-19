"""Registry kernel: models, CAS, payloads, store, registration, lifecycle (spec §3-§21, §55-§60)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from registry_support import add, artifact, open_registry

from ananke.plexus.registry.cas import ContentStore
from ananke.plexus.registry.errors import (
    IntegrityError,
    InvalidIdentityError,
    InvalidRequirementError,
    NotFoundError,
    NotInitializedError,
    PayloadError,
    PolicyViolationError,
    SecretDetectedError,
    VersionContentConflictError,
)
from ananke.plexus.registry.models import (
    ArtifactKind,
    ArtifactManifest,
    Dependency,
    LifecycleStatus,
    TrustStatus,
    artifact_uri,
    parse_ref,
)
from ananke.plexus.registry.payload import (
    MANIFEST_FILE,
    collect_directory,
    extract_to,
    list_payload,
    pack_files,
    safe_relpath,
    unpack_files,
)
from ananke.plexus.registry.policy import RegistryPolicy, SecretsPolicy
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.secrets import redact_files, scan_files
from ananke.plexus.registry.store import SUPPORTED_SCHEMA, RegistryStore

# --------------------------------------------------------------------------- models


class TestRefs:
    def test_uri_roundtrip(self) -> None:
        ref = parse_ref("ananke://skill/core/code-review@2.1.0")
        assert (ref.kind, ref.namespace, ref.name, ref.requirement) == (
            ArtifactKind.SKILL,
            "core",
            "code-review",
            "2.1.0",
        )
        assert (
            artifact_uri("skill", "core", "code-review", "2.1.0")
            == "ananke://skill/core/code-review@2.1.0"
        )

    def test_short_and_bare_forms(self) -> None:
        assert parse_ref("core/x@^2").requirement == "^2"
        bare = parse_ref("graph-review")
        assert bare.namespace is None and not bare.qualified

    @pytest.mark.parametrize(
        "bad", ["", "a/b/c", "ananke://skill/core", "Core/x", "core/../x", "ananke://widget/a/b"]
    )
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(InvalidIdentityError):
            parse_ref(bad)


class TestManifest:
    def test_dependency_shorthand(self) -> None:
        m = ArtifactManifest.model_validate(
            {
                "kind": "skill",
                "namespace": "core",
                "name": "x",
                "version": "1.0.0",
                "dependencies": [
                    {"skill": "core/repo-context", "version": "^1.4"},
                    {"python": "requests>=2"},
                ],
            }
        )
        deps = m.artifact_dependencies()
        assert deps[0].id == "ananke://skill/core/repo-context" and deps[0].version == "^1.4"
        assert m.dependencies[1].type.value == "python-package"

    def test_agent_skills_become_dependencies(self) -> None:
        m = ArtifactManifest.model_validate(
            {
                "kind": "agent",
                "namespace": "eng",
                "name": "coder",
                "version": "1.0.0",
                "skills": [{"ref": "core/graph-review", "version": "^2.0"}],
            }
        )
        (dep,) = m.artifact_dependencies()
        assert dep.id == "ananke://skill/core/graph-review" and dep.version == "^2.0"

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            ArtifactManifest.model_validate(
                {"kind": "skill", "namespace": "a", "name": "b", "version": "1.0.0", "bogus": 1}
            )

    def test_capability_ids_validated(self) -> None:
        with pytest.raises(ValueError):
            ArtifactManifest.model_validate(
                {
                    "kind": "skill",
                    "namespace": "a",
                    "name": "b",
                    "version": "1.0.0",
                    "capabilities": ["Bad Cap"],
                }
            )

    def test_future_schema_version_rejected(self) -> None:
        with pytest.raises(ValueError):
            ArtifactManifest.model_validate(
                {
                    "kind": "skill",
                    "namespace": "a",
                    "name": "b",
                    "version": "1.0.0",
                    "schema_version": 99,
                }
            )

    def test_network_bool_normalised(self) -> None:
        m = ArtifactManifest.model_validate(
            {
                "kind": "skill",
                "namespace": "a",
                "name": "b",
                "version": "1.0.0",
                "permissions": {"network": True},
            }
        )
        assert m.permissions.network == ["*"]

    def test_canonical_dict_omits_defaults(self) -> None:
        m = ArtifactManifest.model_validate(
            {"kind": "skill", "namespace": "a", "name": "b", "version": "1.0.0"}
        )
        assert m.canonical_dict() == {
            "kind": "skill",
            "namespace": "a",
            "name": "b",
            "version": "1.0.0",
        }

    def test_dependency_needs_namespace(self) -> None:
        with pytest.raises(InvalidIdentityError):
            Dependency.model_validate({"skill": "lonely"})


# --------------------------------------------------------------------------- CAS / payload


class TestContentStore:
    def test_put_get_dedupe(self, tmp_path: Path) -> None:
        cas = ContentStore(tmp_path)
        d1, d2 = cas.put(b"hello"), cas.put(b"hello")
        assert d1 == d2 and cas.get(d1) == b"hello" and cas.exists(d1)
        assert list(cas.iter_digests()) == [d1]

    def test_blobs_are_read_only(self, tmp_path: Path) -> None:
        cas = ContentStore(tmp_path)
        d = cas.put(b"x")
        assert not (cas._path(d).stat().st_mode & 0o222)

    def test_tamper_detected(self, tmp_path: Path) -> None:
        cas = ContentStore(tmp_path)
        d = cas.put(b"original")
        path = cas._path(d)
        path.chmod(0o644)
        path.write_bytes(b"tampered")
        with pytest.raises(IntegrityError):
            cas.get(d)
        assert cas.get(d, verify=False) == b"tampered"

    def test_missing_and_malformed(self, tmp_path: Path) -> None:
        cas = ContentStore(tmp_path)
        with pytest.raises(NotFoundError):
            cas.get("0" * 64)
        with pytest.raises(IntegrityError):
            cas.exists("../../etc/passwd")

    def test_algo_prefix_accepted(self, tmp_path: Path) -> None:
        cas = ContentStore(tmp_path)
        d = cas.put(b"abc")
        assert cas.get(f"sha256:{d}") == b"abc"


class TestPayload:
    def test_deterministic(self) -> None:
        a = pack_files({"b.txt": b"2", "a.txt": b"1"})
        b = pack_files({"a.txt": b"1", "b.txt": b"2"})
        assert a == b

    def test_roundtrip_and_listing(self) -> None:
        data = pack_files({"a/b.txt": b"hi"})
        assert unpack_files(data) == {"a/b.txt": b"hi"}
        (entry,) = list_payload(data)
        assert entry.path == "a/b.txt" and entry.size == 2

    @pytest.mark.parametrize("bad", ["/abs", "../x", "a/../../x", "a\\b", "", "."])
    def test_unsafe_paths_rejected(self, bad: str) -> None:
        with pytest.raises(PayloadError):
            safe_relpath(bad)
        with pytest.raises(PayloadError):
            pack_files({bad: b"x"})

    def test_extract_safe(self, tmp_path: Path) -> None:
        data = pack_files({"d/f.txt": b"z"})
        written = extract_to(data, tmp_path / "out")
        assert written == ["d/f.txt"] and (tmp_path / "out/d/f.txt").read_bytes() == b"z"

    def test_malicious_archive_rejected(self, tmp_path: Path) -> None:
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(PayloadError):
            unpack_files(buf.getvalue())
        buf2 = io.BytesIO()
        with tarfile.open(fileobj=buf2, mode="w") as tar:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
        with pytest.raises(PayloadError):
            unpack_files(buf2.getvalue())
        assert not (tmp_path / "evil.txt").exists()

    def test_corrupt_archive(self) -> None:
        with pytest.raises(PayloadError):
            unpack_files(b"not a tar")

    def test_collect_directory_skips_symlinks_and_junk(self, tmp_path: Path) -> None:
        src = tmp_path / "s"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("a")
        (src / "sub" / "b.txt").write_text("b")
        (src / ".git").mkdir()
        (src / ".git" / "config").write_text("x")
        (src / "x.pyc").write_bytes(b"\0")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (src / "link.txt").symlink_to(outside)
        (src / "dirlink").symlink_to(tmp_path)
        report = collect_directory(src)
        assert sorted(report.files) == ["a.txt", "sub/b.txt"]
        assert "link.txt" in report.skipped_symlinks and "dirlink" in report.skipped_symlinks


class TestSecrets:
    def test_detects_common_secrets_without_leaking(self) -> None:
        text = "aws=AKIAABCDEFGHIJKLMNOP\napi_key = 'abcdEFGH12345678wxyz'\n-----BEGIN RSA PRIVATE KEY-----\n"
        findings = scan_files({"cfg.txt": text.encode()})
        assert {f.rule for f in findings} >= {"aws-access-key", "generic-credential", "private-key"}
        assert all("AKIAABCDEFGHIJKLMNOP" not in f.excerpt for f in findings)

    def test_placeholders_ignored(self) -> None:
        assert not scan_files(
            {
                "a": b"api_key = ${API_KEY}\npassword = changeme-please-change\ntoken = <your-token-here>"
            }
        )

    def test_binary_files_skipped(self) -> None:
        assert not scan_files({"a.bin": b"\x00\x01AKIAABCDEFGHIJKLMNOP"})

    def test_redact_replaces_whole_private_key(self) -> None:
        body = b"x\n-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----\ny"
        out, n = redact_files({"k": body})
        assert n >= 1 and b"MIIabc" not in out["k"] and b"[REDACTED]" in out["k"]


# --------------------------------------------------------------------------- store


class TestStore:
    def test_requires_init(self, tmp_path: Path) -> None:
        with pytest.raises(NotInitializedError):
            RegistryStore(tmp_path / "nope" / "registry.db").open()

    def test_pragmas_and_migrations(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        assert reg.store.journal_mode() == "wal"
        assert reg.store.schema_version() == SUPPORTED_SCHEMA
        assert reg.store.fts_available
        assert reg.store.integrity_check() == []
        assert reg.store.foreign_key_violations() == []
        reg.close()
        reopened = Registry.open(tmp_path / "registry")
        assert reopened.store.schema_version() == SUPPORTED_SCHEMA

    def test_newer_schema_refused(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        reg.store.conn.execute("INSERT INTO schema_migrations VALUES (99, 'x', 'future')")
        reg.close()
        from ananke.plexus.registry.errors import SchemaVersionError

        with pytest.raises(SchemaVersionError):
            Registry.open(tmp_path / "registry")

    def test_payload_identity_columns_are_immutable(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        for column in ("payload_sha256", "manifest_json", "version"):
            with pytest.raises(sqlite3.DatabaseError, match="VERSION_IMMUTABLE"):
                reg.store.conn.execute(f"UPDATE artifact_versions SET {column}='x'")

    def test_event_log_is_append_only(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            reg.store.conn.execute("DELETE FROM registry_events")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            reg.store.conn.execute("UPDATE registry_events SET actor='x'")

    def test_transaction_rolls_back(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        with pytest.raises(RuntimeError), reg.store.transaction():
            reg.store.append_event("x.y", None, "t", {})
            raise RuntimeError("boom")
        assert reg.store.list_events() == []

    def test_concurrent_writers(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        root = tmp_path / "registry"
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                r = Registry.open(root, actor=f"w{i}")
                for j in range(5):
                    r.register(artifact(f"skill-{i}", f"1.{j}.0"))
                r.close()
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert reg.store.count_versions() == 20
        assert reg.store.integrity_check() == []


# --------------------------------------------------------------------------- registration


class TestRegistration:
    def test_register_and_fetch(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        res = reg.register(
            artifact(
                capabilities=["graph.query", "architecture.read"],
                metadata={"tags": ["Graph", "review"]},
            )
        )
        assert res.created and res.uri == "ananke://skill/core/graph-review"
        rec = reg.exact_version("core/graph-review@1.0.0")
        assert rec.trust is TrustStatus.DISCOVERED and rec.channel == "candidate"
        assert rec.lifecycle is LifecycleStatus.ACTIVE and rec.license.approval == "approved"
        assert rec.manifest.metadata.tags == ["graph", "review"]
        assert rec.digest.startswith("sha256:") and rec.payload_size > 0
        assert MANIFEST_FILE in reg.files(rec)

    def test_idempotent_same_content(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        first = reg.register(artifact())
        again = reg.register(artifact())
        assert again.already_registered and not again.created and again.digest == first.digest
        assert reg.store.count_versions() == 1

    def test_same_version_different_content_conflicts(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        reg.register(artifact())
        with pytest.raises(VersionContentConflictError, match="VERSION_CONTENT_CONFLICT"):
            reg.register(artifact(files={"README.md": b"changed"}))
        assert reg.store.count_versions() == 1

    def test_multiple_immutable_versions(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        for v in ("1.0.0", "2.0.0", "1.5.0"):
            reg.register(artifact(version=v, files={"README.md": v.encode()}))
        assert [r.version for r in reg.versions("core/graph-review")] == ["1.0.0", "1.5.0", "2.0.0"]

    def test_version_is_normalized(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        res = reg.register(artifact(version="1.2"))
        assert res.version == "1.2.0"
        rec = reg.exact_version("core/graph-review@1.2.0")
        assert rec.provenance.native_version == "1.2"

    def test_strict_semver_policy(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path, RegistryPolicy(strict_semver=True))
        from ananke.plexus.registry.errors import InvalidVersionError

        with pytest.raises(InvalidVersionError):
            reg.register(artifact(version="1.2"))

    def test_auto_version_bumps_from_diff(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        reg.register(artifact(version="1.0.0"))
        patched = reg.register(artifact(files={"README.md": b"docs fix"}), version="auto")
        assert patched.version == "1.0.1" and patched.suggested_bump == "PATCH"
        minor = reg.register(
            artifact(capabilities=["graph.query", "graph.write"], files={"README.md": b"docs fix"}),
            version="auto",
        )
        assert minor.version == "1.1.0" and minor.suggested_bump == "MINOR"
        major = reg.register(
            artifact(
                capabilities=["graph.query", "graph.write"],
                permissions={"network": True},
                files={"README.md": b"docs fix"},
            ),
            version="auto",
        )
        assert major.version == "2.0.0" and major.suggested_bump == "MAJOR"
        same = reg.register(
            artifact(
                capabilities=["graph.query", "graph.write"],
                permissions={"network": True},
                files={"README.md": b"docs fix"},
            ),
            version="auto",
        )
        assert same.already_registered and same.version == "2.0.0"

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        res = reg.register(artifact(), dry_run=True)
        assert res.dry_run and not res.created
        assert reg.store.count_versions() == 0 and list(reg.cas.iter_digests()) == []

    def test_cannot_register_directly_as_approved(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        with pytest.raises(PolicyViolationError):
            reg.register(artifact(), trust=TrustStatus.APPROVED)

    def test_events_recorded_and_published(self, tmp_path: Path) -> None:
        from ananke.plexus.events.bus import EventBus

        bus = EventBus()
        seen: list[str] = []
        bus.subscribe_all(lambda e: seen.append(e.event_type))
        reg = open_registry(tmp_path, bus=bus)
        add(reg)
        assert [e["event_type"] for e in reg.store.list_events()] == ["artifact.registered"]
        assert seen == ["registry.version.registered"]

    def test_secrets_rejected_by_default(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        with pytest.raises(SecretDetectedError):
            reg.register(artifact(files={"cfg.env": b"AWS_KEY=AKIAABCDEFGHIJKLMNOP"}))
        assert reg.store.count_versions() == 0

    def test_secret_redaction_mode(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path, RegistryPolicy(secrets=SecretsPolicy(mode="redact")))
        res = reg.register(artifact(files={"cfg.env": b"AWS_KEY=AKIAABCDEFGHIJKLMNOP"}))
        assert any(d.code == "secrets-redacted" for d in res.diagnostics)
        rec = reg.exact_version("core/graph-review@1.0.0")
        assert b"AKIA" not in reg.read_file(rec, "cfg.env")

    def test_license_policy(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.policy import LicensePolicy

        reg = open_registry(
            tmp_path, RegistryPolicy(licenses=LicensePolicy(allow=["MIT"], allow_unknown=False))
        )
        with pytest.raises(PolicyViolationError, match="LICENSE_DENIED"):
            reg.register(artifact())  # Apache-2.0 not on allow list
        with pytest.raises(PolicyViolationError, match="LICENSE_REQUIRED"):
            reg.register(artifact(license={}))
        ok = reg.register(artifact(license={"expression": "MIT"}))
        assert ok.created

    def test_forbidden_permission(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.policy import PermissionPolicy

        reg = open_registry(
            tmp_path, RegistryPolicy(permissions=PermissionPolicy(forbid=["network.http"]))
        )
        with pytest.raises(PolicyViolationError):
            reg.register(artifact(permissions={"network": ["example.com"]}))

    def test_namespace_governance(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.policy import NamespacePolicy

        policy = RegistryPolicy(namespaces={"core": NamespacePolicy(publishers=["admin"])})
        reg = open_registry(tmp_path, policy)
        with pytest.raises(PolicyViolationError, match="NAMESPACE_PROTECTED"):
            reg.register(artifact())
        reg.actor = "admin"
        assert reg.register(artifact()).created
        assert reg.register(artifact("other", namespace="team")).created

    def test_dependency_cycle_rejected(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        reg.register(artifact("a", "1.0.0", dependencies=[{"skill": "core/b", "version": "^1"}]))
        with pytest.raises(PolicyViolationError, match="DEPENDENCY_CYCLE"):
            reg.register(
                artifact("b", "1.0.0", dependencies=[{"skill": "core/a", "version": "^1"}])
            )
        with pytest.raises(PolicyViolationError, match="DEPENDENCY_CYCLE"):
            reg.register(
                artifact("self", "1.0.0", dependencies=[{"skill": "core/self", "version": "*"}])
            )

    def test_invalid_json_schema_rejected(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.errors import ManifestError

        reg = open_registry(tmp_path)
        with pytest.raises(ManifestError):
            reg.register(artifact(inputs={"json_schema": {"type": "nonsense"}}))

    def test_capability_without_permission_warns(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        res = reg.register(artifact(capabilities=["filesystem.write"]))
        assert any(d.code == "capability-without-permission" for d in res.diagnostics)

    def test_error_diagnostics_block_registration(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.errors import ManifestError
        from ananke.plexus.registry.models import Diagnostic

        reg = open_registry(tmp_path)
        imp = artifact()
        imp.diagnostics.append(Diagnostic(level="error", code="bad", message="broken"))
        with pytest.raises(ManifestError):
            reg.register(imp)

    def test_schemas_files_and_owners_indexed(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        reg.register(
            artifact(
                inputs={"json_schema": schema},
                owners=[{"team": "platform"}, {"user": "alice"}],
                maintainers=["bob"],
            )
        )
        rec = reg.exact_version("core/graph-review@1.0.0")
        vid = reg.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
        assert reg.store.schemas_of(vid)["input"] == schema
        assert {f.path for f in reg.store.files_of(vid)} >= {"README.md", MANIFEST_FILE}
        assert reg.store.owners_of("skill", "core", "graph-review") == [
            ("maintainer", "bob"),
            ("owner", "alice"),
            ("owner", "platform"),
        ]


# --------------------------------------------------------------------------- lookup


class TestLookup:
    def test_alias_and_bare_name(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        assert reg.locate("graph-review") == (ArtifactKind.SKILL, "core", "graph-review")
        reg.alias_set("gr", "core/graph-review")
        assert reg.locate("gr") == (ArtifactKind.SKILL, "core", "graph-review")
        assert reg.aliases()[0]["alias"] == "gr"
        assert reg.alias_remove("gr") and not reg.alias_remove("gr")
        with pytest.raises(InvalidIdentityError):
            reg.alias_set("a/b", "core/graph-review")

    def test_ambiguous_bare_name(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, namespace="core")
        add(reg, namespace="team")
        with pytest.raises(InvalidIdentityError, match="ambiguous"):
            reg.locate("graph-review")
        assert reg.locate("team/graph-review")[1] == "team"

    def test_same_name_different_kinds(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "thing", kind="skill")
        add(reg, "thing", kind="agent")
        with pytest.raises(InvalidIdentityError, match="multiple kinds"):
            reg.locate("core/thing")
        assert reg.locate("core/thing", "agent")[0] is ArtifactKind.AGENT

    def test_exact_version_requires_pin(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        with pytest.raises(InvalidRequirementError):
            reg.exact_version("core/graph-review")
        with pytest.raises(InvalidRequirementError):
            reg.exact_version("core/graph-review@^1")
        with pytest.raises(NotFoundError):
            reg.exact_version("core/graph-review@9.9.9")
        with pytest.raises(NotFoundError):
            reg.exact_version("core/missing@1.0.0")

    def test_list_filters(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "a", kind="skill")
        add(reg, "b", kind="agent")
        assert [a.name for a in reg.list_artifacts("agent")] == ["b"]
        assert len(reg.list_versions()) == 2


# --------------------------------------------------------------------------- lifecycle


class TestLifecycle:
    def test_yank_and_unyank(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        (rec,) = reg.yank(ref, "bad build")
        assert rec.lifecycle is LifecycleStatus.YANKED and rec.lifecycle_message == "bad build"
        assert reg.unyank(ref)[0].lifecycle is LifecycleStatus.ACTIVE
        with pytest.raises(InvalidRequirementError):
            reg.yank("core/graph-review")  # a selector is mandatory

    def test_deprecate_whole_artifact(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")
        recs = reg.deprecate("core/graph-review", "use v2", replacement="core/graph-review-v2")
        assert len(recs) == 2 and all(r.lifecycle is LifecycleStatus.DEPRECATED for r in recs)
        assert recs[0].replacement == "core/graph-review-v2" and recs[0].channel == "deprecated"

    def test_quarantine_is_strongest(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        (rec,) = reg.quarantine(ref, "malicious")
        assert rec.lifecycle is LifecycleStatus.QUARANTINED and rec.trust is TrustStatus.QUARANTINED
        with pytest.raises(PolicyViolationError):
            reg.yank(ref)
        with pytest.raises(PolicyViolationError):
            reg.set_channel(ref, "stable")
        with pytest.raises(PolicyViolationError, match="INVALID_TRUST_TRANSITION"):
            reg.set_trust(ref, TrustStatus.APPROVED)
        (released,) = reg.release_quarantine(ref, "false positive")
        assert (
            released.trust is TrustStatus.RESTRICTED
            and released.lifecycle is LifecycleStatus.ACTIVE
        )

    def test_trust_transition_rules(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        with pytest.raises(PolicyViolationError, match="INVALID_TRUST_TRANSITION"):
            reg.set_trust(ref, TrustStatus.APPROVED)  # must be verified first
        assert reg.set_trust(ref, TrustStatus.VERIFIED).trust is TrustStatus.VERIFIED
        assert reg.set_trust(ref, TrustStatus.APPROVED).trust is TrustStatus.APPROVED

    def test_promotion_keeps_version_and_records_revisions(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        before = reg.exact_version(ref)
        after = reg.promote(
            ref, trust=TrustStatus.APPROVED, channel="stable", run_gate=False, reason="ship it"
        )
        assert after.version == before.version and after.digest == before.digest
        assert after.trust is TrustStatus.APPROVED and after.channel == "stable"
        assert after.revision > before.revision
        revs = reg.revisions(ref)
        assert [r["revision"] for r in revs] == sorted(r["revision"] for r in revs)
        vid = reg.store.version_id(after.kind, after.namespace, after.name, after.version)
        assert [t["status"] for t in reg.store.trust_history(vid)] == [
            "discovered",
            "verified",
            "approved",
        ]
        assert [c["channel"] for c in reg.store.channel_history(vid)] == ["candidate", "stable"]

    def test_metadata_correction_is_a_revision_not_a_new_payload(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        ref = add(reg)
        before = reg.exact_version(ref)
        after = reg.correct_metadata(ref, summary="fixed typo")
        assert (
            after.summary == "fixed typo" and after.digest == before.digest and after.revision == 1
        )
        assert after.manifest.summary == before.manifest.summary  # payload manifest untouched

    def test_unregister_archives_then_purge_guards(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        add(reg, "app", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^1"}])
        reg.unregister("core/lib@1.0.0")
        assert reg.exact_version("core/lib@1.0.0").lifecycle is LifecycleStatus.ARCHIVED
        with pytest.raises(PolicyViolationError, match="depended on"):
            reg.unregister("core/lib@1.0.0", purge=True)
        reg.unregister("core/lib@1.0.0", purge=True, force=True)
        with pytest.raises(NotFoundError):
            reg.exact_version("core/lib@1.0.0")

    def test_snapshot_changes_with_state(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        empty = reg.snapshot_id()
        ref = add(reg)
        s1 = reg.snapshot_id()
        reg.yank(ref)
        assert len({empty, s1, reg.snapshot_id()}) == 3
        assert reg.snapshot_id() == reg.snapshot_id()
        assert reg.create_snapshot("note") == reg.snapshot_id()
        assert reg.store.list_snapshots()[0]["note"] == "note"

    def test_diff_between_versions(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(
            reg,
            version="1.1.0",
            capabilities=["graph.query", "graph.write"],
            files={"README.md": b"new"},
        )
        d = reg.diff("core/graph-review@1.0.0", "core/graph-review@1.1.0")
        assert d.capabilities_added == ["graph.write"] and d.suggested_bump == "MINOR"
        assert any(f.path == "README.md" for f in d.files)

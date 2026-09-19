"""Artifact signatures: Ed25519 verification, trust model, resolver/gate integration, portability."""

from __future__ import annotations

import base64
import os
import random
from pathlib import Path

import pytest
from registry_support import add, artifact, open_registry
from typer.testing import CliRunner

from ananke.plexus.registry import ed25519
from ananke.plexus.registry.cli import registry_app
from ananke.plexus.registry.errors import PolicyViolationError, RegistryError
from ananke.plexus.registry.models import Provenance, SignatureInfo, TrustStatus
from ananke.plexus.registry.policy import RegistryPolicy, SigningPolicy, TrustedKey
from ananke.plexus.registry.portable import export_registry, import_registry
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.resolver import Requirement, Resolver
from ananke.plexus.registry.signing import (
    SignatureInvalidError,
    check_signature,
    key_id_for,
    signing_message,
    validate_key_id,
)

# RFC 8032 §7.1 test vectors (TEST 1, TEST 2)
RFC_VECTORS = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


class TestPureEd25519:
    @pytest.mark.parametrize("pub,msg,sig", RFC_VECTORS)
    def test_rfc8032_vectors(self, pub: str, msg: str, sig: str) -> None:
        assert ed25519.verify_pure(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))

    def test_rejects_tampering_and_malformed(self) -> None:
        pub, msg, sig = (bytes.fromhex(x) for x in RFC_VECTORS[1])
        assert not ed25519.verify_pure(pub, msg + b"x", sig)
        flipped = bytes([sig[0] ^ 1]) + sig[1:]
        assert not ed25519.verify_pure(pub, msg, flipped)
        assert not ed25519.verify_pure(pub, msg, sig[:-1])
        assert not ed25519.verify_pure(pub[:-1], msg, sig)
        assert not ed25519.verify_pure(b"\xff" * 32, msg, sig)  # non-canonical / off-curve
        # S >= L must be rejected (malleability)
        s_bad = (int.from_bytes(sig[32:], "little") + ed25519._Q).to_bytes(32, "little")
        assert not ed25519.verify_pure(pub, msg, sig[:32] + s_bad)

    def test_agrees_with_cryptography_on_random_and_tampered_inputs(self) -> None:
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        rng = random.Random(8032)
        for _ in range(25):
            key = Ed25519PrivateKey.from_private_bytes(rng.randbytes(32))
            pub = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            msg = rng.randbytes(rng.randrange(0, 200))
            sig = key.sign(msg)
            assert ed25519.verify_pure(pub, msg, sig)
            bad = bytearray(sig)
            bad[rng.randrange(64)] ^= 1 << rng.randrange(8)
            assert ed25519.verify_pure(pub, msg, bytes(bad)) is ed25519.verify(pub, msg, bytes(bad))
            assert not ed25519.verify_pure(pub, msg + b"!", sig)


class TestMessageAndKeys:
    def test_message_binds_uri_and_digest(self) -> None:
        a = signing_message("ananke://skill/core/x@1.0.0", "ab" * 32)
        assert a == signing_message("ananke://skill/core/x@1.0.0", "sha256:" + "ab" * 32)
        assert a != signing_message("ananke://skill/core/x@1.0.1", "ab" * 32)
        assert a != signing_message("ananke://skill/core/x@1.0.0", "cd" * 32)

    def test_key_ids(self) -> None:
        assert key_id_for(b"\x01" * 32).startswith("ed25519-")
        for bad in ["", "a b", "-x", "x" * 65, "a/b"]:
            with pytest.raises(RegistryError):
                validate_key_id(bad)

    def test_untrusted_and_unsupported(self) -> None:
        st = check_signature(
            SigningPolicy(),
            version_uri="ananke://skill/core/x@1.0.0",
            sha256_hex="00" * 32,
            key_id="k",
            signature_b64="AAAA",
        )
        assert not st.verified and not st.trusted and st.valid is None
        st = check_signature(
            SigningPolicy(),
            version_uri="u",
            sha256_hex="00",
            key_id="k",
            signature_b64="x",
            algorithm="rsa",
        )
        assert "unsupported" in st.reason

    def test_garbage_signature_from_trusted_key_is_invalid_not_a_crash(self) -> None:
        pol = SigningPolicy(
            trusted_keys={"k": TrustedKey(public_key=base64.b64encode(b"\x01" * 32).decode())}
        )
        st = check_signature(
            pol, version_uri="u", sha256_hex="00", key_id="k", signature_b64="!!not-base64!!"
        )
        assert st.trusted and st.valid is False and not st.verified


@pytest.fixture
def crypto() -> None:
    pytest.importorskip("cryptography")


@pytest.fixture
def keypair(tmp_path: Path, crypto: None) -> tuple[Path, str, str]:
    from ananke.plexus.registry.signing import generate_keypair

    path = tmp_path / "keys" / "signing.key"
    key_id, public = generate_keypair(path)
    return path, key_id, public


def _signed_registry(tmp_path: Path, keypair: tuple[Path, str, str], **policy: object) -> Registry:
    _path, key_id, public = keypair
    pol = RegistryPolicy(
        signing=SigningPolicy(trusted_keys={key_id: TrustedKey(public_key=public, signer="ci")}),
        **policy,  # type: ignore[arg-type]
    )
    return open_registry(tmp_path, pol)


class TestRegistrySigning:
    def test_sign_and_verify(self, tmp_path: Path, keypair: tuple[Path, str, str]) -> None:
        reg = _signed_registry(tmp_path, keypair)
        ref = add(reg)
        assert not reg.is_signed(reg.exact_version(ref))
        st = reg.sign(ref, keypair[0], signer="release-bot")
        assert st.verified and st.key_id == keypair[1]
        assert reg.is_signed(reg.exact_version(ref))
        assert [e for e in reg.store.list_events() if e["event_type"] == "artifact.signed"]

    def test_key_file_must_be_private(self, tmp_path: Path, keypair: tuple[Path, str, str]) -> None:
        reg = _signed_registry(tmp_path, keypair)
        ref = add(reg)
        os.chmod(keypair[0], 0o644)
        with pytest.raises(PolicyViolationError, match="chmod 600"):
            reg.sign(ref, keypair[0])
        os.chmod(keypair[0], 0o600)
        assert reg.sign(ref, keypair[0]).verified

    def test_generated_key_is_owner_only(self, keypair: tuple[Path, str, str]) -> None:
        assert (keypair[0].stat().st_mode & 0o777) == 0o600

    def test_signature_cannot_be_replayed_onto_another_version(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = _signed_registry(tmp_path, keypair)
        a, b = add(reg, version="1.0.0"), add(reg, version="1.0.1")
        sig = reg.sign(a, keypair[0])
        stored = reg.store.signatures_of(
            reg.store.version_id("skill", "core", "graph-review", "1.0.0")
        )
        with pytest.raises(SignatureInvalidError):
            reg.add_signature(b, key_id=sig.key_id, signature=stored[0]["signature"])
        assert not reg.is_signed(reg.exact_version(b))

    def test_untrusted_key_is_stored_but_not_verified(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = open_registry(tmp_path)  # policy trusts nobody
        ref = add(reg)
        st = reg.sign(ref, keypair[0])
        assert st.valid is None and not st.trusted and not st.verified
        assert not reg.is_signed(reg.exact_version(ref))

    def test_revoked_key_stops_counting(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = _signed_registry(tmp_path, keypair)
        ref = add(reg)
        reg.sign(ref, keypair[0])
        reg.policy.signing.trusted_keys[keypair[1]].revoked = True
        (st,) = reg.signatures(ref)
        assert st.valid and st.revoked and not st.verified
        assert not reg.is_signed(reg.exact_version(ref))

    def test_self_asserted_verified_is_ignored(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path, RegistryPolicy(require_signature=True))
        art = artifact()
        art.provenance = Provenance.model_validate(
            {"source_type": "test", "signature": {"algorithm": "ed25519", "verified": True}}
        )
        assert art.provenance.signature is not None and not art.provenance.signature.verified
        reg.register(art)
        rec = reg.exact_version("core/graph-review@1.0.0")
        assert not reg.is_signed(rec)

    def test_require_signature_gates_resolution_and_promotion(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = _signed_registry(tmp_path, keypair, require_signature=True)
        ref = add(reg)
        with pytest.raises(RegistryError):
            reg.promote(ref, trust=TrustStatus.APPROVED, channel="stable")
        reg.promote(ref, trust=TrustStatus.APPROVED, channel="stable", run_gate=False)
        res = Resolver(reg).resolve(Requirement.parse("core/graph-review@^1"))
        assert not res.ok
        reg.sign(ref, keypair[0])
        res = Resolver(reg).resolve(Requirement.parse("core/graph-review@^1"))
        assert res.ok
        reg.promote(ref, trust=TrustStatus.APPROVED, channel="stable")  # gate now satisfied

    def test_attached_signature_at_registration(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = _signed_registry(tmp_path, keypair)
        prep = reg.register(artifact(), dry_run=True)
        sig = _sign_offline(keypair[0], "ananke://skill/core/graph-review@1.0.0", prep.digest)
        art = artifact()
        art.provenance = Provenance(
            source_type="test",
            signature=SignatureInfo(algorithm="ed25519", key_id=keypair[1], value=sig),
        )
        reg.register(art)
        assert reg.is_signed(reg.exact_version("core/graph-review@1.0.0"))

    def test_bad_attached_signature_from_trusted_key_is_rejected(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        reg = _signed_registry(tmp_path, keypair)
        art = artifact()
        art.provenance = Provenance(
            source_type="test",
            signature=SignatureInfo(
                algorithm="ed25519",
                key_id=keypair[1],
                value=base64.b64encode(b"\x00" * 64).decode(),
            ),
        )
        with pytest.raises(SignatureInvalidError):
            reg.register(art)
        assert reg.store.count_versions() == 0

    def test_export_import_keeps_signatures_and_recomputes_trust(
        self, tmp_path: Path, keypair: tuple[Path, str, str]
    ) -> None:
        src = _signed_registry(tmp_path / "a", keypair)
        ref = add(src)
        src.sign(ref, keypair[0])
        archive = Path(export_registry(src, tmp_path / "out.tar.gz").path)

        trusting = _signed_registry(tmp_path / "b", keypair)
        import_registry(trusting, archive)
        assert trusting.is_signed(trusting.exact_version(ref))
        assert (
            trusting.semantic_state()["versions"][0]["signatures"]
            == (src.semantic_state()["versions"][0]["signatures"])
        )

        skeptical = open_registry(tmp_path / "c")  # does not trust the key
        result = import_registry(skeptical, archive)
        assert not skeptical.is_signed(skeptical.exact_version(ref))
        assert any("do not verify" in w for w in result.warnings)

    def test_remove_signature(self, tmp_path: Path, keypair: tuple[Path, str, str]) -> None:
        reg = _signed_registry(tmp_path, keypair)
        ref = add(reg)
        reg.sign(ref, keypair[0])
        assert reg.remove_signature(ref, keypair[1]) is True
        assert reg.remove_signature(ref, keypair[1]) is False
        assert reg.signatures(ref) == []

    def test_sign_without_cryptography_gives_actionable_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real = builtins.__import__

        def fake(name: str, *a: object, **k: object) -> object:
            if name.startswith("cryptography"):
                raise ImportError(name)
            return real(name, *a, **k)  # type: ignore[arg-type]

        reg = open_registry(tmp_path)
        ref = add(reg)
        monkeypatch.setattr(builtins, "__import__", fake)
        with pytest.raises(RegistryError, match="registry-signing"):
            reg.sign(ref, tmp_path / "nokey")


def _sign_offline(key: Path, uri: str, digest: str) -> str:
    from ananke.plexus.registry.signing import sign_message

    sig, _ = sign_message(key, signing_message(uri, digest))
    return base64.b64encode(sig).decode()


class TestSigningCli:
    def test_full_flow(self, tmp_path: Path, crypto: None) -> None:
        runner = CliRunner()
        proj = ["--project", str(tmp_path)]
        assert runner.invoke(registry_app, ["init", *proj]).exit_code == 0
        key = tmp_path / ".ananke" / "secrets" / "k.key"
        out = runner.invoke(registry_app, ["key", "generate", "-o", str(key), "--trust", *proj])
        assert out.exit_code == 0, out.output
        assert "Trusted key" in out.output and "never commit" in out.output

        skill = tmp_path / "skills" / "s"
        skill.mkdir(parents=True)
        (skill / "ananke.yaml").write_text(
            "kind: skill\nnamespace: core\nname: s\nversion: 1.0.0\nsummary: s\n"
            "license: {expression: MIT}\ncapabilities: [a.b]\n"
        )
        assert runner.invoke(registry_app, ["learn", str(skill), *proj]).exit_code == 0
        ref = "core/s@1.0.0"

        unsigned = runner.invoke(registry_app, ["signatures", ref, *proj])
        assert unsigned.exit_code == 1  # nothing verified

        signed = runner.invoke(registry_app, ["sign", ref, "--key", str(key), *proj])
        assert signed.exit_code == 0, signed.output
        ok = runner.invoke(registry_app, ["signatures", ref, "--json", *proj])
        assert ok.exit_code == 0 and '"verified": true' in ok.output

        listed = runner.invoke(registry_app, ["key", "list", *proj])
        assert "ed25519-" in listed.output
        key_id = next(t for t in listed.output.split() if t.startswith("ed25519-"))
        assert runner.invoke(registry_app, ["key", "revoke", key_id, *proj]).exit_code == 0
        assert runner.invoke(registry_app, ["signatures", ref, *proj]).exit_code == 1

        again = runner.invoke(registry_app, ["key", "generate", "-o", str(key), *proj])
        assert again.exit_code == 1 and "already exists" in again.output + (again.stderr or "")

"""Artifact signatures (spec §29, §91): Ed25519 over ``version URI + payload digest``.

Trust model
-----------
* A signature is *verified* only if it is cryptographically valid **and** its key is listed in
  the registry policy (``[signing.trusted_keys.<id>]``) and not revoked. A signature whose key the
  policy does not know is stored but reported ``untrusted``.
* ``verified`` is always computed here — never read from a manifest, a portable archive or a
  database column — so importing content cannot assert its own trust.
* The signed message binds the artifact URI, version and the SHA-256 of the immutable payload, so a
  signature cannot be replayed onto different content or another version.
* Private keys are never stored in the registry, policy, evidence or events. Signing reads a key
  file (which must not be group/world readable) and needs ``pip install ananke-plexus[registry-signing]``.
  Verification needs nothing (pure-Python fallback).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ananke.plexus.registry.ed25519 import verify as ed25519_verify
from ananke.plexus.registry.errors import PolicyViolationError, RegistryError
from ananke.plexus.registry.policy import SigningPolicy

ALGORITHM = "ed25519"
_CONTEXT = b"ananke-registry-signature-v1"
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SigningUnavailableError(RegistryError):
    code = "SIGNING_UNAVAILABLE"


class SignatureInvalidError(RegistryError):
    code = "SIGNATURE_INVALID"


class SignatureStatus(BaseModel):
    key_id: str
    algorithm: str = ALGORITHM
    signer: str | None = None
    created_at: str | None = None
    valid: bool | None = None  # None: could not be checked (unknown key)
    trusted: bool = False
    revoked: bool = False
    verified: bool = False
    reason: str = ""


def signing_message(version_uri: str, sha256_hex: str) -> bytes:
    """The exact bytes that are signed. ``version_uri`` is ``ananke://kind/ns/name@version``."""
    digest = sha256_hex.removeprefix("sha256:")
    return _CONTEXT + b"\n" + version_uri.encode("utf-8") + b"\n" + f"sha256:{digest}".encode()


def key_id_for(public_key: bytes) -> str:
    return f"ed25519-{hashlib.sha256(public_key).hexdigest()[:16]}"


def validate_key_id(key_id: str) -> str:
    if not _KEY_ID.match(key_id):
        raise RegistryError(
            f"invalid key id {key_id!r} (letters, digits, '.', '_', '-'; max 64)",
            code="INVALID_KEY_ID",
        )
    return key_id


def _b64d(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureInvalidError(f"{what} is not valid base64") from exc


def decode_public_key(value: str) -> bytes:
    raw = _b64d(value, "public key")
    if len(raw) != 32:
        raise RegistryError("an Ed25519 public key is 32 bytes", code="INVALID_PUBLIC_KEY")
    return raw


def _crypto() -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise SigningUnavailableError(
            "signing needs the 'cryptography' package: pip install ananke-plexus[registry-signing]"
        ) from exc
    return serialization, ed25519


def generate_keypair(private_path: Path, *, overwrite: bool = False) -> tuple[str, str]:
    """Write a new PKCS#8 PEM private key (mode 0600) and return ``(key_id, public_key_b64)``."""
    serialization, ed25519 = _crypto()
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    try:
        fd = os.open(private_path, flags, 0o600)
    except FileExistsError as exc:
        raise RegistryError(
            f"{private_path} already exists (use --force to replace it)", code="KEY_EXISTS"
        ) from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return key_id_for(public), base64.b64encode(public).decode("ascii")


def _load_private(path: Path) -> Any:
    serialization, ed25519 = _crypto()
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise RegistryError(f"cannot read key file {path}: {exc}", code="KEY_UNREADABLE") from exc
    if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PolicyViolationError(
            f"{path} is readable by other users; run `chmod 600 {path}`",
            code="KEY_PERMISSIONS",
        )
    data = path.read_bytes()
    try:
        if data.lstrip().startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(data, password=None)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise RegistryError("key file is not an Ed25519 key", code="KEY_INVALID")
            return key
        seed = data.strip()
        raw = bytes.fromhex(seed.decode()) if len(seed) == 64 else base64.b64decode(seed)
        return ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    except RegistryError:
        raise
    except (ValueError, TypeError, binascii.Error) as exc:
        raise RegistryError(f"cannot parse key file {path}", code="KEY_INVALID") from exc


def sign_message(private_path: Path, message: bytes) -> tuple[bytes, bytes]:
    """Return ``(signature, public_key)``."""
    serialization, _ = _crypto()
    private = _load_private(private_path)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private.sign(message), public


def check_signature(
    policy: SigningPolicy,
    *,
    version_uri: str,
    sha256_hex: str,
    key_id: str,
    signature_b64: str,
    signer: str | None = None,
    created_at: str | None = None,
    algorithm: str = ALGORITHM,
) -> SignatureStatus:
    """Evaluate one signature against the policy's trusted keys."""
    status = SignatureStatus(
        key_id=key_id, algorithm=algorithm, signer=signer, created_at=created_at
    )
    if algorithm != ALGORITHM:
        status.reason = f"unsupported algorithm {algorithm!r}"
        return status
    trusted = policy.trusted_keys.get(key_id)
    if trusted is None:
        status.reason = "key is not trusted by policy"
        return status
    status.trusted = True
    status.signer = signer or trusted.signer
    try:
        public = decode_public_key(trusted.public_key)
        signature = _b64d(signature_b64, "signature")
    except RegistryError as exc:
        status.valid = False
        status.reason = exc.message
        return status
    status.valid = ed25519_verify(public, signing_message(version_uri, sha256_hex), signature)
    if not status.valid:
        status.reason = "signature does not match this artifact version and digest"
        return status
    if trusted.revoked:
        status.revoked = True
        status.reason = "key has been revoked"
        return status
    status.verified = True
    status.reason = "valid signature from a trusted key"
    return status

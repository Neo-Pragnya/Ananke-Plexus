"""Tests for secret resolver (A4) and ID utilities (A1)."""

import pytest

from ananke.plexus.config.secrets import (
    SecretResolutionError,
    redact,
    resolve_secret,
)
from ananke.plexus.core.ids import content_hash, idempotency_key, new_id, run_id, spec_id

# ---------- Secret resolver ----------


def test_resolve_none():
    assert resolve_secret(None) is None


def test_resolve_empty_string():
    assert resolve_secret("") is None


def test_resolve_plain_string():
    assert resolve_secret("mytoken") == "mytoken"


def test_resolve_env_found(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_VAR", "secret_value")
    val = resolve_secret({"env": "TEST_SECRET_VAR"})
    assert val == "secret_value"


def test_resolve_env_missing(monkeypatch):
    monkeypatch.delenv("TEST_SECRET_VAR_MISSING", raising=False)
    val = resolve_secret({"env": "TEST_SECRET_VAR_MISSING"})
    assert val is None


def test_resolve_unknown_keys():
    with pytest.raises(SecretResolutionError, match="unknown secret reference keys"):
        resolve_secret({"vault_path": "/secret/x"})


def test_resolve_unsupported_type():
    with pytest.raises(SecretResolutionError):
        resolve_secret(12345)


def test_resolve_cmd_missing_binary():
    with pytest.raises(SecretResolutionError, match="not found"):
        resolve_secret({"cmd": "__no_such_binary__ read /secret/x"})


def test_redact_empty():
    assert redact(None) == "<empty>"
    assert redact("") == "<empty>"


def test_redact_short():
    assert redact("abc") == "***"


def test_redact_normal():
    result = redact("supersecrettoken")
    assert result.startswith("supe")
    assert "***" in result
    assert "supersecrettoken" not in result


# ---------- ID utilities ----------


def test_new_id_prefix():
    i = new_id("spec")
    assert i.startswith("spec-")
    assert len(i) > 5


def test_run_id_format():
    rid = run_id()
    assert "T" in rid
    assert len(rid) > 16


def test_spec_id_format():
    sid = spec_id("PROJ-101")
    assert "proj-101" in sid


def test_content_hash_deterministic():
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64


def test_content_hash_differs():
    assert content_hash("a") != content_hash("b")


def test_idempotency_key_deterministic():
    k1 = idempotency_key("jira", "PROJ-101", "in_progress")
    k2 = idempotency_key("jira", "PROJ-101", "in_progress")
    assert k1 == k2


def test_idempotency_key_differs():
    k1 = idempotency_key("jira", "PROJ-101")
    k2 = idempotency_key("jira", "PROJ-102")
    assert k1 != k2


# ---- the keychain, through keyring: never a shell-out to a keychain tool
class _Credential:
    def __init__(self, password: str) -> None:
        self.password = password


class _FakeKeyring:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    def get_password(self, service: str, account: str) -> str | None:
        self.asked.append((service, account))
        return " s3cret \n" if account == "me" else None

    def get_credential(self, service: str, account: str | None) -> _Credential | None:
        self.asked.append((service, account))
        return _Credential("t0ken") if service == "ananke-jira" else None


def _with_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    import sys
    import types

    fake = _FakeKeyring()
    module = types.ModuleType("keyring")
    module.get_password = fake.get_password  # type: ignore[attr-defined]
    module.get_credential = fake.get_credential  # type: ignore[attr-defined]
    errors = types.ModuleType("keyring.errors")

    class KeyringError(Exception):
        pass

    errors.KeyringError = KeyringError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    return fake


def test_a_keychain_secret_is_read_through_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    from ananke.plexus.config.secrets import resolve_secret

    fake = _with_keyring(monkeypatch)
    assert resolve_secret({"keychain": "ananke-jira"}) == "t0ken"
    assert resolve_secret({"keychain": "svc", "account": "me"}) == "s3cret"
    assert resolve_secret({"keychain": "missing"}) is None
    assert fake.asked == [("ananke-jira", None), ("svc", "me"), ("missing", None)]


def test_a_keychain_secret_never_runs_a_keychain_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from ananke.plexus.config.secrets import resolve_secret

    _with_keyring(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"ran {a}"))
    resolve_secret({"keychain": "ananke-jira"})


def test_without_keyring_it_says_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from ananke.plexus.config.secrets import SecretResolutionError, resolve_secret

    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(SecretResolutionError, match=r"ananke-plexus\[keychain\]"):
        resolve_secret({"keychain": "anything"})

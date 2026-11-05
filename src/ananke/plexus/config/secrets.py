"""Secret reference resolver (A4).

Secrets live in environment, OS keychain adapter, or enterprise vault.
They are NEVER embedded in config files or trace attributes.

Supported reference forms (TOML syntax):
  token = { env = "ANANKE_JIRA_TOKEN" }
  token = { cmd = "op read op://vault/item/field" }
  token = { keychain = "ananke-jira-token" }
  token = { keychain = "ananke-jira-token", account = "me@example.com" }

The keychain is read through the ``keyring`` library (``pip install 'ananke-plexus[keychain]'``): the macOS Keychain,
Windows Credential Manager or the Secret Service on Linux. Only the entry named is read.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any


class SecretResolutionError(Exception):
    pass


def resolve_secret(reference: Any) -> str | None:
    """Resolve a secret reference dict to its plaintext value.

    Returns ``None`` if reference is None or empty.
    Raises ``SecretResolutionError`` on unresolvable references.
    """
    if reference is None:
        return None
    if isinstance(reference, str):
        return reference or None

    if not isinstance(reference, dict):
        raise SecretResolutionError(f"unsupported secret reference type: {type(reference)}")

    if "env" in reference:
        return _from_env(str(reference["env"]))
    if "cmd" in reference:
        return _from_cmd(str(reference["cmd"]))
    if "keychain" in reference:
        account = reference.get("account")
        return _from_keychain(str(reference["keychain"]), str(account) if account else None)

    raise SecretResolutionError(f"unknown secret reference keys: {list(reference.keys())}")


def _from_env(var: str) -> str | None:
    import os

    value = os.environ.get(var)
    if value is None:
        return None
    return value.strip() or None


def _from_cmd(command: str) -> str | None:
    parts = command.split()
    if not parts:
        return None
    binary = shutil.which(parts[0])
    if not binary:
        raise SecretResolutionError(f"secret command binary not found: {parts[0]}")
    try:
        result = subprocess.run(  # noqa: S603
            [binary, *parts[1:]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise SecretResolutionError(f"secret command failed: {exc}") from exc
    if result.returncode != 0:
        raise SecretResolutionError(
            f"secret command exited {result.returncode}: {result.stderr.strip()[:200]}"
        )
    value = result.stdout.strip()
    return value or None


def _from_keychain(service: str, account: str | None = None) -> str | None:
    """The one entry named, from the OS keychain, through ``keyring``: the standard way, on every platform."""
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise SecretResolutionError(
            "reading the OS keychain needs the keyring package: pip install 'ananke-plexus[keychain]'"
        ) from exc
    try:
        if account:
            value = keyring.get_password(service, account)
        else:
            credential = keyring.get_credential(service, None)
            value = credential.password if credential else None
    except KeyringError as exc:
        raise SecretResolutionError(
            f"the keychain entry '{service}' couldn't be read: {exc}"
        ) from exc
    return (value or "").strip() or None


def redact(value: str | None, *, visible_chars: int = 4) -> str:
    """Return a safely redacted view of a secret for display/logs."""
    if not value:
        return "<empty>"
    if len(value) <= visible_chars:
        return "*" * len(value)
    return value[:visible_chars] + "***"

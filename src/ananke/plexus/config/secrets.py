"""Secret reference resolver (A4).

Secrets live in environment, OS keychain adapter, or enterprise vault.
They are NEVER embedded in config files or trace attributes.

Supported reference forms (TOML syntax):
  token = { env = "ANANKE_JIRA_TOKEN" }
  token = { cmd = "op read op://vault/item/field" }
  token = { keychain = "ananke-jira-token" }
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
        return _from_keychain(str(reference["keychain"]))

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


def _from_keychain(service: str) -> str | None:
    if shutil.which("security"):
        return _from_macos_keychain(service)
    if shutil.which("secret-tool"):
        return _from_secrettool(service)
    raise SecretResolutionError(
        f"no keychain tool found for secret '{service}' (tried: security, secret-tool)"
    )


def _from_macos_keychain(service: str) -> str | None:
    result = subprocess.run(  # noqa: S603
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _from_secrettool(service: str) -> str | None:
    result = subprocess.run(  # noqa: S603
        ["secret-tool", "lookup", "service", service],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def redact(value: str | None, *, visible_chars: int = 4) -> str:
    """Return a safely redacted view of a secret for display/logs."""
    if not value:
        return "<empty>"
    if len(value) <= visible_chars:
        return "*" * len(value)
    return value[:visible_chars] + "***"

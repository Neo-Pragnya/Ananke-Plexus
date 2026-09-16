"""APM permission sandbox enforcement (I5/I6).

Untrusted skills cannot:
- gain network access implicitly
- invoke arbitrary shell patterns
- write outside approved paths
- read secret configuration
- mutate git
- transition lifecycle objects
"""

from __future__ import annotations

import fnmatch

from ananke.plexus.apm.manifest import SkillManifest

_BLOCKED_SHELL_PATTERNS = [
    "git *",
    "gh *",
    "curl *",
    "wget *",
    "ssh *",
    "scp *",
    "nc *",
    "ncat *",
    "python -c*",
    "python3 -c*",
    "exec *",
    "eval *",
    "rm -rf*",
]

_BLOCKED_WRITE_PATHS = [
    ".ananke/secrets/**",
    ".ananke/config.toml",
    ".ananke/config.local.toml",
    ".git/**",
]


class SandboxViolation(Exception):
    pass


def check_shell(manifest: SkillManifest, command: str) -> tuple[bool, str]:
    for blocked in _BLOCKED_SHELL_PATTERNS:
        if fnmatch.fnmatch(command, blocked):
            return False, f"blocked shell pattern: {blocked}"
    allowed = manifest.permissions.shell
    if not allowed:
        return False, "no shell permissions declared"
    for pattern in allowed:
        safe = pattern.rstrip("*") + "*"
        if fnmatch.fnmatch(command, safe) or command.startswith(pattern.rstrip("*")):
            return True, "allowed"
    return False, f"command not in allowed shell patterns: {command!r}"


def check_network(manifest: SkillManifest, host: str) -> tuple[bool, str]:
    if not manifest.permissions.network:
        return False, "network access denied"
    return (
        (True, "allowed")
        if host in manifest.permissions.network
        else (False, f"host not allowed: {host!r}")
    )


def check_write(manifest: SkillManifest, path: str) -> tuple[bool, str]:
    for blocked in _BLOCKED_WRITE_PATHS:
        if fnmatch.fnmatch(path, blocked):
            return False, f"write blocked: {path!r}"
    allowed_patterns = manifest.permissions.filesystem_write
    if not allowed_patterns:
        return False, "no filesystem_write permissions declared"
    for pattern in allowed_patterns:
        if fnmatch.fnmatch(path, pattern):
            return True, "allowed"
    return False, f"path not in allowed write patterns: {path!r}"


def check_read(manifest: SkillManifest, path: str) -> tuple[bool, str]:
    if ".ananke/secrets" in path or "config.local.toml" in path:
        return False, f"read blocked: secret path {path!r}"
    allowed_patterns = manifest.permissions.filesystem_read
    if not allowed_patterns:
        return False, "no filesystem_read permissions declared"
    for pattern in allowed_patterns:
        if fnmatch.fnmatch(path, pattern):
            return True, "allowed"
    return False, f"path not in allowed read patterns: {path!r}"


def assert_shell(manifest: SkillManifest, command: str) -> None:
    ok, reason = check_shell(manifest, command)
    if not ok:
        raise SandboxViolation(f"Shell sandbox violation: {reason}")


def assert_write(manifest: SkillManifest, path: str) -> None:
    ok, reason = check_write(manifest, path)
    if not ok:
        raise SandboxViolation(f"Write sandbox violation: {reason}")


# Legacy compat
def permission_allows_shell(manifest: SkillManifest, command: str) -> bool:
    ok, _ = check_shell(manifest, command)
    return ok


def permission_allows_network(manifest: SkillManifest, host: str) -> bool:
    ok, _ = check_network(manifest, host)
    return ok


def permission_allows_write(manifest: SkillManifest, path: str) -> bool:
    ok, _ = check_write(manifest, path)
    return ok

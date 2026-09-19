"""Dynamic introspection in an isolated subprocess (spec §36, §128, §129).

Some frameworks expose metadata only by importing/executing code. That is allowed *only*
when policy (or an explicit flag) enables it, and runs in a separate interpreter with:

* no network (socket connect/DNS are patched to raise),
* a scrubbed environment and a throw-away ``HOME``/working directory,
* CPU / memory / file-size limits (POSIX ``resource``) and a wall-clock timeout,
* a JSON-only output contract with a size cap — no Python object crosses the boundary.

This is defence in depth, **not** a security boundary equal to a container or VM. Treat
untrusted packages accordingly (run the registry inside an OS-level sandbox for those).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ananke.plexus.registry.errors import DynamicIntrospectionError
from ananke.plexus.registry.policy import DynamicIntrospectionPolicy

_RUNNER = r"""
import json, os, sys

def _harden(mem_mb, cpu_s):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        limit = mem_mb * 1024 * 1024
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            if hasattr(resource, name):
                try:
                    resource.setrlimit(getattr(resource, name), (limit, limit))
                except (ValueError, OSError):
                    pass
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except Exception:
        pass
    import socket
    def _blocked(*a, **k):
        raise OSError("network access is disabled during registry introspection")
    for attr in ("connect", "connect_ex", "sendto"):
        setattr(socket.socket, attr, _blocked)
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    socket.gethostbyname = _blocked

def main():
    request = json.loads(sys.stdin.read())
    _harden(int(request.get("max_memory_mb", 512)), int(request.get("timeout", 20)) + 5)
    for entry in request.get("sys_path", []):
        sys.path.insert(0, entry)
    spec = request["plugin"]
    module_name, _, func_name = spec.partition(":")
    import importlib
    func = getattr(importlib.import_module(module_name), func_name or "introspect")
    out = func({"source": request["source"], "framework": request["framework"]})
    if not isinstance(out, dict) or not isinstance(out.get("artifacts"), list):
        raise SystemExit("introspector must return {'artifacts': [...]}")
    sys.stdout.write(json.dumps(out))

main()
"""


def run_introspection(
    *,
    plugin: str,
    source: str,
    framework: str,
    sys_path: list[str],
    policy: DynamicIntrospectionPolicy,
) -> list[dict[str, Any]]:
    """Run ``plugin`` (``module:function``) in a sandboxed child; return its artifact dicts."""
    if ":" not in plugin and "." not in plugin:
        raise DynamicIntrospectionError(f"plugin must be 'module:function', got {plugin!r}")
    request = {
        "plugin": plugin,
        "source": source,
        "framework": framework,
        "sys_path": [str(Path(p).resolve()) for p in sys_path],
        "max_memory_mb": policy.max_memory_mb,
        "timeout": policy.timeout_seconds,
    }
    with tempfile.TemporaryDirectory(prefix="ananke-introspect-") as tmp:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tmp,
            "TMPDIR": tmp,
            "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False, scrubbed env
                [sys.executable, "-I", "-c", _RUNNER],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                cwd=tmp,
                env=env,
                timeout=policy.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DynamicIntrospectionError(
                f"introspection timed out after {policy.timeout_seconds}s"
            ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise DynamicIntrospectionError(
            f"introspector exited with {proc.returncode}: {' | '.join(tail)}"
        )
    if len(proc.stdout.encode()) > policy.max_output_bytes:
        raise DynamicIntrospectionError("introspector output exceeded the size limit")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DynamicIntrospectionError(f"introspector produced invalid JSON: {exc}") from exc
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not all(isinstance(a, dict) for a in artifacts):
        raise DynamicIntrospectionError("introspector must return {'artifacts': [ {...}, ... ]}")
    return artifacts

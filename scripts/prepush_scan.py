#!/usr/bin/env python3
"""Supply-chain checks before a push: quick enough to run every time, and the same checks CI runs later.

  osv-scanner   every lock file here (uv.lock, package-lock.json) against the OSV database
  semgrep       security-audit and secrets rules over the source

A tool that isn't installed is skipped with a note, so nobody is blocked by setup, but any finding stops the push.
Install the hook with ``pre-commit install --hook-type pre-push``. The release gate, scripts/scan_dist.py, runs the
full set on the built wheel and sdist. Keep this file byte-identical across repos (Ananke-Plexus is the source of truth).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

LOCKS = ("uv.lock", "package-lock.json", "website/package-lock.json", "pnpm-lock.yaml")
SOURCE = ("src", "scripts")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # noqa: S603 - fixed tool argv


def osv() -> list[str]:
    if not shutil.which("osv-scanner"):
        print("  skipped osv-scanner: not installed (brew install osv-scanner)")
        return []
    locks = [lock for lock in LOCKS if Path(lock).is_file()]
    if not locks:
        return []
    args = ["osv-scanner", "scan", "source", "--format", "json"]
    for lock in locks:
        args += ["-L", lock]
    res = run(args)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return [f"osv-scanner failed: {(res.stderr or res.stdout).strip()[-200:]}"]
    found = []
    for result in data.get("results", []):
        where = Path(result.get("source", {}).get("path", "")).name
        for pkg in result.get("packages", []):
            ids = [v["id"] for v in pkg.get("vulnerabilities", [])]
            if ids:
                info = pkg.get("package", {})
                found.append(f"{where}: {info.get('name')} {info.get('version')}: {', '.join(ids)}")
    print(f"  osv-scanner: {', '.join(locks)}: {len(found)} vulnerable package(s)")
    return found


def semgrep() -> list[str]:
    if shutil.which("semgrep"):
        command = ["semgrep"]
    elif shutil.which("uvx"):
        command = ["uvx", "--quiet", "semgrep"]
    else:
        print("  skipped semgrep: not installed (uv tool install semgrep)")
        return []
    targets = [s for s in SOURCE if Path(s).is_dir()]
    rules = ["--config", "p/security-audit", "--config", "p/secrets"]
    res = run([*command, "scan", "--metrics", "off", *rules, "--json", "--quiet", *targets])
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return [f"semgrep failed: {(res.stderr or res.stdout).strip()[-200:]}"]
    found = [
        f"{r['path']}:{r['start']['line']}: {r['check_id'].rsplit('.', 1)[-1]}"
        for r in data.get("results", [])
    ]
    print(
        f"  semgrep: {len(data.get('paths', {}).get('scanned', []))} files: {len(found)} finding(s)"
    )
    return found


def main() -> int:
    print("supply-chain checks before push")
    found = osv() + semgrep()
    for finding in found:
        print(f"  x {finding}")
    if found:
        print(
            "Fix these, or mark a reviewed false positive (nosemgrep, with a reason), then push again."
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

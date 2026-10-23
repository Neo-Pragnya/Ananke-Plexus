#!/usr/bin/env python3
"""Scan built distributions the way package firewalls and SCA tools do, with free tools only.

Package firewalls (Black Duck, JFrog Xray/Curation, Sonatype, Socket) quarantine a release for four kinds of
reasons. This script checks each one against the exact files in ``dist/``:

  secrets      gitleaks over every unpacked wheel and sdist                          BLOCK on any finding
  vulns        pip-audit over the dependency tree the wheel resolves to              BLOCK on any finding
  heuristics   GuardDog (malicious-package rules) and Bandit HIGH on the shipped code   reported
  inventory    what a heuristic scanner sees: scripts, minified JS, binaries, licences  reported

Usage: python scripts/scan_dist.py [--dist dist] [--out scan-report] [--extras a,b] [--require-all]

Missing tools are reported as skipped unless --require-all is given (CI installs them all). The verdict is
written to <out>/summary.md and <out>/scan.json; the exit code is 1 when anything blocks.
Keep this file byte-identical across repos (Ananke-Plexus is the source of truth).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

SCRIPT_SUFFIXES = {".sh", ".bash", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts"}
BINARY_SUFFIXES = {
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".zip",
    ".whl",
}
MINIFIED_BYTES = 200_000


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # noqa: S603 - fixed tool argv


def tool(name: str) -> list[str] | None:
    """A runnable command for ``name``: on PATH, else the Python tools through uvx."""
    if shutil.which(name):
        return [name]
    if name in {"pip-audit", "guarddog", "bandit"} and shutil.which("uvx"):
        return ["uvx", "--quiet", "--python", "3.12", name]
    return None


def unpack(artifact: Path, dest: Path) -> Path:
    out = dest / artifact.name
    out.mkdir(parents=True)
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as z:
            z.extractall(out)  # noqa: S202 - zipfile strips absolute and ../ member paths
    else:
        with tarfile.open(artifact) as t:
            t.extractall(out, filter="data")
    return out


def check_secrets(trees: list[Path], config: Path | None) -> dict:
    cmd = tool("gitleaks")
    if not cmd:
        return {"status": "skipped", "detail": "install gitleaks"}
    findings = []
    for tree in trees:
        report = tree.parent / f"{tree.name}.gitleaks.json"
        args = [
            *cmd,
            "dir",
            str(tree),
            "--no-banner",
            "--redact",
            "-f",
            "json",
            "-r",
            str(report),
            "--exit-code",
            "0",
        ]
        if config:
            args += ["--config", str(config)]
        res = run(args)
        if res.returncode != 0:
            return {"status": "error", "detail": res.stderr.strip()[-400:]}
        for f in json.loads(report.read_text() or "[]"):
            findings.append(
                f"{f['RuleID']}: {tree.name}/{Path(f['File']).relative_to(tree)}:{f['StartLine']}"
            )
    return {"status": "block" if findings else "pass", "findings": findings}


def check_vulns(wheels: list[Path], extras: str, work: Path) -> dict:
    audit = tool("pip-audit")
    if not audit or not shutil.which("uv"):
        return {"status": "skipped", "detail": "install uv and pip-audit"}
    findings, resolved = [], 0
    for wheel in wheels:
        venv = work / f"venv-{wheel.stem}"
        run(["uv", "venv", "-q", "--python", "3.12", str(venv)])
        python = str(venv / "bin" / "python")
        res = run(
            [
                "uv",
                "pip",
                "install",
                "-q",
                "--python",
                python,
                f"{wheel}[{extras}]" if extras else str(wheel),
            ]
        )
        if res.returncode != 0:
            return {"status": "error", "detail": res.stderr.strip()[-400:]}
        own = wheel.name.split("-")[0].replace("_", "-").lower()
        freeze = run(["uv", "pip", "freeze", "--python", python]).stdout.splitlines()
        reqs = [r for r in freeze if r and not r.lower().replace("_", "-").startswith(own + "==")]
        resolved += len(reqs)
        req_file = work / f"{wheel.stem}.requirements.txt"
        req_file.write_text("\n".join(reqs) + "\n")
        res = run(
            [
                *audit,
                "-r",
                str(req_file),
                "--no-deps",
                "--disable-pip",
                "-f",
                "json",
                "--progress-spinner",
                "off",
            ]
        )
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            return {"status": "error", "detail": (res.stderr or res.stdout).strip()[-400:]}
        for dep in data.get("dependencies", []):
            for v in dep.get("vulns", []):
                fixed = ", ".join(v.get("fix_versions") or []) or "-"
                findings.append(f"{dep['name']} {dep['version']}: {v['id']} (fixed in {fixed})")
    return {"status": "block" if findings else "pass", "resolved": resolved, "findings": findings}


def check_heuristics(artifacts: list[Path], trees: list[Path]) -> dict:
    out: dict = {"status": "report", "guarddog": {}, "bandit_high": []}
    guarddog = tool("guarddog")
    if guarddog:
        for art in artifacts:
            res = run([*guarddog, "pypi", "scan", str(art), "--output-format", "json"])
            try:
                results = json.loads(res.stdout).get("results", {})
            except json.JSONDecodeError:
                out["guarddog"][art.name] = {"error": [(res.stderr or res.stdout).strip()[-300:]]}
                continue
            out["guarddog"][art.name] = {
                rule: [str(hit.get("location", ""))[:160] for hit in hits][:5]
                for rule, hits in results.items()
                if hits and isinstance(hits, list)
            }
    else:
        out["guarddog"] = "skipped: install guarddog"
    bandit = tool("bandit")
    if bandit:
        for tree in (t for t in trees if t.name.endswith(".whl")):
            res = run([*bandit, "-r", "-q", "-f", "json", "--severity-level", "high", str(tree)])
            try:
                hits = json.loads(res.stdout).get("results", [])
            except json.JSONDecodeError:
                continue
            out["bandit_high"] += [
                f"{h['test_id']} {h['test_name']}: {Path(h['filename']).relative_to(tree)}:{h['line_number']}"
                for h in hits
            ]
    else:
        out["bandit_high"] = "skipped: install bandit"
    return out


def inventory(trees: list[Path]) -> dict:
    report = {}
    for tree in trees:
        files = [p for p in tree.rglob("*") if p.is_file()]
        suffixes = Counter(p.suffix.lower() or p.name for p in files)
        meta = next(iter(tree.glob("*.dist-info/METADATA")), None) or next(
            iter(tree.glob("*/PKG-INFO")), None
        )
        licence = ""
        if meta:
            for line in meta.read_text(errors="replace").splitlines():
                if line.startswith(("License-Expression:", "License:")):
                    licence = line.split(":", 1)[1].strip()
                    break
        report[tree.name] = {
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "license": licence,
            "scripts": sum(n for s, n in suffixes.items() if s in SCRIPT_SUFFIXES),
            "binaries": sorted(
                str(p.relative_to(tree)) for p in files if p.suffix.lower() in BINARY_SUFFIXES
            ),
            "minified": sorted(
                str(p.relative_to(tree))
                for p in files
                if p.suffix in {".js", ".mjs"} and p.stat().st_size > MINIFIED_BYTES
            ),
            "top_types": suffixes.most_common(8),
        }
    return report


def summary_md(result: dict) -> str:
    icon = {"pass": "✅", "block": "❌", "skipped": "⏭️", "error": "⚠️"}
    lines = [
        f"# Distribution scan: {result['verdict']}",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for name in ("secrets", "vulns"):
        c = result[name]
        detail = c.get("detail") or f"{len(c.get('findings', []))} finding(s)"
        if "resolved" in c:
            detail += f" in {c['resolved']} resolved dependencies"
        lines.append(f"| {name} | {icon[c['status']]} {c['status']} | {detail} |")
    lines.append(
        "| heuristics | 🔎 report | GuardDog rules and Bandit HIGH below: review each, don't ignore |"
    )
    for name in ("secrets", "vulns"):
        if result[name].get("findings"):
            lines += ["", f"## {name}", "", *[f"- {f}" for f in result[name]["findings"]]]
    lines += ["", "## Heuristics (what a malicious-package scanner flags)", ""]
    gd = result["heuristics"]["guarddog"]
    if isinstance(gd, str):
        lines.append(f"- GuardDog {gd}")
    else:
        for art, rules in gd.items():
            matched = ", ".join(f"`{r}` ({len(v)})" for r, v in rules.items())
            lines.append(f"- **{art}**: {matched or 'no rule matched'}")
    bh = result["heuristics"]["bandit_high"]
    lines.append(f"- Bandit HIGH: {bh if isinstance(bh, str) else len(bh)}")
    if isinstance(bh, list):
        lines += [f"  - {h}" for h in bh[:15]]
    lines += [
        "",
        "## Inventory",
        "",
        "| Artifact | Files | MB | Licence | Scripts | Binaries | Minified JS |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for art, inv in result["inventory"].items():
        lines.append(
            f"| {art} | {inv['files']} | {inv['bytes'] / 1e6:.1f} | {inv['license'] or '—'} | {inv['scripts']} | "
            f"{len(inv['binaries'])} | {len(inv['minified'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dist", type=Path, default=Path("dist"))
    ap.add_argument("--out", type=Path, default=Path("scan-report"))
    ap.add_argument(
        "--extras", default="", help="extras to install when resolving dependencies, e.g. all,mcp"
    )
    ap.add_argument("--gitleaks-config", type=Path, default=Path(".gitleaks.toml"))
    ap.add_argument(
        "--require-all", action="store_true", help="fail when a scanner is not installed"
    )
    args = ap.parse_args()

    artifacts = sorted([*args.dist.glob("*.whl"), *args.dist.glob("*.tar.gz")])
    if not artifacts:
        print(f"no wheel or sdist in {args.dist}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scan-dist-") as tmp:
        work = Path(tmp)
        trees = [unpack(a, work / "unpacked") for a in artifacts]
        config = args.gitleaks_config if args.gitleaks_config.is_file() else None
        result = {
            "artifacts": [a.name for a in artifacts],
            "secrets": check_secrets(trees, config),
            "vulns": check_vulns([a for a in artifacts if a.suffix == ".whl"], args.extras, work),
            "heuristics": check_heuristics(artifacts, trees),
            "inventory": inventory(trees),
        }
    statuses = {result["secrets"]["status"], result["vulns"]["status"]}
    if statuses & {"block", "error"} or ("skipped" in statuses and args.require_all):
        result["verdict"] = "BLOCK"
    elif "skipped" in statuses:
        result["verdict"] = "INCOMPLETE"
    else:
        result["verdict"] = "PASS"
    (args.out / "scan.json").write_text(json.dumps(result, indent=2) + "\n")
    md = summary_md(result)
    (args.out / "summary.md").write_text(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    print(md)
    return 1 if result["verdict"] == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())

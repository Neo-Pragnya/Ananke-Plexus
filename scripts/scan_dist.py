#!/usr/bin/env python3
"""Scan built distributions the way package firewalls and SCA tools do, with free tools only.

Package firewalls (Black Duck, JFrog Xray/Curation, Sonatype, Socket) quarantine a release for four kinds of
reasons. This script checks each one against the exact files in ``dist/``:

  secrets      gitleaks over every unpacked wheel and sdist                          BLOCK on any finding
  vulns        pip-audit over the dependency tree the wheel resolves to, and Grype and  BLOCK on any finding
               osv-scanner when installed: three advisory databases, so one that lags
               doesn't let a CVE through
  bundled      minified or third-party code, and whether the declared licence covers it  BLOCK unless accepted
  signatures   what infostealers and droppers do: keychain dumps, browser stores,      BLOCK unless accepted
               SSH and cloud keys, pipe-to-shell, encoded PowerShell, exec of decoded
               data, chat webhooks, tunnels, hidden Unicode in code
  heuristics   GuardDog rules compared with a reviewed baseline; Bandit HIGH         BLOCK on anything new
  inventory    what a heuristic scanner sees: scripts, minified JS, binaries, licences  reported

Anything accepted goes in ``.scan-dist.toml`` with a reason, and a bundled file's licence must be in the package's
License-Expression::

  [heuristics]
  capability-process-spawn = "runs git, with a fixed argv"

  [[bundled]]
  path = "pkg/static/vendor.min.js"
  licence = "MIT"
  reason = "..."

  [[signature]]
  rule = "pipe-to-shell"
  path = "pkg/docs/install.md"
  reason = "..."

A quarantine has always come from something in this list that was only reported: a vendored minified bundle with
undeclared licences, credential-shaped placeholders, a keychain command. So these block, and accepting one is a
written decision.

Usage: python scripts/scan_dist.py [--dist dist] [--out scan-report] [--extras a,b] [--require-all]

Missing tools are reported as skipped unless --require-all is given (CI installs them all). The verdict is
written to <out>/summary.md and <out>/scan.json; the exit code is 1 when anything blocks.
Keep this file byte-identical across repos (Ananke-Plexus is the source of truth).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

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
MINIFIED_LINE = 1_000  # a hand-written file rarely has a line this long; a bundle usually does
CODE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
}
# Another party's code announces itself: a licence banner, or a bundler's own markers.
THIRD_PARTY = re.compile(
    r"@license\b|/\*![\s\S]{0,200}?(?:license|copyright)|SPDX-License-Identifier|Released under the [\w.\- ]+ licen[cs]e"
    r"|__esbuild_esm_|webpackBootstrap|__webpack_require__|sourceMappingURL=",
    re.IGNORECASE,
)
# Each of these is what malware does, not what a library usually needs. Specific on purpose: a broad pattern teaches
# people to accept findings without reading them.
SIGNATURES = {
    "keychain-dump": r"find-generic-password|find-internet-password|dump-keychain",
    "browser-credential-store": r"Login Data[\"' ]|Cookies\.sqlite|logins\.json|key4\.db|Local State[\"' ].{0,80}encrypted_key",
    "ssh-private-key-read": r"\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)\b",
    "cloud-credentials-read": r"\.aws/credentials|\.config/gcloud/(?:credentials|application_default)|\.azure/accessTokens",
    "pipe-to-shell": r"(?:curl|wget)\b[^\n|]{0,160}\|\s*(?:sudo\s+)?(?:ba|z)?sh\b|iwr\b[^\n|]{0,160}\|\s*iex\b",
    "encoded-powershell": r"-(?:EncodedCommand|enc)\s+[A-Za-z0-9+/=]{24,}",
    "exec-decoded": r"(?:exec|eval)\s*\(\s*(?:base64|codecs|zlib|marshal|bytes\.fromhex|__import__\(['\"]base64)",
    "chat-webhook": r"discord(?:app)?\.com/api/webhooks|api\.telegram\.org/bot|hooks\.slack\.com/services",
    "tunnel-or-paste-host": r"\bngrok\.(?:io|app)|pastebin\.com/raw|transfer\.sh/|\.trycloudflare\.com",
}
HIDDEN_UNICODE = re.compile("[\u202a-\u202e\u2066-\u2069\u200b-\u200d\u2060]")


def load_accepted(path: Path) -> dict:
    """What a person has reviewed and accepted, with reasons. A missing reason is no acceptance."""
    if not path.is_file() or tomllib is None:
        return {"heuristics": {}, "bundled": [], "signature": []}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for kind in ("bundled", "signature"):
        for entry in data.get(kind, []):
            if not str(entry.get("reason", "")).strip():
                raise SystemExit(f"{path}: every [[{kind}]] needs a reason: {entry}")
    for rule, why in data.get("heuristics", {}).items():
        reason = why.get("reason", "") if isinstance(why, dict) else why
        if not str(reason).strip():
            raise SystemExit(f"{path}: heuristics.{rule} needs a reason")
    return {
        "heuristics": data.get("heuristics", {}),
        "bundled": data.get("bundled", []),
        "signature": data.get("signature", []),
    }


def _inside(path: str) -> str:
    """A path inside an artifact, without the sdist's own top folder, so one entry fits the wheel and the sdist."""
    first, _, rest = path.partition("/")
    return rest if rest and re.fullmatch(r"[\w.\-]+-\d[\w.\-]*", first) else path


def _declared(tree: Path) -> str:
    meta = next(iter(tree.glob("*.dist-info/METADATA")), None) or next(
        iter(tree.glob("*/PKG-INFO")), None
    )
    if not meta:
        return ""
    for line in meta.read_text(errors="replace").splitlines():
        if line.startswith(("License-Expression:", "License:")):
            return line.split(":", 1)[1].strip()
    return ""


def check_bundled(trees: list[Path], accepted: list[dict]) -> dict:
    """Minified or third-party code must be accepted by name, with a licence the package declares."""
    findings, notes = [], []
    for tree in trees:
        declared = _declared(tree)
        for file in sorted(
            p
            for p in tree.rglob("*")
            if p.is_file() and p.suffix.lower() in {".js", ".mjs", ".cjs", ".css"}
        ):
            rel = _inside(str(file.relative_to(tree)))
            text = file.read_text(encoding="utf-8", errors="replace")
            longest = max((len(line) for line in text.splitlines()), default=0)
            why = [
                w
                for w, hit in (
                    ("minified", file.stat().st_size > MINIFIED_BYTES or longest > MINIFIED_LINE),
                    ("another party's licence or bundler marks", bool(THIRD_PARTY.search(text))),
                )
                if hit
            ]
            if not why:
                continue
            entry = next((e for e in accepted if fnmatch.fnmatch(rel, e.get("path", ""))), None)
            if entry is None:
                findings.append(
                    f"{tree.name}/{rel}: {', '.join(why)}. Remove it, or accept it in .scan-dist.toml with its licence"
                )
            elif str(entry.get("licence", "")) not in declared:
                findings.append(
                    f"{tree.name}/{rel}: its licence {entry.get('licence')!r} isn't in License-Expression {declared!r}"
                )
            else:
                notes.append(f"{tree.name}/{rel}: accepted ({entry['reason']})")
    return {"status": "block" if findings else "pass", "findings": findings, "accepted": notes}


def check_signatures(trees: list[Path], accepted: list[dict]) -> dict:
    """What malware does. Docs count too: a firewall reads every file, and an example can look like an instruction."""
    findings, notes = [], []
    for tree in trees:
        for file in sorted(
            p for p in tree.rglob("*") if p.is_file() and p.stat().st_size < 5_000_000
        ):
            data = file.read_bytes()
            if b"\0" in data[:4096]:
                continue
            text = data.decode("utf-8", errors="replace")
            rel = _inside(str(file.relative_to(tree)))
            hits = [rule for rule, rx in SIGNATURES.items() if re.search(rx, text, re.IGNORECASE)]
            if file.suffix.lower() in CODE_SUFFIXES and HIDDEN_UNICODE.search(text):
                hits.append("hidden-unicode-in-code")
            for rule in hits:
                ok = next(
                    (
                        e
                        for e in accepted
                        if e.get("rule") == rule and fnmatch.fnmatch(rel, e.get("path", ""))
                    ),
                    None,
                )
                if ok:
                    notes.append(f"{tree.name}/{rel}: {rule} accepted ({ok['reason']})")
                else:
                    findings.append(f"{tree.name}/{rel}: {rule}")
    return {"status": "block" if findings else "pass", "findings": findings, "accepted": notes}


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
        findings += _second_opinions(venv, req_file, work)
    tools = ["pip-audit", *(t for t in ("grype", "osv-scanner") if shutil.which(t))]
    return {
        "status": "block" if findings else "pass",
        "resolved": resolved,
        "findings": sorted(set(findings)),
        "tools": tools,
    }


def _second_opinions(venv: Path, req_file: Path, work: Path) -> list[str]:
    """Grype (through a Syft SBOM) and osv-scanner, when installed. Their databases differ from pip-audit's."""
    findings: list[str] = []
    if shutil.which("grype") and shutil.which("syft"):
        sbom = work / f"{venv.name}.sbom.json"
        run(["syft", "-q", f"dir:{venv}", "-o", f"syft-json={sbom}"])
        res = run(["grype", "-q", f"sbom:{sbom}", "-o", "json"])
        try:
            for m in json.loads(res.stdout).get("matches", []):
                a, v = m["artifact"], m["vulnerability"]
                fixed = ", ".join((v.get("fix") or {}).get("versions") or []) or "-"
                findings.append(f"{a['name']} {a['version']}: {v['id']} (fixed in {fixed}, grype)")
        except json.JSONDecodeError:
            findings.append(f"grype failed: {(res.stderr or res.stdout).strip()[-200:]}")
    if shutil.which("osv-scanner"):
        res = run(
            [
                "osv-scanner",
                "scan",
                "source",
                "-L",
                f"requirements.txt:{req_file}",
                "--format",
                "json",
            ]
        )
        try:
            for r in json.loads(res.stdout or "{}").get("results", []):
                for pkg in r.get("packages", []):
                    for v in pkg.get("vulnerabilities", []):
                        info = pkg.get("package", {})
                        findings.append(
                            f"{info.get('name')} {info.get('version')}: {v['id']} (osv-scanner)"
                        )
        except json.JSONDecodeError:
            findings.append(f"osv-scanner failed: {(res.stderr or res.stdout).strip()[-200:]}")
    return findings


def check_heuristics(
    artifacts: list[Path], trees: list[Path], accepted: dict | None = None
) -> dict:
    out: dict = {"status": "pass", "guarddog": {}, "bandit_high": [], "findings": []}
    accepted = accepted or {}
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
    # Anything a heuristic matched that nobody has reviewed blocks, so a new kind of hit is seen the day it appears.
    if isinstance(out["guarddog"], dict):
        out["findings"] += _unreviewed(out["guarddog"], accepted)
    else:
        out["status"] = "skipped"
    if isinstance(out["bandit_high"], list):
        out["findings"] += [f"Bandit HIGH {h}" for h in out["bandit_high"]]
    if out["findings"]:
        out["status"] = "block"
    return out


def _unreviewed(guarddog: dict, accepted: dict) -> list[str]:
    """GuardDog hits nobody has accepted. A rule accepted with ``paths`` counts only at those paths."""
    findings = []
    for art, rules in guarddog.items():
        for rule, where in rules.items():
            entry = accepted.get(rule)
            if isinstance(entry, dict) and entry.get("paths"):
                files = [_inside(w.split(":", 1)[0]) for w in where]
                if not all(any(fnmatch.fnmatch(f, g) for g in entry["paths"]) for f in files):
                    findings.append(
                        f"{art}: GuardDog {rule} outside its accepted paths, at {', '.join(where[:3])}"
                    )
            elif entry is None:
                findings.append(
                    f"{art}: GuardDog {rule} at {', '.join(where[:3])}. Fix it, or accept it in .scan-dist.toml with a reason"
                )
    return findings


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
    for name in ("secrets", "vulns", "bundled", "signatures", "heuristics"):
        c = result[name]
        detail = c.get("detail") or f"{len(c.get('findings', []))} finding(s)"
        if "resolved" in c:
            detail += f" in {c['resolved']} resolved dependencies ({', '.join(c.get('tools', ['pip-audit']))})"
        if c.get("accepted"):
            detail += f", {len(c['accepted'])} accepted"
        lines.append(f"| {name} | {icon.get(c['status'], '')} {c['status']} | {detail} |")
    for name in ("secrets", "vulns", "bundled", "signatures", "heuristics"):
        if result[name].get("findings"):
            lines += ["", f"## {name}", "", *[f"- {f}" for f in result[name]["findings"]]]
        if result[name].get("accepted"):
            lines += [
                "",
                f"### {name}: accepted",
                "",
                *[f"- {f}" for f in result[name]["accepted"]],
            ]
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
        "--accepted",
        type=Path,
        default=Path(".scan-dist.toml"),
        help="reviewed findings, with reasons",
    )
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
        accepted = load_accepted(args.accepted)
        result = {
            "artifacts": [a.name for a in artifacts],
            "secrets": check_secrets(trees, config),
            "vulns": check_vulns([a for a in artifacts if a.suffix == ".whl"], args.extras, work),
            "bundled": check_bundled(trees, accepted["bundled"]),
            "signatures": check_signatures(trees, accepted["signature"]),
            "heuristics": check_heuristics(artifacts, trees, accepted["heuristics"]),
            "inventory": inventory(trees),
        }
    statuses = {
        result[k]["status"] for k in ("secrets", "vulns", "bundled", "signatures", "heuristics")
    }
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

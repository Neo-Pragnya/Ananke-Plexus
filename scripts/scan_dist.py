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
  metadata     what an SCA tool parses first: Metadata-Version 2.4 or older, a licence  BLOCK
               classifier and a licence file, so no parser sees an unknown licence
  bounds       the oldest version every declared range allows, audited: a range that   BLOCK
               still admits a vulnerable release is flagged even when today's install
               is fine
  licences     the licence of every package in the resolved tree (pip-licenses' view),  BLOCK unless accepted
               with what pulls each one in (pipdeptree's view): copyleft or unknown blocks
  mentions     copyleft licence names inside shipped files, which licence search reads  BLOCK unless accepted
               as a declaration; each accepted one becomes a line in triage.md for
               Black Duck and similar tools
  maturity     dependency versions published in the last 14 days, which firewalls with   reported
               a maturity rule block: when one is, installing this package fails even
               though nothing here changed. The report gives the --exclude-newer date
               that avoids them
  exceptions   every accepted finding needs an owner and a review_by date ([review]     BLOCK when expired
               sets defaults); one due within 30 days is listed
  inventory    what a heuristic scanner sees: scripts, minified JS, binaries, licences  reported

It also writes findings.json (one record per finding: check, category, where, disposition, owner, review date),
provenance (commit, each artifact's sha256, scan time, every tool's version) and, with syft, a CycloneDX SBOM of
each wheel's resolved environment. A missing second CVE scanner, a Grype database over 5 days old or a missing SBOM
tool makes the scan degraded: INCOMPLETE locally, BLOCK with --require-all, never a quiet pass.

Anything accepted goes in ``.scan-dist.toml`` with a reason, and a bundled file's licence must be in the package's
License-Expression::

  [heuristics]
  capability-process-spawn = "runs git, with a fixed argv"

  [[bundled]]
  path = "pkg/static/vendor.min.js"
  licence = "MIT"
  reason = "..."

  [[bandit]]
  test = "B324"
  path = "pkg/ids.py"
  reason = "MD5 for a colour hue, not for security"

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
GATES = (
    "secrets",
    "vulns",
    "bundled",
    "signatures",
    "heuristics",
    "metadata",
    "bounds",
    "licences",
    "mentions",
    "exceptions",
)
COPYLEFT = re.compile(
    r"\b(?:A|L)?GPL(?:v?[23])?(?:-[0-9.]+(?:-only|-or-later)?)?\b|GNU (?:Affero |Lesser )?General Public License"
    r"|\bSSPL\b|Server Side Public License|Commons Clause|Business Source License|\bBUSL\b|CC-BY-NC|CC-BY-SA|Elastic License"
)
PERMISSIVE = re.compile(
    r"\b(?:MIT|BSD|Apache|ISC|PSF|Python Software Foundation|Unlicense|0BSD|Zlib|CC0|Public Domain|HPND|Unicode)\b",
    re.IGNORECASE,
)
# weak copyleft: allowed by most policies, but only with a person's review
WEAK_COPYLEFT = re.compile(
    r"\b(?:MPL|Mozilla Public License|EPL|Eclipse Public License|CDDL)\b", re.IGNORECASE
)
HIDDEN_UNICODE = re.compile("[\u202a-\u202e\u2066-\u2069\u200b-\u200d\u2060]")


def load_accepted(path: Path) -> dict:
    """What a person has reviewed and accepted, with reasons. A missing reason is no acceptance."""
    if not path.is_file() or tomllib is None:
        return {
            "heuristics": {},
            "bundled": [],
            "signature": [],
            "licence": [],
            "mention": [],
            "bandit": [],
        }
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for kind in ("bundled", "signature", "licence", "mention", "bandit"):
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
        "licence": data.get("licence", []),
        "mention": data.get("mention", []),
        "bandit": data.get("bandit", []),
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
            # zipfile strips absolute and ../ member paths, and never creates links
            z.extractall(out)  # noqa: S202  # nosec B202
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
    # a missing second opinion is a weaker check, never a quiet pass
    degraded = [t for t in ("grype", "osv-scanner") if t not in tools]
    if "grype" in tools and (age := _grype_db_age_days()) is not None and age > 5:
        degraded.append(f"grype database is {age} days old")
    status = "block" if findings else ("degraded" if degraded else "pass")
    return {
        "status": status,
        "resolved": resolved,
        "findings": sorted(set(findings)),
        "tools": tools,
        "degraded": degraded,
    }


def _grype_db_age_days() -> int | None:
    """How old Grype's vulnerability database is, so a stale one is reported, not trusted."""
    import datetime

    res = run(["grype", "db", "status", "-o", "json"])
    try:
        built = json.loads(res.stdout).get("built", "")
        moment = datetime.datetime.fromisoformat(built.replace("Z", "+00:00"))
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None
    return (datetime.datetime.now(datetime.UTC) - moment).days


def provenance(artifacts: list[Path]) -> dict:
    """What a finding record needs: the revision, each artifact's digest, when, and with which tool versions."""
    import datetime
    import hashlib

    head = run(["git", "rev-parse", "HEAD"])
    versions = {}
    for name, args in (
        ("gitleaks", ["version"]),
        ("pip-audit", ["--version"]),
        ("guarddog", ["--version"]),
        ("bandit", ["--version"]),
        ("grype", ["version"]),
        ("syft", ["version"]),
        ("osv-scanner", ["--version"]),
        ("uv", ["--version"]),
    ):
        cmd = tool(name)
        if cmd:
            out = run([*cmd, *args])
            text = (out.stdout or out.stderr).strip().splitlines()
            versions[name] = next(
                (line for line in text if any(c.isdigit() for c in line)), "unknown"
            )[:80]
        else:
            versions[name] = "not installed"
    return {
        "commit": head.stdout.strip() if head.returncode == 0 else "not a git checkout",
        "scanned_at": datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat(),
        "artifacts": {
            a.name: "sha256:" + hashlib.sha256(a.read_bytes()).hexdigest() for a in artifacts
        },
        "tools": versions,
    }


def check_exceptions(path: Path) -> dict:
    """Every accepted finding is an exception: it needs an owner and a review date, and an expired one blocks."""
    import datetime

    if not path.is_file() or tomllib is None:
        return {"status": "pass", "findings": [], "due": []}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("review", {})
    today = datetime.date.today()
    findings, due, entries = [], [], []
    for kind in ("bundled", "signature", "licence", "mention", "bandit"):
        entries += [(kind, e) for e in data.get(kind, [])]
    entries += [
        ("heuristics", {"rule": rule, **(v if isinstance(v, dict) else {"reason": v})})
        for rule, v in data.get("heuristics", {}).items()
    ]
    for kind, entry in entries:
        label = entry.get("path") or entry.get("package") or entry.get("rule") or entry.get("test")
        owner = entry.get("owner") or defaults.get("owner")
        review_by = entry.get("review_by") or defaults.get("review_by")
        if not owner or not review_by:
            findings.append(
                f"[{kind}] {label}: needs an owner and a review_by date ([review] sets defaults)"
            )
            continue
        try:
            when = datetime.date.fromisoformat(str(review_by))
        except ValueError:
            findings.append(f"[{kind}] {label}: review_by {review_by!r} isn't a YYYY-MM-DD date")
            continue
        if when < today:
            findings.append(
                f"[{kind}] {label}: its review was due {when}; review it again, or remove it"
            )
        elif (when - today).days <= 30:
            due.append(f"[{kind}] {label}: review due {when} ({owner})")
    return {
        "status": "block" if findings else "pass",
        "findings": findings,
        "due": due,
        "count": len(entries),
    }


def write_sbom(wheels: list[Path], work: Path, out: Path) -> dict:
    """A CycloneDX SBOM of what each wheel resolves to, for Black Duck or another SCA tool to import and compare."""
    if not shutil.which("syft"):
        return {"status": "degraded", "detail": "install syft to write an SBOM"}
    written = []
    for wheel in wheels:
        venv = work / f"venv-{wheel.stem}"
        if not venv.is_dir():
            continue
        target = out / f"sbom-{wheel.stem}.cdx.json"
        res = run(["syft", "-q", f"dir:{venv}", "-o", f"cyclonedx-json={target}"])
        if res.returncode == 0 and target.is_file():
            written.append(target.name)
    return {"status": "report", "detail": ", ".join(written) or "none written", "files": written}


def findings_register(result: dict, accepted_path: Path) -> list[dict]:
    """One record per finding, with what the remediation plan asks for: what, where, why, who and until when."""
    defaults = {}
    if accepted_path.is_file() and tomllib is not None:
        defaults = tomllib.loads(accepted_path.read_text(encoding="utf-8")).get("review", {})
    records = []
    for check in (*GATES, "maturity"):
        part = result.get(check, {})
        for text in part.get("findings", []):
            shipped = (
                "sdist only" if ".tar.gz" in text and ".whl" not in text else "shipped in the wheel"
            )
            records.append(
                {
                    "check": check,
                    "finding": text,
                    "category": CATEGORY.get(check, "other policy"),
                    "shipped": shipped,
                    "disposition": "report"
                    if check == "maturity"
                    else "blocked: fix, or accept with a reason",
                    "owner": defaults.get("owner", "unassigned"),
                }
            )
        for text in part.get("accepted", []):
            records.append(
                {
                    "check": check,
                    "finding": text,
                    "category": CATEGORY.get(check, "other policy"),
                    "disposition": "accepted exception (.scan-dist.toml)",
                    "owner": defaults.get("owner", "unassigned"),
                    "review_by": defaults.get("review_by", "unset"),
                }
            )
    return records


CATEGORY = {
    "secrets": "provenance: credential-shaped content",
    "vulns": "vulnerability advisory",
    "bounds": "vulnerability advisory (declared range)",
    "bundled": "licence and provenance: third-party or minified code",
    "signatures": "malware-shaped behaviour",
    "heuristics": "malware-shaped behaviour",
    "metadata": "identification: metadata a scanner reads",
    "licences": "licence policy",
    "mentions": "licence policy: possible false identification",
    "exceptions": "exception review",
    "maturity": "other policy: package age",
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
    artifacts: list[Path],
    trees: list[Path],
    accepted: dict | None = None,
    bandit_accepted: list[dict] | None = None,
) -> dict:
    out: dict = {
        "status": "pass",
        "guarddog": {},
        "bandit_high": [],
        "findings": [],
        "accepted": [],
    }
    accepted = accepted or {}
    bandit_accepted = bandit_accepted or []
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
        # a reviewed hit is accepted by its test id and file, so the same test in any other file still blocks
        for hit in out["bandit_high"]:
            test = hit.split(" ", 1)[0]
            where = hit.rsplit(": ", 1)[-1].rsplit(":", 1)[0]
            ok = next(
                (
                    e
                    for e in bandit_accepted
                    if e.get("test") == test and fnmatch.fnmatch(where, e.get("path", ""))
                ),
                None,
            )
            if ok:
                out["accepted"].append(f"Bandit {hit}: accepted ({ok['reason']})")
            else:
                out["findings"].append(f"Bandit HIGH {hit}")
    if out["findings"]:
        out["status"] = "block"
    return out


MATURE_DAYS = 14


def check_maturity(wheels: list[Path], work: Path, days: int = MATURE_DAYS) -> dict:
    """Resolved dependency versions younger than ``days``. Reported: the ranges are open on purpose."""
    import datetime
    import urllib.request

    now = datetime.datetime.now(datetime.UTC)
    cutoff = (now - datetime.timedelta(days=days)).date()
    young: list[str] = []
    names: set[str] = set()
    for wheel in wheels:
        req_file = work / f"{wheel.stem}.requirements.txt"
        if not req_file.is_file():
            return {"status": "skipped", "detail": "needs the environment the vulns check installs"}
        for line in req_file.read_text().splitlines():
            if "==" not in line:
                continue
            name, version = line.split("==", 1)
            url = f"https://pypi.org/pypi/{name}/{version.strip()}/json"
            try:
                # always https://pypi.org; the name and version come from the resolved environment
                # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                with urllib.request.urlopen(url, timeout=15) as response:
                    files = json.load(response).get("urls", [])
            except (OSError, ValueError):
                continue
            stamps = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
            if not stamps:
                continue
            published = min(
                datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) for s in stamps
            )
            age = (now - published).days
            if age < days:
                young.append(f"{name} {version} was published {published.date()} ({age} days ago)")
                names.add(name)
    # only the young dependencies get a cutoff: this package itself may be days old too
    advice = ""
    if names:
        flags = " ".join(f"--exclude-newer-package {n}={cutoff}" for n in sorted(names))
        advice = f"behind a firewall with a maturity rule: uv pip install <package> {flags}"
    return {
        "status": "report",
        "findings": sorted(set(young)),
        "detail": advice or f"every dependency is over {days} days old",
    }


def check_metadata(trees: list[Path]) -> dict:
    """What an SCA parser reads first. A format it doesn't know, or no licence it can read, is an unknown component."""
    findings = []
    for tree in (t for t in trees if t.name.endswith(".whl")):
        meta = next(iter(tree.glob("*.dist-info/METADATA")), None)
        if meta is None:
            findings.append(f"{tree.name}: no METADATA")
            continue
        fields = meta.read_text(errors="replace").split("\n\n", 1)[0].splitlines()
        version = next(
            (f.split(":", 1)[1].strip() for f in fields if f.startswith("Metadata-Version:")), ""
        )
        if tuple(int(x) for x in version.split(".") if x.isdigit()) > (2, 4):
            findings.append(
                f'{tree.name}: Metadata-Version {version}; older parsers stop at 2.4. Set core-metadata-version = "2.4"'
            )
        if not any(f.startswith("Classifier: License ::") for f in fields):
            findings.append(
                f"{tree.name}: no licence classifier, so a parser that ignores License-Expression sees none"
            )
        if not any(f.startswith(("License-Expression:", "License:")) for f in fields):
            findings.append(f"{tree.name}: no licence field")
        if not any(f.startswith("License-File:") for f in fields):
            findings.append(f"{tree.name}: no licence file")
    return {"status": "block" if findings else "pass", "findings": findings}


def check_bounds(wheels: list[Path], work: Path) -> dict:
    """Audit the oldest version every declared range allows, as a tool reading the ranges would."""
    audit = tool("pip-audit")
    if not audit or not shutil.which("uv"):
        return {"status": "skipped", "detail": "install uv and pip-audit"}
    findings = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
            head = z.read(meta).decode("utf-8", "replace").split("\n\n", 1)[0]
        requires = [
            r.split(":", 1)[1].strip() for r in head.splitlines() if r.startswith("Requires-Dist:")
        ]
        base = [r for r in requires if "extra ==" not in r]
        if not base:
            continue
        req_in, low = work / f"{wheel.stem}.bounds.in", work / f"{wheel.stem}.bounds.txt"
        req_in.write_text("\n".join(base) + "\n")
        res = run(
            [
                "uv",
                "pip",
                "compile",
                "-q",
                "--resolution",
                "lowest-direct",
                "--python-version",
                "3.11",
                str(req_in),
                "-o",
                str(low),
            ]
        )
        if res.returncode != 0:
            findings.append(
                f"{wheel.name}: the oldest allowed versions don't resolve: {res.stderr.strip()[-200:]}"
            )
            continue
        res = run(
            [
                *audit,
                "-r",
                str(low),
                "--no-deps",
                "--disable-pip",
                "-f",
                "json",
                "--progress-spinner",
                "off",
            ]
        )
        try:
            for dep in json.loads(res.stdout).get("dependencies", []):
                for v in dep.get("vulns", []):
                    fixed = ", ".join(v.get("fix_versions") or []) or "-"
                    findings.append(
                        f"{wheel.name}: allows {dep['name']} {dep['version']}, which has {v['id']}; raise the lower bound to {fixed}"
                    )
        except json.JSONDecodeError:
            findings.append(f"{wheel.name}: pip-audit failed on the lower bounds")
    return {"status": "block" if findings else "pass", "findings": sorted(set(findings))}


LICENCE_PROBE = """
import importlib.metadata as m, json
out = []
for d in m.distributions():
    md = d.metadata
    lic = md.get("License-Expression") or ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
        text = (md.get("License") or "").strip().splitlines()
        lic = " OR ".join(cls) or (text[0][:80] if text else "")
    reqs = [r.split(";")[0].split("[")[0].strip() for r in (d.requires or []) if "extra ==" not in r]
    out.append({"name": md["Name"], "version": d.version, "licence": lic or "UNKNOWN", "requires": reqs})
print(json.dumps(out))
"""


def check_licences(wheels: list[Path], work: Path, accepted: list[dict]) -> dict:
    """Every resolved package's licence, read from its own metadata, and what pulls it in."""
    findings, notes, table = [], [], []
    canon = lambda s: re.sub(r"[-_.]+", "-", s).lower()  # noqa: E731
    for wheel in wheels:
        python = work / f"venv-{wheel.stem}" / "bin" / "python"
        if not python.exists():
            return {"status": "skipped", "detail": "needs the environment the vulns check installs"}
        res = run([str(python), "-c", LICENCE_PROBE])
        try:
            dists = json.loads(res.stdout)
        except json.JSONDecodeError:
            return {"status": "error", "detail": res.stderr.strip()[-300:]}
        pulled: dict[str, set[str]] = {}
        for d in dists:
            for r in d["requires"]:
                name = re.split(r"[ <>=!~(]", r, maxsplit=1)[0]
                pulled.setdefault(canon(name), set()).add(d["name"])
        for d in sorted(dists, key=lambda d: canon(d["name"])):
            lic = d["licence"]
            via = ", ".join(sorted(pulled.get(canon(d["name"]), set()))) or "top level"
            table.append(f"{d['name']} {d['version']}: {lic} (pulled in by {via})")
            if (
                lic != "UNKNOWN"
                and not COPYLEFT.search(lic)
                and not WEAK_COPYLEFT.search(lic)
                and PERMISSIVE.search(lic)
            ):
                continue
            ok = next(
                (e for e in accepted if canon(e.get("package", "")) == canon(d["name"])), None
            )
            if ok:
                notes.append(f"{d['name']} ({lic}): accepted ({ok['reason']})")
            else:
                findings.append(
                    f"{wheel.name}: {d['name']} {d['version']} is {lic}, pulled in by {via}"
                )
    return {
        "status": "block" if findings else "pass",
        "findings": sorted(set(findings)),
        "accepted": sorted(set(notes)),
        "table": table,
    }


def check_mentions(trees: list[Path], accepted: list[dict]) -> dict:
    """Copyleft licence names in shipped files. Licence search reads one as a declaration for the whole component."""
    findings, notes, triage = [], [], []
    for tree in trees:
        for file in sorted(
            p for p in tree.rglob("*") if p.is_file() and p.stat().st_size < 5_000_000
        ):
            data = file.read_bytes()
            if b"\0" in data[:4096]:
                continue
            names = sorted(
                {m.group(0) for m in COPYLEFT.finditer(data.decode("utf-8", errors="replace"))}
            )
            if not names:
                continue
            rel = _inside(str(file.relative_to(tree)))
            ok = next((e for e in accepted if fnmatch.fnmatch(rel, e.get("path", ""))), None)
            if ok:
                notes.append(f"{tree.name}/{rel}: {', '.join(names)} accepted")
                triage.append(f"| `{rel}` | {', '.join(names)} | {ok['reason']} |")
            else:
                findings.append(f"{tree.name}/{rel}: names {', '.join(names)}")
    return {
        "status": "block" if findings else "pass",
        "findings": findings,
        "accepted": notes,
        "triage": sorted(set(triage)),
    }


def triage_md(result: dict) -> str:
    """What to tell Black Duck (or another licence search) about matches that aren't the component's licence."""
    lines = [
        "# Licence-search triage",
        "",
        "Each file below names a licence, but the package isn't under it. Mark these as false identifications",
        "(licence not applicable to the component) with the reason given. The package's own licence is its",
        "License-Expression. Every dependency's licence is in scan.json under licences.table.",
        "",
        "| File | Names | Why it isn't this package's licence |",
        "| --- | --- | --- |",
        *result["mentions"].get("triage", []),
    ]
    if result["licences"].get("accepted"):
        lines += [
            "",
            "## Dependencies outside the permissive set, reviewed",
            "",
            *[f"- {n}" for n in result["licences"]["accepted"]],
        ]
    return "\n".join(lines) + "\n"


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
    icon = {"pass": "✅", "block": "❌", "skipped": "⏭️", "error": "⚠️", "degraded": "⚠️"}
    lines = [
        f"# Distribution scan: {result['verdict']}",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for name in GATES:
        c = result[name]
        detail = c.get("detail") or f"{len(c.get('findings', []))} finding(s)"
        if "resolved" in c:
            detail += f" in {c['resolved']} resolved dependencies ({', '.join(c.get('tools', ['pip-audit']))})"
        if c.get("accepted"):
            detail += f", {len(c['accepted'])} accepted"
        lines.append(f"| {name} | {icon.get(c['status'], '')} {c['status']} | {detail} |")
    for name in GATES:
        if result[name].get("findings"):
            lines += ["", f"## {name}", "", *[f"- {f}" for f in result[name]["findings"]]]
        if result[name].get("accepted"):
            lines += [
                "",
                f"### {name}: accepted",
                "",
                *[f"- {f}" for f in result[name]["accepted"]],
            ]
    maturity = result.get("maturity", {})
    if result["vulns"].get("degraded"):
        lines += ["", "## Degraded", "", *[f"- {d}" for d in result["vulns"]["degraded"]]]
    if result["exceptions"].get("due"):
        lines += [
            "",
            "## Exceptions due for review",
            "",
            *[f"- {d}" for d in result["exceptions"]["due"]],
        ]
    prov = result.get("provenance", {})
    lines += [
        "",
        "## Provenance",
        "",
        f"- commit {prov.get('commit')}, scanned {prov.get('scanned_at')}",
    ]
    lines += [f"- {name} {digest}" for name, digest in prov.get("artifacts", {}).items()]
    lines += [f"- {name}: {ver}" for name, ver in prov.get("tools", {}).items()]
    lines += [f"- SBOM: {result.get('sbom', {}).get('detail', '')}"]
    lines += ["", "## Maturity (reported)", "", maturity.get("detail", "")]
    lines += [f"- {f}" for f in maturity.get("findings", [])]
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
            "heuristics": check_heuristics(
                artifacts, trees, accepted["heuristics"], accepted["bandit"]
            ),
            "metadata": check_metadata(trees),
            "bounds": check_bounds([a for a in artifacts if a.suffix == ".whl"], work),
            "licences": check_licences(
                [a for a in artifacts if a.suffix == ".whl"], work, accepted["licence"]
            ),
            "mentions": check_mentions(trees, accepted["mention"]),
            "maturity": check_maturity([a for a in artifacts if a.suffix == ".whl"], work),
            "exceptions": check_exceptions(args.accepted),
            "inventory": inventory(trees),
            "provenance": provenance(artifacts),
        }
        result["sbom"] = write_sbom([a for a in artifacts if a.suffix == ".whl"], work, args.out)
    statuses = {result[k]["status"] for k in GATES}
    # a missing scanner or a stale database is a degraded check; CI (--require-all) treats it as a failure
    weak = {"skipped", "degraded"} | (
        {"degraded"} if result["sbom"]["status"] == "degraded" else set()
    )
    if statuses & {"block", "error"} or (statuses & weak and args.require_all):
        result["verdict"] = "BLOCK"
    elif statuses & weak:
        result["verdict"] = "INCOMPLETE"
    else:
        result["verdict"] = "PASS"
    (args.out / "scan.json").write_text(json.dumps(result, indent=2) + "\n")
    register = findings_register(result, args.accepted)
    (args.out / "findings.json").write_text(
        json.dumps({"provenance": result["provenance"], "findings": register}, indent=2) + "\n"
    )
    md = summary_md(result)
    (args.out / "summary.md").write_text(md)
    (args.out / "triage.md").write_text(triage_md(result))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    print(md)
    return 1 if result["verdict"] == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())

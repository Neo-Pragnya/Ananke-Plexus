"""What to fix first: scanner findings, failing tests, low coverage and vulnerable dependencies in one schema, tied
to what changed (three-repo plan: CAP-quality-cve-02..04).

Everything comes from reports already in the repository (SARIF, JUnit, coverage) and the dependency check, read by
Ananke's repository analysis. Nothing is invented: a vulnerability names its package, version and advisory, a fix
version is given only when the advisory has one, and a finding with no report behind it doesn't exist.

Each finding (``finding/v1``) gets a stable id, and is placed against the change:

* **changed**: it's in a file that changed;
* **impacted**: it's in a file that imports a changed file, directly or through others (the code graph);
* **reachable**: for a vulnerable dependency, the project files that import that package. A package no file imports
  may be transitive: it's kept, but ranked lower.

The tiers (``fix now``, ``fix soon``, ``later``) and the suggestions follow from severity and those links, and the
report lists the gates to rerun: the tools whose reports these came from. Nothing is run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any

SCHEMA = "prioritized-findings/v1"
WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
LOW_COVERAGE = 0.6
# distribution name -> import name, where they differ (lowercase, '-' as '_')
IMPORT_NAMES = {
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "scikit_learn": "sklearn",
    "pillow": "PIL",
    "python_dateutil": "dateutil",
    "opencv_python": "cv2",
    "protobuf": "google",
    "pyjwt": "jwt",
    "python_dotenv": "dotenv",
    "attrs": "attr",
    "pycryptodome": "Crypto",
    "msgpack_python": "msgpack",
}
GATES = {
    "semgrep": "semgrep scan --config auto {paths}",
    "bandit": "bandit -r {paths}",
    "trivy": "trivy fs .",
    "codeql": "rerun the CodeQL workflow (it needs the build)",
    "grype": "grype dir:.",
    "ruff": "ruff check {paths}",
    "eslint": "npx eslint {paths}",
    "gitleaks": "gitleaks detect --source .",
}


def _id(*parts: Any) -> str:
    return "f-" + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _impacted(changed: set[str], graph: dict[str, Any] | None) -> set[str]:
    if not graph or not changed:
        return set(changed)
    importers: dict[str, set[str]] = defaultdict(set)
    for e in graph.get("edges", []):
        if e.get("kind") == "imports" and e.get("resolved_target"):
            src = str(e["source"]).removeprefix("file:")
            dst = str(e["resolved_target"]).removeprefix("file:")
            importers[dst].add(src)
    seen = set(changed)
    queue = deque(changed)
    while queue:
        for who in importers.get(queue.popleft(), ()):
            if who not in seen:
                seen.add(who)
                queue.append(who)
    return seen


def _importers_of(package: str, graph: dict[str, Any] | None) -> list[str]:
    if not graph:
        return []
    key = re.sub(r"[-.]+", "_", package.lower())
    module = IMPORT_NAMES.get(key, key)
    out = set()
    for e in graph.get("edges", []):
        if e.get("kind") == "imports" and not e.get("resolved_target"):
            target = str(e.get("target", "")).removeprefix("module:").split(".")[0]
            if target.lower() == module.lower():
                out.add(str(e["source"]).removeprefix("file:"))
    return sorted(out)


def normalize(report: dict[str, Any], graph: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every finding in the repository analysis as ``finding/v1``, in a stable order."""
    sections = report.get("sections", report)
    found = sections.get("findings") or {}
    out: list[dict[str, Any]] = []
    for f in found.get("findings") or []:
        out.append(
            {
                "id": _id("code", f.get("tool"), f.get("rule"), f.get("path"), f.get("line")),
                "kind": "code",
                "source": f.get("tool"),
                "rule": f.get("rule"),
                "severity": f.get("severity", "low"),
                "path": f.get("path") or None,
                "line": f.get("line"),
                "message": f.get("message", ""),
                "remediation": f.get("remediation", ""),
                "evidence": f.get("report") or "SARIF report",
            }
        )
    for t in found.get("tests") or []:
        for failure in t.get("failures") or []:
            out.append(
                {
                    "id": _id("test", failure.get("name"), t.get("report")),
                    "kind": "test",
                    "source": "junit",
                    "rule": f"test-{failure.get('kind', 'failure')}",
                    "severity": "high",
                    "path": None,
                    "line": None,
                    "message": f"{failure.get('name')}: {failure.get('message', '')}".strip(": "),
                    "remediation": "",
                    "evidence": t.get("report"),
                }
            )
    deps = sections.get("dependencies") or {}
    for v in deps.get("vulnerabilities") or []:
        out.append(
            {
                "id": _id(
                    "dependency",
                    v.get("ecosystem"),
                    v.get("package"),
                    v.get("version"),
                    v.get("id"),
                ),
                "kind": "dependency",
                "source": "osv",
                "rule": v.get("id"),
                "severity": str(v.get("severity") or "medium").lower(),
                "path": None,
                "line": None,
                "package": v.get("package"),
                "version": v.get("version"),
                "fixed_in": v.get("fixed_in"),
                "aliases": v.get("aliases", []),
                "message": v.get("summary", ""),
                "remediation": "",
                "evidence": f"{v.get('ecosystem')}:{v.get('package')}@{v.get('version')} {v.get('id')}",
                "importers": _importers_of(str(v.get("package")), graph),
            }
        )
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        out,
        key=lambda f: (
            order.get(f["severity"], 4),
            f["kind"],
            f["path"] or "",
            f["line"] or 0,
            f["id"],
        ),
    )


def prioritize(
    report: dict[str, Any],
    graph: dict[str, Any] | None,
    changed: list[str],
    *,
    low_coverage: float = LOW_COVERAGE,
) -> dict[str, Any]:
    changed_set = {c.strip("./") for c in changed if c.strip()}
    impacted = _impacted(changed_set, graph)
    findings = normalize(report, graph)
    sections = report.get("sections", report)
    coverage = sections.get("coverage") or {}
    for c in coverage.get("files") or []:
        path = str(c.get("path", ""))
        rate = c.get("line_rate")
        if path in changed_set and rate is not None and rate < low_coverage:
            findings.append(
                {
                    "id": _id("coverage", path),
                    "kind": "coverage",
                    "source": "coverage",
                    "rule": "low-coverage",
                    "severity": "medium",
                    "path": path,
                    "line": None,
                    "message": f"a changed file with {round(100 * rate)}% line coverage ({c.get('lines_covered')}/{c.get('lines_total')})",
                    "remediation": "add tests for the lines this change touched",
                    "evidence": coverage.get("source"),
                }
            )
    for f in findings:
        where = f.get("path")
        f["changed"] = bool(where and where in changed_set)
        f["impacted"] = bool(where and where in impacted and not f["changed"])
        if f["kind"] == "dependency":
            f["reachable"] = bool(f["importers"])
            f["changed"] = any(p in changed_set for p in f["importers"])
            f["impacted"] = not f["changed"] and any(p in impacted for p in f["importers"])
        score = (
            WEIGHT.get(f["severity"], 1)
            + 3 * f["changed"]
            + f["impacted"]
            + (f.get("reachable", False) if f["kind"] == "dependency" else 0)
            - (f["kind"] == "dependency" and not f.get("reachable"))
        )
        f["score"] = int(score)
        f["tier"] = (
            "fix now"
            if (f["changed"] and WEIGHT.get(f["severity"], 1) >= 2)
            or (f["severity"] == "critical" and (f.get("reachable") or f["kind"] != "dependency"))
            or f["kind"] == "test"
            else "fix soon"
            if f["impacted"] or WEIGHT.get(f["severity"], 1) >= 3
            else "later"
        )
        f["suggestion"] = _suggest(f)
    findings.sort(key=lambda f: (-f["score"], f["kind"], f["path"] or "", f["line"] or 0, f["id"]))
    tools = sorted({str(f["source"]).lower() for f in findings if f["kind"] == "code"})
    paths = " ".join(sorted(changed_set)) or "."
    gates = [
        {"gate": tool, "command": GATES[tool].format(paths=paths)}
        for tool in tools
        if tool in GATES
    ]
    gates += [
        {"gate": tool, "command": f"rerun {tool} the way it produced its report"}
        for tool in tools
        if tool not in GATES
    ]
    if any(f["kind"] == "test" for f in findings):
        gates.append({"gate": "tests", "command": "rerun the failing tests first, then the suite"})
    if any(f["kind"] == "dependency" for f in findings):
        gates.append(
            {
                "gate": "dependencies",
                "command": "dev explore --check-vulnerabilities (or pip-audit / osv-scanner)",
            }
        )
    body = [{k: v for k, v in f.items()} for f in findings]
    return {
        "schema": SCHEMA,
        "changed": sorted(changed_set),
        "impacted": sorted(impacted - changed_set),
        "counts": {
            t: sum(1 for f in findings if f["tier"] == t) for t in ("fix now", "fix soon", "later")
        },
        "findings": body,
        "rerun": gates,
        "digest": "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        "limits": [
            *((sections.get("findings") or {}).get("limits") or []),
            "Impact follows imports between the project's own files; dynamic imports aren't followed.",
            "A package no project file imports may still be used transitively: it's ranked lower, not dismissed.",
            "Nothing was run: rerun the listed gates to confirm a fix.",
        ],
    }


def _suggest(f: dict[str, Any]) -> str:
    if f["kind"] == "dependency":
        where = (
            f" (imported by {', '.join(f['importers'][:3])})"
            if f["importers"]
            else " (no project file imports it directly)"
        )
        if f.get("fixed_in"):
            return f"upgrade {f['package']} from {f['version']} to {f['fixed_in']} or later{where}"
        return f"no fixed version of {f['package']} is published for {f['rule']}: read the advisory, then remove, replace or isolate it{where}"
    if f["kind"] == "test":
        return "fix the failing test, or the code it caught; then rerun it"
    if f["kind"] == "coverage":
        return str(f["remediation"])
    where = f"{f['path']}:{f['line']}" if f.get("line") else str(f.get("path") or "")
    return f"{f['remediation'] or 'fix the ' + str(f['rule']) + ' finding'} at {where}".strip()


def to_markdown(result: dict[str, Any], limit: int = 30) -> str:
    c = result["counts"]
    lines = [
        "# What to fix first",
        "",
        f"{c['fix now']} to fix now, {c['fix soon']} soon, {c['later']} later. "
        f"{len(result['changed'])} changed file(s), {len(result['impacted'])} more affected through imports.",
        "",
        "| Tier | Severity | Kind | Where | What | Suggestion |",
        "|---|---|---|---|---|---|",
    ]
    for f in result["findings"][:limit]:
        where = f.get("path") or f.get("evidence") or ""
        if f.get("line"):
            where += f":{f['line']}"
        cells = [
            f["tier"],
            f["severity"],
            f["kind"],
            where,
            (f["message"] or str(f["rule"]))[:120],
            f["suggestion"][:160],
        ]
        lines.append(
            "| " + " | ".join(str(x).replace("|", "\\|").replace("<", "&lt;") for x in cells) + " |"
        )
    if result["rerun"]:
        lines += ["", "## Gates to rerun", ""] + [
            f"- **{g['gate']}**: `{g['command']}`" for g in result["rerun"]
        ]
    lines += ["", "## Limits", ""] + [f"- {x}" for x in result["limits"]]
    return "\n".join(lines) + "\n"


__all__ = ["SCHEMA", "normalize", "prioritize", "to_markdown"]

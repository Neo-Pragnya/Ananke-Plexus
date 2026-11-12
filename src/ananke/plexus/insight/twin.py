"""Evidence-aware software digital twin (ADV01): one model of the system, each fact with where it came from.

For every component (from the repository analysis: a service, a library, a database, a frontend) the twin joins:

* **source:** files, lines of code, languages, quality hotspots;
* **contract:** the API operations it serves (``triage api-contract``) and the tables it declares or uses;
* **infrastructure:** the Compose services, Kubernetes resources and Terraform it is deployed by;
* **verification:** coverage of its files and findings from SARIF and JUnit reports;
* **runtime:** what a captured snapshot says is running (``triage drift``), when one is given.

Every fact carries a **status**: *observed* (read from a file or report), *inferred* (concluded by an analyzer) or
*unknown* (no evidence), and the source it came from. **Freshness:** a report (coverage, SARIF, JUnit, a snapshot)
older than the latest change to the code it describes is marked **stale**, so an old coverage file isn't taken for
the current state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest, git

SCHEMA = "ananke/digital-twin@1"


def _commit_time(root: Path, paths: list[str]) -> float | None:
    if not paths or not (root / ".git").exists():
        return None
    try:
        out = git(root, "log", "-1", "--format=%ct", "--", *paths[:200]).strip()
    except InsightError:
        return None
    return float(out) if out else None


def _fresh(root: Path, report: str, code_paths: list[str]) -> dict[str, Any]:
    path = root / report
    if not path.is_file():
        return {"report": report, "status": "unknown: missing"}
    made = path.stat().st_mtime
    changed = _commit_time(root, code_paths)
    if changed is None:
        return {"report": report, "status": "observed", "made": _iso(made)}
    return {
        "report": report,
        "made": _iso(made),
        "code_changed": _iso(changed),
        "status": "stale: older than the code" if made < changed else "observed",
    }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def build(
    root: Path, *, snapshots: list[Path] | None = None, openapi: str | None = None
) -> dict[str, Any]:
    from ananke.plexus import intel
    from ananke.plexus.intel.api_contract import ApiContractError
    from ananke.plexus.intel.api_contract import build as api_build
    from ananke.plexus.intel.infra import scan

    root = root.resolve()
    sections = intel.analyze(root).get("sections") or {}
    overview = sections.get("overview") or {}
    components = overview.get("components") or []
    if not components:
        components = [
            {
                "id": "component:(root)",
                "name": root.name,
                "path": "",
                "kind": "repository",
                "language": ",".join(overview.get("languages") or []),
                "files": overview.get("files"),
                "loc": overview.get("loc"),
                "evidence": "the whole repository",
            }
        ]
    files = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    try:
        contract = api_build(root, sections.get("api") or {}, files)
    except ApiContractError:
        contract = {"operations": [], "findings": []}
    infra = scan([root])
    coverage = sections.get("coverage") or {}
    findings = sections.get("findings") or {}
    quality = sections.get("quality") or {}
    data = sections.get("data") or {}

    def inside(path: str, comp: dict[str, Any]) -> bool:
        base = str(comp.get("path") or "")
        return not base or path == base or path.startswith(base.rstrip("/") + "/")

    drift = None
    if snapshots or openapi:
        from ananke.plexus.insight.drift import compare

        drift = compare(root, snapshots or [], openapi)
    twins = []
    for comp in components:
        code_paths = [comp["path"]] if comp.get("path") else ["."]
        ops = [
            o
            for o in contract["operations"]
            if inside(str(o.get("evidence", "")).split(":")[0], comp)
        ]
        hot = [h for h in quality.get("hotspots") or [] if inside(str(h.get("path", "")), comp)]
        cov_rows = [
            (int(f.get("lines_covered") or 0), int(f.get("lines_total") or 0))
            for f in coverage.get("files") or []
            if isinstance(f, dict) and inside(str(f.get("path", "")), comp)
        ]
        covered = sum(c for c, _ in cov_rows)
        total = sum(t for _, t in cov_rows)
        tables = [
            t["name"]
            for t in data.get("tables") or []
            if inside(str(t.get("evidence", "")).split(":")[0], comp)
        ]
        used = sorted(
            {
                u["table"]
                for u in data.get("usage") or []
                if isinstance(u, dict) and inside(str(u.get("evidence", "")).split(":")[0], comp)
            }
        )
        deployed = sorted(
            {  # two Compose files (dev and prod) naming one service are one resource here
                f"{r['id']} ({r['file']})"
                for r in infra["resources"]
                if r.get("name") == comp["name"]
                or (r.get("build") and inside(str(r["build"]), comp))
            }
        )
        comp_findings = [
            f
            for f in findings.get("findings") or []
            if isinstance(f, dict) and inside(str(f.get("path") or f.get("file") or ""), comp)
        ]
        runtime = None
        if drift is not None:
            names = {comp["name"], *(d.split(":")[-1] for d in deployed)}
            runtime = [d for d in drift["drift"] if any(n and n in d["name"] for n in names)]
        evidence_reports = [coverage.get("source")] if coverage.get("source") else []
        evidence_reports += [
            r.get("path") if isinstance(r, dict) else r for r in findings.get("reports") or []
        ]
        twins.append(
            {
                "id": comp["id"],
                "name": comp["name"],
                "kind": comp.get("kind"),
                "path": comp.get("path"),
                "source": {
                    "files": comp.get("files"),
                    "loc": comp.get("loc"),
                    "language": comp.get("language"),
                    "hotspots": len(hot),
                    "status": "observed",
                    "evidence": comp.get("evidence"),
                },
                "contract": {
                    "operations": len(ops),
                    "secured": sum(1 for o in ops if o.get("auth")),
                    "tables_declared": tables,
                    "tables_used": used,
                    "status": "inferred" if ops or used else "unknown: none found",
                },
                "infrastructure": {
                    "resources": deployed,
                    "status": "observed" if deployed else "unknown: no manifest names it",
                },
                "verification": {
                    "coverage": round(covered / total, 3) if total else None,
                    "findings": len(comp_findings),
                    "status": "observed"
                    if total or comp_findings
                    else "unknown: no report covers it",
                    "freshness": [_fresh(root, str(r), code_paths) for r in evidence_reports if r],
                },
                "runtime": (
                    {"drift": runtime, "status": "observed in the snapshot"}
                    if runtime is not None
                    else {"status": "unknown: no snapshot given"}
                ),
            }
        )
    stale = sorted(
        {
            f["report"]
            for t in twins
            for f in t["verification"]["freshness"]
            if f["status"].startswith("stale")
        }
    )
    unknown = {
        k: sum(1 for t in twins if str(t[k]["status"]).startswith("unknown"))
        for k in ("contract", "infrastructure", "verification", "runtime")
    }
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "project": root.name,
        "components": twins,
        "summary": {
            "components": len(twins),
            "stale_reports": stale,
            "unknown": unknown,
            "operations": len(contract["operations"]),
            "contract_findings": len(contract["findings"]),
        },
        "limits": [
            "Each fact is as fresh as its source: a stale report describes older code.",
            "Runtime facts come only from snapshots you capture; without one, the runtime is unknown, not assumed healthy.",
        ],
    }
    report["digest"] = digest(report)
    return report


def to_markdown(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        f"# Digital twin: {r['project']}",
        "",
        f"{s['components']} components, {s['operations']} API operations, {s['contract_findings']} contract findings.",
        "",
    ]
    if s["stale_reports"]:
        lines += [f"Stale (older than the code): {', '.join(s['stale_reports'])}.", ""]
    lines += [
        "| Component | Source | Contract | Infrastructure | Verification | Runtime |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for t in r["components"]:
        v = t["verification"]
        cov = f"coverage {v['coverage']}" if v["coverage"] is not None else v["status"]
        lines.append(
            f"| {t['name']} ({t['kind']}) | {t['source']['files']} files, {t['source']['loc']} loc | "
            f"{t['contract']['operations']} ops, tables {', '.join(t['contract']['tables_used']) or '-'} | "
            f"{', '.join(t['infrastructure']['resources']) or t['infrastructure']['status']} | {cov} | "
            f"{t['runtime']['status']} |"
        )
    lines += [
        "",
        "## Limits",
        "",
        *(f"- {x}" for x in r["limits"]),
        "",
        f"Digest: `{r['digest']}`",
        "",
    ]
    return "\n".join(lines)

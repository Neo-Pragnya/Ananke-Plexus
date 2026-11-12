"""Evidence-first architecture decisions (ADV12): an ADR whose alternatives carry measured numbers, not adjectives.

Input is a small decision file (YAML or JSON)::

    title: Where order totals are computed
    context: Totals are computed twice and disagree on rounding.
    criteria: {impact: 2, risk: 3, cost: 1}          # weights; lower numbers are better for every criterion
    options:
      - name: Compute in the service layer
        touches: [backend/app/services/orders.py]    # files or symbols the option changes
        adds_dependencies: []
        assumptions:
          - {metric: effort_days, value: 2, source: team estimate}
      - name: Compute in the database
        touches: [backend/app/models.py, db/migrations]
        adds_dependencies: [sqlalchemy-utils]

For each option the repository answers what it can: how much code the change reaches and which tests and routes
(**simulated** with the what-if graph walk), the quality hotspots it touches and their complexity (**observed**), and
whether a new dependency is already used or has known vulnerabilities in the last dependency check (**observed**,
or **unknown** when there was no check). Anything else comes from the file as an **assumption** with its source.

Options are ranked by the weighted criteria, with the weights and every number shown, so the ranking can be argued
with. The ADR's status stays **proposed**: people decide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ananke.plexus.insight import InsightError, digest

SCHEMA = "ananke/decision@1"
MAX_OPTIONS = 8


def load(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InsightError(f"{path} doesn't exist") from exc
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise InsightError(f"{path.name} is malformed: {str(exc).splitlines()[0][:200]}") from exc
    if not isinstance(data, dict) or not data.get("title"):
        raise InsightError(f"{path.name} needs a title")
    options = data.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise InsightError(f"{path.name} needs at least two options: a decision has alternatives")
    if len(options) > MAX_OPTIONS:
        raise InsightError(f"{path.name} has more than {MAX_OPTIONS} options; narrow them first")
    for o in options:
        if not isinstance(o, dict) or not o.get("name"):
            raise InsightError(f"{path.name}: every option needs a name")
    return data


def _touched_files(root: Path, touches: list[str]) -> list[str]:
    out = []
    for t in touches:
        p = root / t
        if p.is_dir():
            out += sorted(x.relative_to(root).as_posix() for x in p.rglob("*") if x.is_file())
        else:
            out.append(t)
    return out


def assess(root: Path, decision: dict[str, Any]) -> dict[str, Any]:
    from ananke.plexus import intel
    from ananke.plexus.insight.timemachine import graph_of
    from ananke.plexus.insight.whatif import seeds, simulate

    root = root.resolve()
    sections = intel.analyze(root).get("sections") or {}
    graph = graph_of(root)
    hotspots: dict[str, dict[str, Any]] = {}
    for h in (sections.get("quality") or {}).get(
        "hotspots"
    ) or []:  # one per function: keep each file's worst
        if isinstance(h, dict) and h.get("path"):
            best = hotspots.get(h["path"])
            if best is None or (h.get("complexity") or 0) > (best.get("complexity") or 0):
                hotspots[h["path"]] = h
    deps = sections.get("dependencies") or {}
    known = {
        str(p.get("name", "")).lower(): p for p in deps.get("packages") or [] if isinstance(p, dict)
    }
    vulns: dict[str, list[str]] = {}
    for v in deps.get("vulnerabilities") or []:
        if isinstance(v, dict):
            vulns.setdefault(str(v.get("package", "")).lower(), []).append(str(v.get("id")))
    checked = deps.get("scanner") not in (None, "", "none")
    weights = {
        k: float(v)
        for k, v in (decision.get("criteria") or {"impact": 1, "risk": 1, "cost": 1}).items()
    }
    rows = []
    for option in decision["options"]:
        touches = [str(t) for t in option.get("touches") or []]
        files = _touched_files(root, touches)
        start, missing = seeds(graph, files) if files else ([], [])
        reached = simulate(graph, start, depth=1, both=True) if start else {}
        reached_files = sorted({r["path"] for r in reached.values() if r["path"]} | set(files))
        tests = [f for f in reached_files if "test" in f.rsplit("/", 1)[-1]]
        hot = [
            {
                "file": f,
                "complexity": hotspots[f].get("complexity"),
                "symbol": hotspots[f].get("symbol"),
            }
            for f in files
            if f in hotspots
        ]
        new_deps = []
        for dep in option.get("adds_dependencies") or []:
            name = str(dep).lower()
            new_deps.append(
                {
                    "name": dep,
                    "already_used": name in known,
                    "known_vulnerabilities": vulns.get(name, []) if checked else None,
                    "status": "observed"
                    if checked or name in known
                    else "unknown: run a dependency check",
                }
            )
        evidence = [
            {"metric": "files changed", "value": len(files), "status": "declared by the option"},
            {"metric": "code reached", "value": len(reached), "status": "simulated"},
            {"metric": "files reached", "value": len(reached_files), "status": "simulated"},
            {"metric": "tests reached", "value": len(tests), "status": "simulated"},
            {"metric": "hotspots touched", "value": len(hot), "status": "observed"},
            {
                "metric": "new dependencies",
                "value": sum(1 for d in new_deps if not d["already_used"]),
                "status": "declared by the option",
            },
        ]
        for a in option.get("assumptions") or []:
            if isinstance(a, dict) and a.get("metric"):
                evidence.append(
                    {
                        "metric": str(a["metric"]),
                        "value": a.get("value"),
                        "status": f"assumed ({a.get('source') or 'no source given'})",
                    }
                )
        vuln_count = sum(len(d["known_vulnerabilities"] or []) for d in new_deps)
        effort = next(
            (
                e["value"]
                for e in evidence
                if e["metric"] in {"effort_days", "cost", "effort"}
                and isinstance(e["value"], (int, float))
            ),
            None,
        )
        scores = {
            "impact": len(reached_files),
            "risk": len(hot) * 2
            + vuln_count * 5
            + sum(1 for d in new_deps if not d["already_used"]),
            "cost": effort if effort is not None else len(files),
        }
        rows.append(
            {
                "name": option["name"],
                "description": option.get("description") or "",
                "touches": touches,
                "not_found": missing,
                "evidence": evidence,
                "dependencies": new_deps,
                "hotspots": hot,
                "tests_reached": tests[:20],
                "scores": scores,
            }
        )
    for crit in weights:  # normalise each criterion to 0..1 across options, so units don't decide
        values = [r["scores"].get(crit, 0) for r in rows]
        low, high = min(values), max(values)
        for r in rows:
            v = r["scores"].get(crit, 0)
            r.setdefault("normalised", {})[crit] = (
                0.0 if high == low else round((v - low) / (high - low), 3)
            )
    for r in rows:
        r["weighted"] = round(sum(weights[c] * r["normalised"].get(c, 0.0) for c in weights), 3)
    ranked = sorted(rows, key=lambda r: (r["weighted"], r["name"]))
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "title": decision["title"],
        "context": decision.get("context") or "",
        "status": "proposed",
        "weights": weights,
        "options": rows,
        "ranking": [r["name"] for r in ranked],
        "limits": [
            "Lower is better for every criterion; the ranking is a starting point for the discussion, not the decision.",
            "Reach is simulated one step in both directions (measured best by `ananke graph whatif --backtest`).",
            "Assumptions are the decision file's own numbers, shown with their source and never checked.",
        ],
    }
    report["digest"] = digest(report)
    return report


def to_markdown(r: dict[str, Any]) -> str:
    lines = [
        f"# {r['title']}",
        "",
        "Status: proposed",
        "",
        "## Context",
        "",
        r["context"] or "(none given)",
        "",
        "## Options",
        "",
    ]
    for o in r["options"]:
        lines += [f"### {o['name']}", ""]
        if o["description"]:
            lines += [o["description"], ""]
        lines += ["| Evidence | Value | How it's known |", "| --- | --- | --- |"]
        lines += [f"| {e['metric']} | {e['value']} | {e['status']} |" for e in o["evidence"]]
        for d in o["dependencies"]:
            vul = d["known_vulnerabilities"]
            lines.append(
                f"| dependency {d['name']} | {'already used' if d['already_used'] else 'new'}; "
                f"vulnerabilities: {'unknown' if vul is None else len(vul)} | {d['status']} |"
            )
        if o["not_found"]:
            lines += ["", f"Not in the code graph: {', '.join(o['not_found'])}."]
        lines.append("")
    weights = ", ".join(f"{k} {v:g}" for k, v in r["weights"].items())
    lines += [
        "## Comparison",
        "",
        f"Weights: {weights}. Lower is better.",
        "",
        "| Option | " + " | ".join(r["weights"]) + " | Weighted |",
        "| --- | " + " | ".join("---" for _ in r["weights"]) + " | --- |",
    ]
    for o in sorted(r["options"], key=lambda o: o["weighted"]):
        lines.append(
            f"| {o['name']} | "
            + " | ".join(
                f"{o['scores'].get(c, 0)} ({o['normalised'].get(c, 0)})" for c in r["weights"]
            )
            + f" | {o['weighted']} |"
        )
    lines += [
        "",
        "## Decision",
        "",
        "To be decided. Record the choice, who made it and why here.",
        "",
        "## Limits",
        "",
        *(f"- {x}" for x in r["limits"]),
        "",
        f"Digest: `{r['digest']}`",
        "",
    ]
    return "\n".join(lines)

"""Infrastructure as it is declared: Compose services, Dockerfiles, Kubernetes manifests and Terraform resources,
how they depend on each other, what a container log says about each service, and a safe order to check them in
(three-repo plan: CAP-infra-docker-01..04). Nothing is started, applied or contacted.

* **Resources** (``infra/v1``), each with its file and line: Compose services (image, build folder, ports,
  ``depends_on``, health check), Dockerfiles (base image, exposed ports, health check, whether it runs as root),
  Kubernetes objects (kind, name, images, probes; a Service is linked to the workloads its selector matches) and
  Terraform resources (linked to the resources they reference).
* **Several projects** at once: a service in one that names another's service in a URL (``http://orders:8000``) or
  runs an image the other builds is linked across them.
* **A container log** (``docker compose logs`` output, ``service-1  | ...``) is split by service and triaged; each
  service's findings come with its code folder, its health check and what it depends on.
* **A validation sequence**: dry-run checks first (``docker compose config``, ``terraform validate``,
  ``kubectl apply --dry-run=client``), then build, then start dependencies before what depends on them, checking
  health at each step. Commands only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "infra/v1"
_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".terraform", "dist", "build"}
_TF_RESOURCE = re.compile(r'^\s*(resource|data|module)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{')
_TF_REF = re.compile(r"\b((?:data\.)?[a-z][a-z0-9_]*\.[a-z0-9_-]+)\.[a-z0-9_]+")
_URL_HOST = re.compile(r"\b[a-z]+://([a-z0-9][a-z0-9_-]*)(?::\d+)?\b")
_LOG_PREFIX = re.compile(r"^([a-zA-Z0-9][\w.-]*?)(?:[-_]\d+)?\s+\|\s?(.*)$")


def _files(root: Path, limit: int = 20_000) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if any(part in _SKIP for part in p.relative_to(root).parts) or not p.is_file():
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _line(lines: list[str], needle: str) -> int:
    return next((i for i, line in enumerate(lines, start=1) if needle in line), 1)


def _compose(
    root: Path, rel: str, text: str, project: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import yaml

    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, ValueError):
        return [], []
    services = (data or {}).get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return [], []
    lines = text.splitlines()
    res, edges = [], []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        rid = f"{project}:compose:{name}"
        build = spec.get("build")
        folder = (
            build
            if isinstance(build, str)
            else (build or {}).get("context")
            if isinstance(build, dict)
            else None
        )
        health = spec.get("healthcheck") if isinstance(spec.get("healthcheck"), dict) else None
        env = spec.get("environment")
        env_text = " ".join(str(v) for v in (env.values() if isinstance(env, dict) else env or []))
        res.append(
            {
                "id": rid,
                "kind": "compose-service",
                "name": str(name),
                "project": project,
                "file": rel,
                "line": _line(lines, f"{name}:"),
                "image": spec.get("image") if isinstance(spec.get("image"), str) else None,
                "build": str(Path(rel).parent / folder) if folder else None,
                "ports": [str(p) for p in spec.get("ports") or []],
                "health": " ".join(health.get("test"))
                if health and isinstance(health.get("test"), list)
                else (health or {}).get("test"),
                "urls": sorted(set(_URL_HOST.findall(env_text))),
            }
        )
        deps = spec.get("depends_on")
        for dep, cond in (
            deps.items() if isinstance(deps, dict) else ((d, None) for d in deps or [])
        ):
            condition = cond.get("condition") if isinstance(cond, dict) else None
            edges.append(
                {
                    "from": rid,
                    "to": f"{project}:compose:{dep}",
                    "via": "depends_on" + (f" ({condition})" if condition else ""),
                    "evidence": f"{rel}:{_line(lines, f'{name}:')}",
                }
            )
    return res, edges


def _dockerfile(rel: str, text: str, project: str) -> dict[str, Any]:
    lines = text.splitlines()
    froms = [
        ln.split()[1]
        for ln in lines
        if ln.strip().upper().startswith("FROM ") and len(ln.split()) > 1
    ]
    exposes = [
        p for ln in lines if ln.strip().upper().startswith("EXPOSE ") for p in ln.split()[1:]
    ]
    users = [
        ln.split()[1]
        for ln in lines
        if ln.strip().upper().startswith("USER ") and len(ln.split()) > 1
    ]
    health = next(
        (
            ln.strip()[len("HEALTHCHECK") :].strip()
            for ln in lines
            if ln.strip().upper().startswith("HEALTHCHECK")
        ),
        None,
    )
    return {
        "id": f"{project}:dockerfile:{rel}",
        "kind": "dockerfile",
        "name": rel,
        "project": project,
        "file": rel,
        "line": _line(lines, "FROM"),
        "base": froms[-1] if froms else None,
        "ports": exposes,
        "health": health,
        "runs_as_root": not users or users[-1] in ("root", "0"),
    }


def _kubernetes(rel: str, text: str, project: str) -> list[dict[str, Any]]:
    import yaml

    try:
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except (yaml.YAMLError, RecursionError, ValueError):
        return []
    lines = text.splitlines()
    out = []
    for d in docs:
        kind, meta = d.get("kind"), d.get("metadata") or {}
        if not kind or not isinstance(meta, dict) or not meta.get("name"):
            continue
        spec = d.get("spec") or {}
        template = (spec.get("template") or {}) if isinstance(spec, dict) else {}
        pod = template.get("spec") or (spec if kind == "Pod" else {})
        containers = pod.get("containers") or [] if isinstance(pod, dict) else []
        labels = (
            ((template.get("metadata") or {}).get("labels") or meta.get("labels") or {})
            if isinstance(template, dict)
            else {}
        )
        out.append(
            {
                "id": f"{project}:k8s:{kind}/{meta['name']}",
                "kind": f"k8s-{str(kind).lower()}",
                "name": str(meta["name"]),
                "project": project,
                "file": rel,
                "line": _line(lines, f"name: {meta['name']}"),
                "images": [
                    c.get("image") for c in containers if isinstance(c, dict) and c.get("image")
                ],
                "probes": sorted(
                    {
                        p
                        for c in containers
                        if isinstance(c, dict)
                        for p in ("livenessProbe", "readinessProbe")
                        if c.get(p)
                    }
                ),
                "labels": labels if isinstance(labels, dict) else {},
                "selector": (spec.get("selector") or {})
                if kind == "Service" and isinstance(spec, dict)
                else {},
            }
        )
    return out


def _terraform(
    rel: str, text: str, project: str
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    res, refs = [], []
    current = None
    for no, line in enumerate(text.splitlines(), start=1):
        m = _TF_RESOURCE.match(line)
        if m:
            block, a, b = m.groups()
            address = (
                f"{a}.{b}"
                if block == "resource" and b
                else f"data.{a}.{b}"
                if block == "data"
                else f"module.{a}"
            )
            current = f"{project}:tf:{address}"
            res.append(
                {
                    "id": current,
                    "kind": f"tf-{block}",
                    "name": address,
                    "project": project,
                    "file": rel,
                    "line": no,
                }
            )
            continue
        if current:
            for ref in _TF_REF.findall(line):
                if not ref.startswith(("var.", "local.", "each.", "count.", "path.", "terraform.")):
                    refs.append((current, f"{project}:tf:{ref}", f"{rel}:{no}"))
    return res, refs


def scan(roots: list[Path]) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tf_refs: list[tuple[str, str, str]] = []
    for root in roots:
        root = root.resolve()
        project = root.name
        for path in _files(root):
            rel = path.relative_to(root).as_posix()
            name = path.name
            if path.stat().st_size > 2_000_000:
                continue
            if not (name.endswith((".yml", ".yaml", ".tf")) or name.startswith("Dockerfile")):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if name.startswith(("docker-compose", "compose")) and name.endswith((".yml", ".yaml")):
                found, links = _compose(root, rel, text, project)
                resources += found
                edges += links
            elif name.startswith("Dockerfile"):
                resources.append(_dockerfile(rel, text, project))
            elif name.endswith(".tf"):
                found_tf, refs = _terraform(rel, text, project)
                resources += found_tf
                tf_refs += refs
            elif "apiVersion:" in text and "kind:" in text:
                resources += _kubernetes(rel, text, project)
    ids = {r["id"] for r in resources}
    edges += [
        {"from": a, "to": b, "via": "reference", "evidence": ev}
        for a, b, ev in tf_refs
        if b in ids and a != b
    ]
    for svc in (r for r in resources if r["kind"] == "k8s-service" and r["selector"]):
        for w in resources:
            if (
                w["kind"].startswith("k8s-")
                and w is not svc
                and w["labels"]
                and all(w["labels"].get(k) == v for k, v in svc["selector"].items())
            ):
                edges.append(
                    {
                        "from": svc["id"],
                        "to": w["id"],
                        "via": "selector",
                        "evidence": f"{svc['file']}:{svc['line']}",
                    }
                )
    compose = [r for r in resources if r["kind"] == "compose-service"]
    for r in compose:
        if r["build"]:
            df = next(
                (
                    d
                    for d in resources
                    if d["kind"] == "dockerfile"
                    and d["project"] == r["project"]
                    and Path(d["file"]).parent.as_posix() == Path(r["build"]).as_posix()
                ),
                None,
            )
            if df:
                edges.append(
                    {
                        "from": r["id"],
                        "to": df["id"],
                        "via": "build",
                        "evidence": f"{r['file']}:{r['line']}",
                    }
                )
        for host in r["urls"]:
            for other in compose:
                if other["name"] == host and other["project"] != r["project"]:
                    edges.append(
                        {
                            "from": r["id"],
                            "to": other["id"],
                            "via": f"url http://{host}",
                            "evidence": f"{r['file']}:{r['line']}",
                        }
                    )
        if r["image"]:
            for other in compose:
                if (
                    other["project"] != r["project"]
                    and other["build"]
                    and r["image"].split(":")[0].endswith("/" + other["name"])
                ):
                    edges.append(
                        {
                            "from": r["id"],
                            "to": other["id"],
                            "via": f"image {r['image']}",
                            "evidence": f"{r['file']}:{r['line']}",
                        }
                    )
    edges = [e for e in edges if e["to"] in ids]
    return {
        "schema": SCHEMA,
        "projects": [p.resolve().name for p in roots],
        "resources": resources,
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["via"])),
        "cross_project": [
            e for e in edges if e["from"].split(":", 1)[0] != e["to"].split(":", 1)[0]
        ],
        "warnings": _warnings(resources, edges),
    }


def _warnings(resources: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in resources:
        if r["kind"] == "dockerfile" and r["runs_as_root"]:
            out.append(f"{r['file']}: the container runs as root (no USER)")
        if (
            r["kind"] == "dockerfile"
            and r["base"]
            and (":" not in r["base"] or r["base"].endswith(":latest"))
        ):
            out.append(f"{r['file']}: base image {r['base']} isn't pinned to a version")
        if r["kind"] in ("k8s-deployment", "k8s-statefulset") and not r["probes"]:
            out.append(f"{r['file']}:{r['line']}: {r['name']} has no liveness or readiness probe")
    for e in edges:
        if e["via"] == "depends_on":
            out.append(
                f"{e['evidence']}: {e['from'].rsplit(':', 1)[-1]} starts after {e['to'].rsplit(':', 1)[-1]} but doesn't wait for it to be healthy"
            )
    return out


def triage_log(infra: dict[str, Any], text: str) -> dict[str, Any]:
    """A `docker compose logs` output split by service, triaged, and tied to each service's code and health check."""
    from ananke.plexus.connectors import _LOG_RULES, _TIMESTAMP

    by_service: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for no, raw in enumerate(text.splitlines(), start=1):
        m = _LOG_PREFIX.match(raw)
        if m:
            by_service[m.group(1)].append((no, m.group(2)))
    services = {r["name"]: r for r in infra["resources"] if r["kind"] == "compose-service"}
    deps: dict[str, list[str]] = defaultdict(list)
    for e in infra["edges"]:
        if e["via"].startswith("depends_on"):
            deps[e["from"].rsplit(":", 1)[-1]].append(e["to"].rsplit(":", 1)[-1])
    out: list[dict[str, Any]] = []
    first_line: dict[str, int] = {}
    for name in sorted(by_service):
        findings: dict[str, dict[str, Any]] = {}
        for no, raw in by_service[name]:
            line = _TIMESTAMP.sub("", raw).rstrip()
            for rule, level, pattern in _LOG_RULES:
                if pattern.search(line):
                    f = findings.setdefault(
                        rule,
                        {
                            "rule": rule,
                            "level": level,
                            "message": line[:300],
                            "lines": [],
                            "count": 0,
                        },
                    )
                    f["count"] += 1
                    if len(f["lines"]) < 5:
                        f["lines"].append(no)
                    if level == "error":
                        first_line[name] = min(first_line.get(name, no), no)
                    break
        svc = services.get(name)
        out.append(
            {
                "service": name,
                "declared": bool(svc),
                "code": svc["build"] if svc else None,
                "health": svc["health"] if svc else None,
                "depends_on": sorted(deps.get(name, [])),
                "findings": sorted(
                    findings.values(), key=lambda f: (f["level"] != "error", f["lines"][0])
                ),
            }
        )
    failing = sorted(first_line, key=lambda n: first_line[n])
    return {"services": out, "failing": failing, "first_failing": failing[0] if failing else None}


def validation_sequence(infra: dict[str, Any]) -> list[dict[str, str]]:
    """Dry runs first, then build, then dependencies before dependents, checking health. Commands only."""
    steps: list[dict[str, str]] = []
    files = sorted({(r["kind"], r["file"]) for r in infra["resources"]})
    for kind, f in files:
        if kind == "compose-service":
            steps.append(
                {
                    "step": "validate",
                    "command": f"docker compose -f {f} config --quiet",
                    "why": "the file parses and resolves",
                }
            )
    for f in sorted({f for k, f in files if k.startswith("tf-")}):
        folder = str(Path(f).parent)
        steps.append(
            {
                "step": "validate",
                "command": f"terraform -chdir={folder} validate",
                "why": "Terraform configuration is valid",
            }
        )
    for f in sorted({f for k, f in files if k.startswith("k8s-")}):
        steps.append(
            {
                "step": "validate",
                "command": f"kubectl apply --dry-run=client -f {f}",
                "why": "manifests are well formed; nothing is applied",
            }
        )
    services = [r for r in infra["resources"] if r["kind"] == "compose-service"]
    deps = defaultdict(set)
    for e in infra["edges"]:
        if e["via"].startswith("depends_on"):
            deps[e["from"]].add(e["to"])
    for s in services:
        if s["build"]:
            steps.append(
                {
                    "step": "build",
                    "command": f"docker compose -f {s['file']} build {s['name']}",
                    "why": "the image builds",
                }
            )
    placed: list[str] = []
    pending = [s["id"] for s in services]
    while pending:
        ready = [i for i in pending if deps[i] <= set(placed)] or [pending[0]]
        for i in ready:
            s = next(x for x in services if x["id"] == i)
            steps.append(
                {
                    "step": "start",
                    "command": f"docker compose -f {s['file']} up -d {s['name']}",
                    "why": "after what it depends on",
                }
            )
            steps.append(
                {
                    "step": "check",
                    "command": f"docker compose -f {s['file']} ps {s['name']}",
                    "why": "healthy before the next one starts"
                    if s["health"]
                    else "running before the next one starts; it declares no health check, so read its logs",
                }
            )
            placed.append(i)
            pending.remove(i)
    return steps


def to_markdown(infra: dict[str, Any], log: dict[str, Any] | None = None) -> str:
    lines = [
        "# Infrastructure",
        "",
        f"{len(infra['resources'])} resources and {len(infra['edges'])} links in {', '.join(infra['projects'])}.",
        "",
        "| Resource | Kind | Where |",
        "|---|---|---|",
    ]
    lines += [
        f"| {r['name']} | {r['kind']} | {r['file']}:{r['line']} |" for r in infra["resources"]
    ]
    if infra["edges"]:
        lines += ["", "## Links", ""] + [
            f"- {e['from']} → {e['to']} ({e['via']}, {e['evidence']})" for e in infra["edges"]
        ]
    if infra["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in infra["warnings"]]
    if log:
        lines += ["", "## From the log", ""]
        if log["first_failing"]:
            lines.append(f"First failing service: **{log['first_failing']}**.")
        for s in log["services"]:
            if s["findings"]:
                lines.append(
                    f"- **{s['service']}** (code {s['code'] or 'unknown'}; health check {s['health'] or 'none'}; depends on {', '.join(s['depends_on']) or 'nothing'}):"
                )
                lines += [
                    f"  - {f['rule']} x{f['count']}, lines {', '.join(map(str, f['lines']))}: {f['message']}"
                    for f in s["findings"][:5]
                ]
    lines += ["", "## A safe order to check it in", ""]
    lines += [
        f"{n}. `{s['command']}`: {s['why']}" for n, s in enumerate(validation_sequence(infra), 1)
    ]
    return "\n".join(lines).replace("<", "&lt;") + "\n"


__all__ = ["SCHEMA", "scan", "to_markdown", "triage_log", "validation_sequence"]

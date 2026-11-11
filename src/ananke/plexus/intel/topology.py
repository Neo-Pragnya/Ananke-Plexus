"""Several repositories as one system (three-repo plan: CAP-multi-repository-topology-01..04).

* **Workspace** (``.ananke/workspace.json`` in the folder you work from): the repositories that belong together,
  each pinned to the Git revision it was added or last pinned at. A report says which have moved since.
* **Identity**: each repository's package names (``pyproject.toml``, ``package.json``), the services it declares
  (Compose, Kubernetes) and the routes it serves. Hosts that no repository serves (``api.stripe.com``) become
  *external* boundary nodes.
* **Calls across repositories**: an HTTP call in one repository is matched to a route another serves, by method and
  path shape, and by host when the URL names a service (``http://orders:8000``).
* **Change and mismatch**: routes in a repository's files that changed since its pin, the calls elsewhere that reach
  them, and calls to paths no repository serves.

Only files and Git are read; nothing is contacted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ananke.plexus.intel._api_common import shape

SCHEMA = "topology/v1"
WORKSPACE = Path(".ananke") / "workspace.json"


def _git(root: Path, *args: str) -> str | None:
    argv = ["git", "-C", str(root), *args]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    return done.stdout.strip() if done.returncode == 0 else None


def load_workspace(home: Path) -> dict[str, Any]:
    path = home / WORKSPACE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "ananke/workspace@1", "roots": []}
    return (
        data
        if isinstance(data, dict) and isinstance(data.get("roots"), list)
        else {"schema": "ananke/workspace@1", "roots": []}
    )


def pin(home: Path, roots: list[Path]) -> dict[str, Any]:
    """Add or re-pin repositories in the workspace at their current revision."""
    ws = load_workspace(home)
    by_path = {r["path"]: r for r in ws["roots"]}
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise ValueError(f"{root} isn't a folder")
        by_path[str(root)] = {
            "path": str(root),
            "name": root.name,
            "pin": _git(root, "rev-parse", "HEAD"),
        }
    ws["roots"] = sorted(by_path.values(), key=lambda r: r["name"])
    target = home / WORKSPACE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ws, indent=2) + "\n", encoding="utf-8")
    return ws


def _packages(root: Path) -> list[str]:
    names = []
    try:
        import tomllib

        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        if (data.get("project") or {}).get("name"):
            names.append(str(data["project"]["name"]))
    except (OSError, ValueError):
        pass
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if isinstance(pkg, dict) and pkg.get("name"):
            names.append(str(pkg["name"]))
    except (OSError, ValueError):
        pass
    return names


def _matches(route: tuple[str, ...], call: tuple[str, ...]) -> bool:
    return len(route) == len(call) and all(
        r == "{}" or c == "{}" or r == c for r, c in zip(route, call, strict=True)
    )


def build(roots: list[dict[str, Any]]) -> dict[str, Any]:
    """The topology of the given workspace roots (dicts with path, name and an optional pin)."""
    from ananke.plexus import intel
    from ananke.plexus.intel.infra import scan

    repos = []
    for r in roots:
        root = Path(r["path"])
        api = (intel.analyze(root).get("sections") or {}).get("api") or {}
        head = _git(root, "rev-parse", "HEAD")
        changed = (
            (_git(root, "diff", "--name-only", f"{r['pin']}...HEAD") or "").split()
            if r.get("pin") and head and r["pin"] != head
            else []
        )
        repos.append(
            {
                "name": r["name"],
                "path": str(root),
                "pin": r.get("pin"),
                "head": head,
                "moved": bool(r.get("pin") and head and r["pin"] != head),
                "packages": _packages(root),
                "routes": [
                    {"method": x["method"], "path": x["path"], "evidence": x.get("evidence", "")}
                    for x in api.get("routes") or []
                ],
                "clients": api.get("clients") or [],
                "changed_files": changed,
            }
        )
    infra = scan([Path(r["path"]) for r in roots])
    services: dict[str, str] = {}
    for res in infra["resources"]:
        if res["kind"] in ("compose-service", "k8s-deployment", "k8s-service"):
            services[res["name"]] = res["project"]
    for repo in repos:
        repo["services"] = sorted(n for n, p in services.items() if p == Path(repo["path"]).name)
    links: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    externals: dict[str, list[str]] = {}
    for caller in repos:
        for c in caller["clients"]:
            host, path = c.get("host"), c.get("path") or ""
            if host and host not in services:
                externals.setdefault(host, []).append(f"{caller['name']}:{c.get('evidence')}")
                continue
            candidates = [
                r
                for r in repos
                if r is not caller and (not host or services.get(host) == Path(r["path"]).name)
            ]
            hit = None
            for server in candidates:
                for route in server["routes"]:
                    if (route["method"] in (c.get("method"), "ANY")) and _matches(
                        shape(route["path"]), shape(path)
                    ):
                        hit = (server, route)
                        break
                if hit:
                    break
            own = any(_matches(shape(rt["path"]), shape(path)) for rt in caller["routes"])
            if hit:
                server, route = hit
                route_file = route["evidence"].rsplit(":", 1)[0]
                links.append(
                    {
                        "from": f"{caller['name']}:{c.get('evidence')}",
                        "to": f"{server['name']}:{route['evidence']}",
                        "call": f"{c.get('method')} {path}",
                        "route": f"{route['method']} {route['path']}",
                        "route_changed_since_pin": route_file in server["changed_files"],
                    }
                )
            elif not own:  # no repository in the workspace serves it, the caller included
                mismatches.append(
                    {
                        "call": f"{c.get('method')} {path}",
                        "from": f"{caller['name']}:{c.get('evidence')}",
                        "host": host,
                    }
                )
    return {
        "schema": SCHEMA,
        "repositories": [{k: v for k, v in r.items() if k not in ("clients",)} for r in repos],
        "links": links,
        "impacted_by_changes": [lk for lk in links if lk["route_changed_since_pin"]],
        "mismatches": mismatches,
        "external": [{"host": h, "callers": sorted(v)} for h, v in sorted(externals.items())],
        "infra_links": infra["cross_project"],
    }


def to_markdown(t: dict[str, Any]) -> str:
    lines = [
        "# Topology",
        "",
        "| Repository | Packages | Services | Routes | Pinned | Moved |",
        "|---|---|---|---|---|---|",
    ]
    for r in t["repositories"]:
        lines.append(
            f"| {r['name']} | {', '.join(r['packages']) or '—'} | {', '.join(r['services']) or '—'} | {len(r['routes'])} | "
            f"{(r['pin'] or 'not pinned')[:10]} | {'yes' if r['moved'] else 'no'} |"
        )
    lines += ["", "## Calls between repositories", ""]
    lines += [
        f"- {lk['from']} → {lk['to']} (`{lk['call']}` → `{lk['route']}`)"
        + (" **route changed since pin**" if lk["route_changed_since_pin"] else "")
        for lk in t["links"]
    ] or ["- none found"]
    if t["mismatches"]:
        lines += ["", "## Calls no repository serves", ""] + [
            f"- `{m['call']}` from {m['from']}" + (f" (host {m['host']})" if m["host"] else "")
            for m in t["mismatches"]
        ]
    if t["external"]:
        lines += ["", "## External systems", ""] + [
            f"- {e['host']}: called from {', '.join(e['callers'][:3])}" for e in t["external"]
        ]
    return "\n".join(lines).replace("<", "&lt;") + "\n"


__all__ = ["SCHEMA", "WORKSPACE", "build", "load_workspace", "pin", "to_markdown"]

"""Repo → running environment drift (ADV09): what the repository declares against what is running.

**Declared** (read from the repository): Compose services and Kubernetes workloads and services (images, replicas,
ports, environment variable names), Terraform resources, and the API operations the code serves.

**Observed** (files you capture, so nothing here needs access to the environment): ``kubectl get ... -o json``,
``docker inspect``, ``docker compose ps --format json``, ``terraform show -json`` or a ``.tfstate`` file, and the
OpenAPI document a running service serves (a file, or a URL on this machine only).

**Drift**, each item naming both sides: declared but not running, running but not declared, a different image or
tag, replicas, ports or environment variable names, Terraform resources in code but not in state and the reverse, and
operations the code serves that the running service doesn't (or the reverse). Environment variable **values are never
read**, only names. Every item is observed from the two inputs; how old the snapshot is, is stated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ananke.plexus.insight import InsightError, digest

SCHEMA = "ananke/drift@1"
MAX_INPUT_BYTES = 100_000_000
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "CronJob", "Job"}
_TF_RESOURCE = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"')


def _image_parts(image: str) -> tuple[str, str]:
    image = image.split("@", 1)[0]
    name, _, tag = (
        image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "latest")
    )
    return name, tag or "latest"


def _env_names(env: Any) -> list[str]:
    if isinstance(env, dict):
        return sorted(str(k) for k in env)
    out = []
    for item in env or []:
        if isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]))
        elif isinstance(item, str):
            out.append(item.split("=", 1)[0])
    return sorted(out)


# ---------------------------------------------------------------- declared


def declared(root: Path) -> dict[str, Any]:
    from ananke.plexus.intel.context import RepoContext

    ctx = RepoContext.open(root)
    services: dict[str, dict[str, Any]] = {}
    tf: dict[str, str] = {}
    for rel in ctx.files:
        name = rel.rsplit("/", 1)[-1]
        if name.endswith((".yml", ".yaml")):
            text = ctx.text(rel)
            if not text or ("services:" not in text and "kind:" not in text) or "{{" in text:
                continue  # not a manifest, or a Helm template that only renders with values
            try:
                docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
            except (yaml.YAMLError, RecursionError, ValueError):
                continue
            for d in docs:
                if "compose" in name and isinstance(d.get("services"), dict):
                    for svc, spec in d["services"].items():
                        if isinstance(spec, dict):
                            services[str(svc)] = {
                                "name": str(svc),
                                "source": "compose",
                                "evidence": rel,
                                "images": [spec["image"]]
                                if isinstance(spec.get("image"), str)
                                else [],
                                "built": spec.get("build") is not None,
                                "replicas": (
                                    (spec.get("deploy") or {}).get("replicas")
                                    if isinstance(spec.get("deploy"), dict)
                                    else None
                                ),
                                "ports": sorted(
                                    str(p).split(":")[-1].split("/")[0]
                                    for p in spec.get("ports") or []
                                ),
                                "env": _env_names(spec.get("environment")),
                            }
                elif d.get("kind") in _WORKLOADS | {"Service"}:
                    services.update(_k8s_declared(d, rel))
        elif rel.endswith(".tf"):
            for line in (ctx.text(rel) or "").splitlines():
                m = _TF_RESOURCE.match(line)
                if m:
                    tf[f"{m.group(1)}.{m.group(2)}"] = rel
    return {"services": services, "terraform": tf}


def _k8s_declared(d: dict[str, Any], rel: str) -> dict[str, dict[str, Any]]:
    kind = str(d.get("kind"))
    meta = d.get("metadata") or {}
    name = str(meta.get("name") or "")
    if not name:
        return {}
    spec = d.get("spec") or {}
    if kind == "Service":
        ports = sorted(str(p.get("port")) for p in spec.get("ports") or [] if isinstance(p, dict))
        return {
            f"service/{name}": {
                "name": f"service/{name}",
                "source": "kubernetes",
                "evidence": rel,
                "images": [],
                "built": False,
                "replicas": None,
                "ports": ports,
                "env": [],
            }
        }
    pod = _pod_spec(d)
    containers = [c for c in pod.get("containers") or [] if isinstance(c, dict)]
    return {
        f"{kind.lower()}/{name}": {
            "name": f"{kind.lower()}/{name}",
            "source": "kubernetes",
            "evidence": rel,
            "images": sorted(str(c.get("image")) for c in containers if c.get("image")),
            "built": False,
            "replicas": spec.get("replicas")
            if kind in {"Deployment", "StatefulSet", "ReplicaSet"}
            else None,
            "ports": sorted(
                str(p.get("containerPort"))
                for c in containers
                for p in c.get("ports") or []
                if isinstance(p, dict) and p.get("containerPort")
            ),
            "env": sorted({n for c in containers for n in _env_names(c.get("env"))}),
        }
    }


def _pod_spec(d: dict[str, Any]) -> dict[str, Any]:
    spec = d.get("spec") or {}
    if d.get("kind") == "Pod":
        return spec
    if d.get("kind") == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
    return ((spec.get("template") or {}).get("spec")) or {}


# ---------------------------------------------------------------- observed


def _load(path: Path) -> Any:
    if not path.is_file():
        raise InsightError(f"{path} doesn't exist")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise InsightError(f"{path.name} is over {MAX_INPUT_BYTES // 1_000_000} MB")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        rows = []
        for line in text.splitlines():  # docker compose ps --format json writes one object per line
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise InsightError(f"{path.name} isn't JSON or JSON lines: {exc}") from exc
        return rows


def observed(snapshot: Path) -> tuple[str, dict[str, Any]]:
    """What a captured file says is running, keyed like the declared side, and which tool wrote it."""
    data = _load(snapshot)
    if (isinstance(data, dict) and data.get("kind") == "List") or (
        isinstance(data, dict) and "items" in data
    ):
        out: dict[str, dict[str, Any]] = {}
        for item in data.get("items") or []:
            if isinstance(item, dict) and item.get("kind") in _WORKLOADS | {"Service"}:
                for key, row in _k8s_declared(item, snapshot.name).items():
                    status = item.get("status") or {}
                    row["ready"] = status.get("readyReplicas")
                    out[key] = row
        return "kubectl", out
    if isinstance(data, dict) and ("resources" in data or "values" in data):
        return "terraform", {"terraform": _tf_state(data)}
    if isinstance(data, list) and data and isinstance(data[0], dict) and "Config" in data[0]:
        return "docker inspect", {
            _docker_name(c): _docker_row(c, snapshot.name) for c in data if isinstance(c, dict)
        }
    if (
        isinstance(data, list)
        and data
        and isinstance(data[0], dict)
        and ("Service" in data[0] or "Name" in data[0])
    ):
        out = {}
        for c in data:
            name = str(c.get("Service") or c.get("Name") or "")
            ports = sorted(
                {
                    str(p.get("TargetPort"))
                    for p in c.get("Publishers") or []
                    if isinstance(p, dict) and p.get("TargetPort")
                }
            )
            out[name] = {
                "name": name,
                "source": "compose ps",
                "evidence": snapshot.name,
                "images": [str(c.get("Image"))] if c.get("Image") else [],
                "replicas": None,
                "ports": ports,
                "env": None,
                "state": c.get("State"),
            }
        return "docker compose ps", out
    raise InsightError(
        f"{snapshot.name}: not a recognised snapshot; give `kubectl get ... -o json`, `docker inspect`, "
        "`docker compose ps --format json` or `terraform show -json` output"
    )


def _docker_name(c: dict[str, Any]) -> str:
    labels = (c.get("Config") or {}).get("Labels") or {}
    return str(labels.get("com.docker.compose.service") or str(c.get("Name") or "").lstrip("/"))


def _docker_row(c: dict[str, Any], evidence: str) -> dict[str, Any]:
    cfg = c.get("Config") or {}
    exposed = cfg.get("ExposedPorts") or {}
    return {
        "name": _docker_name(c),
        "source": "docker inspect",
        "evidence": evidence,
        "images": [str(cfg.get("Image"))] if cfg.get("Image") else [],
        "replicas": None,
        "ports": sorted(str(p).split("/")[0] for p in exposed),
        "env": _env_names(cfg.get("Env")),
        "state": (c.get("State") or {}).get("Status"),
    }


def _tf_state(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in data.get("resources") or []:  # a .tfstate file
        if isinstance(r, dict) and r.get("mode") == "managed":
            out[f"{r.get('type')}.{r.get('name')}"] = str(r.get("module") or "")

    def walk(module: dict[str, Any]) -> None:  # terraform show -json
        for r in module.get("resources") or []:
            if isinstance(r, dict) and r.get("mode") == "managed":
                out[f"{r.get('type')}.{r.get('name')}"] = str(module.get("address") or "")
        for child in module.get("child_modules") or []:
            if isinstance(child, dict):
                walk(child)

    root_module = (
        ((data.get("values") or {}).get("root_module"))
        if isinstance(data.get("values"), dict)
        else None
    )
    if isinstance(root_module, dict):
        walk(root_module)
    return out


def served_operations(source: str) -> tuple[set[tuple[str, str]], str]:
    """Operations in an OpenAPI document: a file, or a URL on this machine (fetched once, no redirects)."""
    if source.startswith(("http://", "https://")):
        host = urlparse(source).hostname or ""
        if host not in _LOOPBACK:
            raise InsightError(
                f"{host} isn't this machine; only 127.0.0.1, localhost or ::1 are fetched"
            )
        import urllib.request

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(source, timeout=10) as resp:
                doc = json.loads(resp.read(MAX_INPUT_BYTES))
        except (OSError, ValueError) as exc:
            raise InsightError(f"couldn't read OpenAPI from {source}: {exc}") from exc
        label = source
    else:
        from ananke.plexus.intel.api_contract import ApiContractError, load_spec

        try:
            doc = load_spec(Path(source))
        except ApiContractError as exc:
            raise InsightError(str(exc)) from exc
        label = Path(source).name
    ops = set()
    for path, item in (doc.get("paths") or {}).items():
        if isinstance(item, dict):
            for method in item:
                if method.lower() in {"get", "put", "post", "delete", "patch", "head", "options"}:
                    ops.add((method.upper(), _shape(str(path))))
    return ops, label


def _shape(path: str) -> str:
    return "/" + "/".join(
        "{}" if s.startswith(("{", ":", "<")) else s for s in path.strip("/").split("/") if s
    )


# ---------------------------------------------------------------- compare


def compare(root: Path, snapshots: list[Path], openapi: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not snapshots and not openapi:
        raise InsightError(
            "give at least one snapshot (--snapshot FILE) or a running service's OpenAPI (--openapi)"
        )
    want = declared(root)
    drift: list[dict[str, Any]] = []
    inputs = []
    for snap in snapshots:
        tool, running = observed(snap)
        inputs.append({"file": snap.name, "tool": tool})
        if tool == "terraform":
            state = running["terraform"]
            for addr, where in sorted(want["terraform"].items()):
                if addr not in state:
                    drift.append(
                        _item(
                            "in code, not in state",
                            addr,
                            where,
                            snap.name,
                            "declared but not applied",
                        )
                    )
            for addr in sorted(set(state) - set(want["terraform"])):
                drift.append(
                    _item(
                        "in state, not in code",
                        addr,
                        "",
                        snap.name,
                        "applied but not in the repository",
                    )
                )
            continue
        declared_here = {
            k: v
            for k, v in want["services"].items()
            if (tool == "kubectl") == (v["source"] == "kubernetes")
        }
        for key, spec in sorted(declared_here.items()):
            live = running.get(key)
            if live is None:
                drift.append(
                    _item(
                        "declared, not running",
                        key,
                        spec["evidence"],
                        snap.name,
                        "not in the snapshot",
                    )
                )
                continue
            drift += _differences(key, spec, live, snap.name)
        for key in sorted(set(running) - set(declared_here)):
            drift.append(
                _item(
                    "running, not declared",
                    key,
                    "",
                    snap.name,
                    "not in any manifest in the repository",
                )
            )
    api_summary = None
    if openapi:
        from ananke.plexus import intel

        served, label = served_operations(openapi)
        routes = (intel.analyze(root).get("sections") or {}).get("api", {}).get("routes") or []
        code = {
            (r["method"], _shape(r["path"])) for r in routes if r.get("method") and r.get("path")
        }
        inputs.append({"file": label, "tool": "openapi"})
        for m, p in sorted(code - served):
            if m != "ANY":
                drift.append(
                    _item(
                        "served by code, not by the running service",
                        f"{m} {p}",
                        "routes",
                        label,
                        "the deployed version may be older",
                    )
                )
        for m, p in sorted(served - code):
            drift.append(
                _item(
                    "served by the running service, not by code",
                    f"{m} {p}",
                    "",
                    label,
                    "the deployed version may be newer or another service",
                )
            )
        api_summary = {"code": len(code), "served": len(served), "both": len(code & served)}
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "inputs": inputs,
        "declared": {"services": len(want["services"]), "terraform": len(want["terraform"])},
        "api": api_summary,
        "summary": {"drift": len(drift), "by_kind": _count(drift)},
        "drift": drift,
        "limits": [
            "The running side is what the snapshot recorded when it was taken; take a fresh one before acting.",
            "Environment variable values are never read, only their names.",
            "A service built from source (build:) has no image to compare until it's tagged.",
        ],
    }
    report["digest"] = digest(report)
    return report


def _item(kind: str, name: str, declared_at: str, observed_in: str, detail: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "declared": declared_at,
        "observed": observed_in,
        "detail": detail,
        "status": "observed",
    }


def _differences(
    key: str, spec: dict[str, Any], live: dict[str, Any], snap: str
) -> list[dict[str, Any]]:
    out = []
    if spec["images"] and live.get("images"):
        want = {_image_parts(i) for i in spec["images"]}
        have = {_image_parts(i) for i in live["images"]}
        if want != have:
            out.append(
                _item(
                    "image differs",
                    key,
                    spec["evidence"],
                    snap,
                    f"declared {', '.join(sorted(spec['images']))}; running {', '.join(sorted(live['images']))}",
                )
            )
    if (
        spec.get("replicas") is not None
        and live.get("replicas") is not None
        and spec["replicas"] != live["replicas"]
    ):
        out.append(
            _item(
                "replicas differ",
                key,
                spec["evidence"],
                snap,
                f"declared {spec['replicas']}; running {live['replicas']}",
            )
        )
    if live.get("ready") is not None and live.get("replicas") and live["ready"] < live["replicas"]:
        out.append(
            _item(
                "not all replicas ready",
                key,
                spec["evidence"],
                snap,
                f"{live['ready']} of {live['replicas']} ready",
            )
        )
    if spec["ports"] and live.get("ports") and set(spec["ports"]) != set(live["ports"]):
        out.append(
            _item(
                "ports differ",
                key,
                spec["evidence"],
                snap,
                f"declared {', '.join(spec['ports'])}; running {', '.join(live['ports'])}",
            )
        )
    if live.get("env") is not None and spec["env"]:
        missing = sorted(set(spec["env"]) - set(live["env"]))
        if missing:
            out.append(
                _item(
                    "environment variables missing",
                    key,
                    spec["evidence"],
                    snap,
                    f"declared but not set: {', '.join(missing)}",
                )
            )
    if live.get("state") and str(live["state"]).lower() not in {"running", "up"}:
        out.append(_item("not running", key, spec["evidence"], snap, f"state {live['state']}"))
    return out


def _count(items: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i["kind"]] = out.get(i["kind"], 0) + 1
    return dict(sorted(out.items()))


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Drift between the repository and what is running",
        "",
        "Compared with: " + ", ".join(f"{i['file']} ({i['tool']})" for i in report["inputs"]) + ".",
        "",
        f"{report['summary']['drift']} differences." if report["drift"] else "No drift found.",
        "",
    ]
    if report["drift"]:
        lines += [
            "| Drift | What | Declared in | Observed in | Detail |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {d['kind']} | `{d['name']}` | {d['declared'] or '-'} | {d['observed']} | {d['detail']} |"
            for d in report["drift"]
        ]
    if report["api"]:
        a = report["api"]
        lines += [
            "",
            f"API: {a['code']} operations in code, {a['served']} served, {a['both']} in both.",
        ]
    lines += [
        "",
        "## Limits",
        "",
        *(f"- {x}" for x in report["limits"]),
        "",
        f"Digest: `{report['digest']}`",
        "",
    ]
    return "\n".join(lines)

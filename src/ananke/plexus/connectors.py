"""Built-in typed connectors: graph, test results, API docs and build logs as capabilities a workflow can use
(three-repo plan: ASM-A09).

Each connector has typed input and output ports, so workflow assembly can connect it, find it for a gap and explain
it, like any registered capability. Two rules:

* **Separate process.** A connector runs in its own isolated Python process (``python -I``, an environment with
  only what it needs to start, no network variables), reads only the input it's given, and writes nothing.
* **Verified output.** The parent checks the output against the port's schema before writing it. Output that fails
  is an error, never a file.

    ananke connector list
    ananke connector run junit-to-sarif results.xml -o results.sarif
    ananke connector register --approve        # add them to the registry as tools with typed ports

They need nothing outside the standard library and Ananke, and none of them uses the network.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
NAMESPACE = "ananke"
TIMEOUT = 120
MAX_INPUT = 50_000_000


class ConnectorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Connector:
    name: str
    summary: str
    inputs: tuple[tuple[str, str], ...]
    outputs: tuple[tuple[str, str], ...]
    convert: Callable[[Path], Any]
    verify: Callable[[Any], list[str]]


# ------------------------------------------------------------------------------------------------- converters
def _code_graph(source: Path) -> dict[str, Any]:
    from ananke.plexus.graph.service import build_graph

    if not source.is_dir():
        raise ConnectorError("INVALID_INPUT", f"{source} isn't a folder of source code")
    snap = build_graph(source)
    return {
        "schema": "code-graph/v1",
        "provider": snap.provider,
        "nodes": [n.model_dump(mode="json") for n in snap.nodes],
        "edges": [e.model_dump(mode="json") for e in snap.edges],
    }


def _verify_graph(out: Any) -> list[str]:
    if not isinstance(out, dict) or out.get("schema") != "code-graph/v1":
        return ["not a code-graph/v1 document"]
    ids = {n.get("node_id") for n in out.get("nodes", [])}
    dangling = [e for e in out.get("edges", []) if e.get("source") not in ids]
    return [f"{len(dangling)} edges start at a node that isn't in the graph"] if dangling else []


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ConnectorError("INVALID_INPUT", f"{path}: no such file")
    if path.stat().st_size > MAX_INPUT:
        raise ConnectorError("INVALID_INPUT", f"{path} is larger than {MAX_INPUT // 1_000_000} MB")
    return path.read_text(encoding="utf-8", errors="replace")


def _first_line(text: str | None, default: str) -> str:
    text = (text or "").strip()
    return text.splitlines()[0][:500] if text else default


def _junit_to_sarif(report: Path) -> dict[str, Any]:
    text = _read_text(report)
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ConnectorError("INVALID_INPUT", "a JUnit report with a DTD or entities isn't read")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs and entities refused above
    except ET.ParseError as exc:
        raise ConnectorError("INVALID_INPUT", f"{report} isn't JUnit XML: {exc}") from exc
    results: list[dict[str, Any]] = []
    total = 0
    for case in root.iter("testcase"):
        total += 1
        for tag, level in (("failure", "error"), ("error", "error"), ("skipped", "note")):
            found = case.find(tag)
            if found is None:
                continue
            where = case.get("file") or (case.get("classname") or "").replace(".", "/") + ".py"
            line = case.get("line")
            location: dict[str, Any] = {"artifactLocation": {"uri": where}}
            if line and line.isdigit():
                location["region"] = {"startLine": int(line)}
            message = _first_line(found.get("message") or found.text, tag)
            results.append(
                {
                    "ruleId": f"test-{tag}",
                    "level": level,
                    "message": {
                        "text": f"{case.get('classname', '')}.{case.get('name', '')}: {message}"
                    },
                    "locations": [{"physicalLocation": location}],
                }
            )
    if total == 0:
        raise ConnectorError("INVALID_INPUT", f"{report} has no <testcase> elements")
    rules = [{"id": "test-failure"}, {"id": "test-error"}, {"id": "test-skipped"}]
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {"name": "ananke junit-to-sarif", "version": VERSION, "rules": rules}
                },
                "results": results,
                "properties": {"tests": total},
            }
        ],
    }


def _verify_sarif(out: Any) -> list[str]:
    if not isinstance(out, dict) or out.get("version") != "2.1.0":
        return ["not SARIF 2.1.0"]
    if not out.get("runs"):
        return ["no runs"]
    problems = []
    for run_ in out["runs"]:
        if not run_.get("tool", {}).get("driver", {}).get("name"):
            problems.append("a run has no tool.driver.name")
        for r in run_.get("results", []):
            if not r.get("message", {}).get("text") or r.get("level") not in (
                "error",
                "warning",
                "note",
                "none",
            ):
                problems.append("a result has no message text or an invalid level")
                break
    return problems


_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def _openapi_routes(spec: Path) -> dict[str, Any]:
    text = _read_text(spec)
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            import yaml

            doc = yaml.safe_load(text)
        except Exception as exc:
            raise ConnectorError(
                "INVALID_INPUT", f"{spec} is neither JSON nor YAML: {exc}"
            ) from exc
    if not isinstance(doc, dict) or not str(doc.get("openapi", "")).startswith("3"):
        raise ConnectorError(
            "INVALID_INPUT", f"{spec} isn't an OpenAPI 3 document (no 'openapi: 3.x')"
        )
    routes = []
    for path, item in sorted((doc.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        shared = [p for p in item.get("parameters") or [] if isinstance(p, dict)]
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = [p for p in op.get("parameters") or [] if isinstance(p, dict)] + shared
            routes.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": op.get("operationId", ""),
                    "summary": str(op.get("summary") or "")[:200],
                    "parameters": sorted({str(p["name"]) for p in params if p.get("name")}),
                    "responses": sorted(str(code) for code in op.get("responses") or {}),
                }
            )
    info = doc.get("info") or {}
    return {
        "schema": "api-routes/v1",
        "title": info.get("title", ""),
        "version": str(info.get("version", "")),
        "routes": routes,
    }


def _verify_routes(out: Any) -> list[str]:
    if not isinstance(out, dict) or out.get("schema") != "api-routes/v1":
        return ["not an api-routes/v1 document"]
    methods = {m.upper() for m in _METHODS}
    bad = [
        r
        for r in out.get("routes", [])
        if r.get("method") not in methods or not str(r.get("path", "")).startswith("/")
    ]
    return [f"{len(bad)} routes have no valid method or path"] if bad else []


_LOG_RULES = (
    ("build-failed", "error", re.compile(r"^Finished: (FAILURE|ABORTED|UNSTABLE)\b")),
    ("python-traceback", "error", re.compile(r"^Traceback \(most recent call last\):")),
    (
        "out-of-memory",
        "error",
        re.compile(r"\bOOMKilled\b|\bOut of memory\b|\bMemoryError\b", re.I),
    ),
    (
        "exit-code",
        "error",
        re.compile(
            r"\b(exit(?:ed)? (?:with )?(?:code|status)|returned non-zero exit status) [1-9]\d*",
            re.I,
        ),
    ),
    ("error-line", "error", re.compile(r"(^|\s|\[)(ERROR|FATAL|CRITICAL)(\]|:|\s)")),
    ("warning-line", "warning", re.compile(r"(^|\s|\[)(WARN|WARNING)(\]|:|\s)")),
)
_TIMESTAMP = re.compile(r"^\s*(\[?\d{4}-\d\d-\d\d[T ][\d:.,]+Z?\]?|\d\d:\d\d:\d\d(\.\d+)?)\s*")


def _log_triage(log: Path) -> dict[str, Any]:
    lines = _read_text(log).splitlines()
    marks = ("Started by ", "Finished: ", "[Pipeline]")
    kind = (
        "jenkins"
        if any(line.startswith(marks) for line in lines[:200] + lines[-20:])
        else "container"
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for no, raw in enumerate(lines, start=1):
        line = _TIMESTAMP.sub("", raw).rstrip()
        for rule, level, pattern in _LOG_RULES:
            if pattern.search(line):
                key = (rule, re.sub(r"\d+", "N", line)[:160])
                entry = grouped.setdefault(
                    key,
                    {"rule": rule, "level": level, "message": line[:300], "lines": [], "count": 0},
                )
                entry["count"] += 1
                if len(entry["lines"]) < 5:
                    entry["lines"].append(no)
                break
    findings = sorted(grouped.values(), key=lambda f: (f["level"] != "error", f["lines"][0]))
    return {"schema": "findings/v1", "source": kind, "lines": len(lines), "findings": findings}


def _verify_findings(out: Any) -> list[str]:
    if not isinstance(out, dict) or out.get("schema") != "findings/v1":
        return ["not a findings/v1 document"]
    bad = [
        f
        for f in out.get("findings", [])
        if f.get("level") not in ("error", "warning") or not f.get("lines")
    ]
    return [f"{len(bad)} findings have no level or no line"] if bad else []


# what each connector does, as the registry's hierarchical capability id
CAPABILITY_IDS = {
    "code-graph": "graph.export",
    "junit-to-sarif": "test.report",
    "openapi-routes": "api.routes",
    "log-triage": "logs.triage",
}

CONNECTORS: dict[str, Connector] = {
    c.name: c
    for c in (
        Connector(
            "code-graph",
            "Python and notebook source to Ananke's native code graph",
            (("source", "python-source/v1"),),
            (("graph", "code-graph/v1"),),
            _code_graph,
            _verify_graph,
        ),
        Connector(
            "junit-to-sarif",
            "JUnit XML test results to SARIF 2.1.0: each failure, error and skip with its location",
            (("junit", "junit-xml/v1"),),
            (("sarif", "sarif/2.1.0"),),
            _junit_to_sarif,
            _verify_sarif,
        ),
        Connector(
            "openapi-routes",
            "An OpenAPI 3 document (JSON or YAML) to its routes: method, path, operation, parameters, responses",
            (("spec", "openapi/v3"),),
            (("routes", "api-routes/v1"),),
            _openapi_routes,
            _verify_routes,
        ),
        Connector(
            "log-triage",
            "A Jenkins console or container log to grouped findings: build result, tracebacks, OOM, exit codes",
            (("log", "build-log/v1"),),
            (("findings", "findings/v1"),),
            _log_triage,
            _verify_findings,
        ),
    )
}


def describe() -> list[dict[str, Any]]:
    return [
        {
            "ref": f"{NAMESPACE}/{c.name}@{VERSION}",
            "summary": c.summary,
            "inputs": [{"name": n, "schema_ref": s} for n, s in c.inputs],
            "outputs": [{"name": n, "schema_ref": s} for n, s in c.outputs],
        }
        for c in CONNECTORS.values()
    ]


def run(name: str, source: Path, *, timeout: int = TIMEOUT) -> Any:
    """Run a connector in its own isolated process, then verify what it returns. Raises ``ConnectorError``."""
    connector = CONNECTORS.get(name)
    if connector is None:
        raise ConnectorError(
            "UNKNOWN_CONNECTOR", f"no connector {name!r}; choose {', '.join(CONNECTORS)}"
        )
    env = {
        k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP") if k in os.environ
    }
    argv = [sys.executable, "-I", "-m", "ananke.plexus.connectors", name, str(source.resolve())]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv: this interpreter and this module
            argv, capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectorError("TIMEOUT", f"{name} took longer than {timeout}s") from exc
    if done.returncode != 0:
        try:
            err = json.loads(done.stdout)
        except ValueError:
            raise ConnectorError(
                "CONNECTOR_FAILED", (done.stderr or done.stdout).strip()[-400:]
            ) from None
        raise ConnectorError(
            str(err.get("code", "CONNECTOR_FAILED")), str(err.get("message", "failed"))
        )
    try:
        out = json.loads(done.stdout)
    except ValueError as exc:
        raise ConnectorError(
            "UNVERIFIED_OUTPUT", f"{name} returned something that isn't JSON"
        ) from exc
    problems = connector.verify(out)
    if problems:
        raise ConnectorError(
            "UNVERIFIED_OUTPUT", f"{name}'s output failed its check: {'; '.join(problems)}"
        )
    return out


def register(registry: Any, *, approve: bool = False) -> list[str]:
    """Add every connector to the registry as a tool with typed ports. Returns their refs."""
    from ananke.plexus.registry.errors import RegistryError
    from ananke.plexus.registry.importers.base import ImportedArtifact
    from ananke.plexus.registry.models import ArtifactManifest, Provenance, TrustStatus

    refs = []
    for c in CONNECTORS.values():
        ref = f"{NAMESPACE}/{c.name}@{VERSION}"
        try:
            registry.exact_version(ref)
        except RegistryError:
            manifest = ArtifactManifest.model_validate(
                {
                    "kind": "tool",
                    "namespace": NAMESPACE,
                    "name": c.name,
                    "version": VERSION,
                    "summary": c.summary,
                    "license": {"expression": "Apache-2.0"},
                    "capabilities": [CAPABILITY_IDS[c.name]],
                    "inputs": {
                        "native": {"ports": [{"name": n, "schema_ref": s} for n, s in c.inputs]}
                    },
                    "outputs": {
                        "native": {"ports": [{"name": n, "schema_ref": s} for n, s in c.outputs]}
                    },
                }
            )
            doc = f"# {c.name}\n\n{c.summary}.\n\nRun: `ananke connector run {c.name} INPUT -o OUTPUT`\n"
            registry.register(
                ImportedArtifact(
                    manifest=manifest,
                    files={"README.md": doc.encode()},
                    provenance=Provenance(
                        source_type="builtin", source_name=f"ananke.plexus.connectors:{c.name}"
                    ),
                )
            )
        if approve:
            registry.promote(ref, trust=TrustStatus.APPROVED, channel="stable", run_gate=False)
        refs.append(ref)
    return refs


def _child(argv: list[str]) -> int:
    """The isolated process: convert one input, print JSON, exit 1 with a coded error on failure."""
    name, source = argv[0], Path(argv[1])
    try:
        out = CONNECTORS[name].convert(source)
    except ConnectorError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message}))
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1:]))


__all__ = [
    "CONNECTORS",
    "NAMESPACE",
    "VERSION",
    "Connector",
    "ConnectorError",
    "describe",
    "register",
    "run",
]

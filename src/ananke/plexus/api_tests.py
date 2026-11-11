"""Contract tests from an OpenAPI 3 document, and evidence of running them (three-repo plan: CAP-api-testing-01..04).

* **Cases** for every operation: the positive case, a missing resource for each path parameter, a request without
  credentials when the operation is secured, an invalid body (a required field left out, a field of the wrong type),
  a response-time limit, and repeating an idempotent request (``PUT``, ``DELETE``).
* **Fixtures**: an example body built from each request schema (its ``example``, else required fields filled by type),
  and the **missing status codes**: operations that document no 4xx response for a failure they can have.
* **Running** them is a separate, explicit step against a service on this machine only (``127.0.0.1``, ``localhost``
  or ``::1``): the generated pytest file reads its base URL from ``ANANKE_API_BASE_URL``. Results are written as JUnit
  XML and summarised per case.
* **Suggested or executed**: every case in the report says whether it was only generated or was run, and if it ran,
  whether it passed. A generated test is never reported as a passing one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA = "api-test-plan/v1"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_METHODS = ("get", "put", "post", "delete", "patch")


class ApiTestError(ValueError):
    pass


def _load(spec: Path) -> dict[str, Any]:
    text = spec.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except ValueError:
        import yaml

        doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or not str(doc.get("openapi", "")).startswith("3"):
        raise ApiTestError(f"{spec} isn't an OpenAPI 3 document")
    return doc


def _resolve(doc: dict[str, Any], schema: Any, depth: int = 0) -> dict[str, Any]:
    if not isinstance(schema, dict) or depth > 8:
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = doc
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return _resolve(doc, node, depth + 1)
    return schema


def example(doc: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    """An example value for a schema: its own example, else something of the right type for each required field."""
    s = _resolve(doc, schema)
    if "example" in s:
        return s["example"]
    if s.get("enum"):
        return s["enum"][0]
    kind = s.get("type") or ("object" if "properties" in s else "string")
    if kind == "object" and depth < 6:
        props = s.get("properties") or {}
        return {k: example(doc, props[k], depth + 1) for k in s.get("required", []) if k in props}
    if kind == "array":
        return [example(doc, s.get("items", {}), depth + 1)]
    return {"integer": 1, "number": 1.5, "boolean": True}.get(kind, "example")


def _wrong(value: Any) -> Any:
    return (
        "not-a-number" if isinstance(value, (int, float)) and not isinstance(value, bool) else 12345
    )


def plan(doc: dict[str, Any]) -> dict[str, Any]:
    secured_by_default = bool(doc.get("security"))
    operations, missing = [], []
    for path, item in sorted((doc.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or re.sub(
                r"[^a-z0-9]+", "_", f"{method}_{path}".lower()
            ).strip("_")
            params = [
                p
                for p in (op.get("parameters") or []) + (item.get("parameters") or [])
                if isinstance(p, dict)
            ]
            declared = [p["name"] for p in params if p.get("in") == "path" and p.get("name")]
            # the path template is the source of truth: many documents don't declare their path parameters
            path_params = sorted(set(declared) | set(re.findall(r"\{([^}/]+)\}", path)))
            responses = sorted(str(c) for c in (op.get("responses") or {}))
            ok = next((c for c in responses if c.startswith("2")), "200")
            body_schema = (
                ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
            ).get("schema")
            body = example(doc, body_schema) if body_schema else None
            secured = (
                bool(op.get("security", doc.get("security")))
                if "security" in op
                else secured_by_default
            )
            cases = [{"case": "positive", "expect": [ok], "body": body}]
            for p in path_params:
                cases.append({"case": f"unknown {p}", "expect": ["404"], "param": p})
            if secured:
                cases.append({"case": "no credentials", "expect": ["401", "403"]})
            if isinstance(body, dict) and body:
                first = next(iter(body))
                cases.append(
                    {
                        "case": f"missing {first}",
                        "expect": ["400", "422"],
                        "body": {k: v for k, v in body.items() if k != first},
                    }
                )
                cases.append(
                    {
                        "case": f"wrong type for {first}",
                        "expect": ["400", "422"],
                        "body": {**body, first: _wrong(body[first])},
                    }
                )
            cases.append(
                {"case": "responds within 5 seconds", "expect": [ok], "timeout": 5, "body": body}
            )
            if method in ("put", "delete"):
                cases.append(
                    {
                        "case": "repeated is safe",
                        "expect": [ok, "404"] if method == "delete" else [ok],
                        "repeat": True,
                        "body": body,
                    }
                )
            needed = set()
            if path_params:
                needed.add("404")
            if body_schema:
                needed.add("400 or 422")
            if secured:
                needed.add("401 or 403")
            have = set(responses)
            gaps = sorted(
                n
                for n in needed
                if not any(code in have for code in n.split(" or "))
                and "4XX" not in have
                and "default" not in have
            )
            if gaps:
                missing.append({"operation": f"{method.upper()} {path}", "missing": gaps})
            operations.append(
                {
                    "name": name,
                    "method": method.upper(),
                    "path": path,
                    "documented": responses,
                    "cases": cases,
                }
            )
    return {
        "schema": SCHEMA,
        "title": (doc.get("info") or {}).get("title", ""),
        "operations": operations,
        "missing_status_codes": missing,
    }


def pytest_file(p: dict[str, Any]) -> str:
    """One pytest module with a test per case. It reads ANANKE_API_BASE_URL and talks only to that service."""
    lines = [
        f'"""Contract tests generated by Ananke from {p["title"] or "an OpenAPI document"}. Edit the fixtures to real values."""',
        "",
        "import os",
        "",
        "import httpx",
        "import pytest",
        "",
        'BASE = os.environ.get("ANANKE_API_BASE_URL", "")',
        'pytestmark = pytest.mark.skipif(not BASE, reason="set ANANKE_API_BASE_URL to a service on this machine")',
        'UNKNOWN = "00000000-does-not-exist"',
        "",
        "",
        "def _path(template, **values):",
        "    for key, value in values.items():",
        '        template = template.replace("{" + key + "}", str(value))',
        '    return __import__("re").sub(r"\\{[^}]+\\}", "1", template)',
        "",
    ]
    for op in p["operations"]:
        for n, case in enumerate(op["cases"], 1):
            fn = (
                re.sub(r"[^a-z0-9]+", "_", f"test_{op['name']}_{case['case']}".lower()).strip("_")
                + f"_{n}"
            )
            path = (
                f"_path({op['path']!r}"
                + (f", {case['param']}=UNKNOWN" if case.get("param") else "")
                + ")"
            )
            body = f", json={case['body']!r}" if case.get("body") is not None else ""
            timeout = case.get("timeout", 30)
            headers = ", headers={}" if case["case"] == "no credentials" else ""
            call = (
                f"httpx.request({op['method']!r}, BASE + {path}{body}{headers}, timeout={timeout})"
            )
            lines += ["", f"def {fn}():", f"    # {op['method']} {op['path']}: {case['case']}"]
            if case.get("repeat"):
                lines.append(f"    {call}")
            lines += [
                f"    response = {call}",
                f"    assert str(response.status_code) in {case['expect']!r}, response.text[:200]",
                "",
            ]
    return "\n".join(lines) + "\n"


def run(test_file: Path, base_url: str, out_dir: Path, *, timeout: int = 900) -> dict[str, Any]:
    """Run the generated tests against a service on this machine, and keep the JUnit results."""
    host = urlparse(base_url).hostname or ""
    if host not in LOOPBACK:
        raise ApiTestError(
            f"{base_url}: tests run only against a service on this machine (127.0.0.1, localhost, ::1)"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = out_dir / "api-tests.junit.xml"
    env = {**__import__("os").environ, "ANANKE_API_BASE_URL": base_url}
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        str(test_file),
    ]
    subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=False)  # noqa: S603
    results: dict[str, str] = {}
    if junit.is_file():
        for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 - our own pytest run's output
            outcome = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            results[case.get("name", "")] = outcome
    return {"junit": str(junit), "results": results}


def report(p: dict[str, Any], executed: dict[str, str] | None = None) -> dict[str, Any]:
    """Every case marked suggested, or executed with its outcome. Nothing generated counts as passing."""
    rows = []
    for op in p["operations"]:
        for n, case in enumerate(op["cases"], 1):
            fn = (
                re.sub(r"[^a-z0-9]+", "_", f"test_{op['name']}_{case['case']}".lower()).strip("_")
                + f"_{n}"
            )
            outcome = (executed or {}).get(fn)
            rows.append(
                {
                    "operation": f"{op['method']} {op['path']}",
                    "case": case["case"],
                    "test": fn,
                    "status": "suggested"
                    if outcome in (None, "skipped")
                    else f"executed: {outcome}",
                }
            )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"cases": rows, "counts": counts, "missing_status_codes": p["missing_status_codes"]}


__all__ = ["LOOPBACK", "SCHEMA", "ApiTestError", "example", "plan", "pytest_file", "report", "run"]

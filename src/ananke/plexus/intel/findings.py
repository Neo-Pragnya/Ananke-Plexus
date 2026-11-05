"""What scanners and test runs already reported, from the reports in the repository. Nothing is run.

SARIF comes from CodeQL, Semgrep, Trivy, Bandit and many more; JUnit XML from most test runners. A repository with no
report has ``status: not_found``, which isn't the same as no findings.
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.etree import (
    ElementTree as ET,
)

from ananke.plexus.intel._common import is_fixture, read_text
from ananke.plexus.intel.context import RepoContext

MAX_REPORT_BYTES = 30_000_000
MAX_FINDINGS = 2_000
MAX_FAILURES = 500
_LEVELS = {"error": "high", "warning": "medium", "note": "low", "none": "low"}
_JUNIT_NAME = re.compile(r"(^|/)(junit|test-?results?|pytest|TEST-)[^/]*\.xml$", re.I)


def _severity(result: dict[str, Any], rule: dict[str, Any]) -> str:
    properties = rule.get("properties") if isinstance(rule.get("properties"), dict) else {}
    score = properties.get("security-severity")
    try:
        number = float(score) if score is not None else None
    except (TypeError, ValueError):
        number = None
    if number is not None:
        return (
            "critical"
            if number >= 9
            else "high"
            if number >= 7
            else "medium"
            if number >= 4
            else "low"
        )
    return _LEVELS.get(
        str(
            result.get("level")
            or (rule.get("defaultConfiguration") or {}).get("level")
            or "warning"
        ),
        "medium",
    )


def _read_sarif(path: str, text: str, limits: list[str]) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
        runs = data.get("runs", [])
    except (ValueError, AttributeError, RecursionError):
        limits.append(f"{path} couldn't be read as SARIF")
        return []
    found: list[dict[str, Any]] = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        driver = (run.get("tool") or {}).get("driver") or {}
        tool = str(driver.get("name") or "unknown")
        rules = {str(r.get("id")): r for r in driver.get("rules") or [] if isinstance(r, dict)}
        for result in run.get("results") or []:
            if not isinstance(result, dict):
                continue
            rule_id = str(result.get("ruleId") or "")
            where = ((result.get("locations") or [{}])[0] or {}).get("physicalLocation") or {}
            file = str((where.get("artifactLocation") or {}).get("uri") or "")
            line = (where.get("region") or {}).get("startLine")
            rule = rules.get(rule_id, {})
            help_text = (
                (rule.get("help") or {}).get("text")
                or (rule.get("shortDescription") or {}).get("text")
                or ""
            )
            found.append(
                {
                    "tool": tool,
                    "rule": rule_id,
                    "severity": _severity(result, rule),
                    "message": str((result.get("message") or {}).get("text") or "")[:500],
                    "path": file.removeprefix("file://"),
                    "line": line if isinstance(line, int) else None,
                    "remediation": str(help_text)[:500],
                    "report": path,
                }
            )
    return found


def _read_junit(path: str, text: str, limits: list[str]) -> dict[str, Any] | None:
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        limits.append(f"{path} declares a DOCTYPE or entities, so it wasn't read")
        return None
    try:
        root = ET.fromstring(text)  # noqa: S314 - DOCTYPE and entities refused above
    except ET.ParseError:
        limits.append(f"{path} couldn't be read as JUnit XML")
        return None
    if root.tag not in {"testsuite", "testsuites"}:
        return None
    total = passed = skipped = 0
    failures: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        total += 1
        broken = case.find("failure")
        if broken is None:
            broken = case.find("error")
        if broken is not None:
            failures.append(
                {
                    "name": f"{case.get('classname', '')}.{case.get('name', '')}".strip("."),
                    "message": str(broken.get("message") or "")[:500],
                    "kind": broken.tag,
                    "report": path,
                }
            )
        elif case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
    return {
        "report": path,
        "total": total,
        "passed": passed,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures[:MAX_FAILURES],
    }


def analyze(ctx: RepoContext) -> dict[str, Any]:
    limits: list[str] = []
    findings: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    sarif = [p for p in ctx.with_suffix(".sarif", ".sarif.json") if not is_fixture(p)]
    junit = [p for p in ctx.with_suffix(".xml") if _JUNIT_NAME.search(p) and not is_fixture(p)]
    for path in sarif:
        text = read_text(ctx, path, MAX_REPORT_BYTES)
        if text is None:
            limits.append(f"{path} is too large to read")
            continue
        findings += _read_sarif(path, text, limits)
    for path in junit:
        text = read_text(ctx, path, MAX_REPORT_BYTES)
        if text is None:
            limits.append(f"{path} is too large to read")
            continue
        result = _read_junit(path, text, limits)
        if result:
            tests.append(result)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order.get(f["severity"], 4), f["path"], f["line"] or 0, f["rule"]))
    if len(findings) > MAX_FINDINGS:
        limits.append(f"only the worst {MAX_FINDINGS} of {len(findings)} findings are listed")
    if not sarif:
        limits.append("no SARIF report here, so scanner findings are unknown, not zero")
    if not junit:
        limits.append("no JUnit report here, so test results are unknown, not all passing")
    limits.append(
        "reports are read as they are: they may be older than the code, and nothing was run"
    )
    return {
        "status": "found" if sarif or tests else "not_found",
        "limits": limits,
        "reports": sorted({*sarif, *(t["report"] for t in tests)}),
        "findings": findings[:MAX_FINDINGS],
        "findings_total": len(findings),
        "tests": tests,
    }

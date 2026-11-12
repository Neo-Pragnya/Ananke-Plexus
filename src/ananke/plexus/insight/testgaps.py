"""Test gap synthesis (ADV04): tests built from three kinds of evidence, each test citing the evidence it answers.

* **Missing branches** from a coverage report (Cobertura ``coverage.xml`` or coverage.py ``coverage.json``): for
  each partly taken branch, the function it's in, the condition's source line and which way wasn't taken.
* **Observed failures** from JUnit XML: a regression test per failing case, with its message.
* **Changed contracts**: API operations whose handler file changed since a revision, with the contract findings that
  apply to them (missing error responses, untested auth), from ``ananke triage api-contract``.

Each test is a pytest skeleton that fails until it's written: generated tests are suggestions, never counted as
passing. Nothing is run; ``--write`` puts the file where you say.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest, git

SCHEMA = "ananke/test-gaps@1"
MAX_REPORT_BYTES = 50_000_000
MAX_GAPS = 200
_COND = re.compile(r"\((\d+)/(\d+)\)")


def _safe(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")[:60] or "case"


def _read_xml(path: Path) -> Any:  # an Element from the hardened reader
    """The hardened reader the coverage section uses: strict UTF-8, no DOCTYPE, no entities."""
    from ananke.plexus.intel.coverage import _BadReport, _safe_xml

    if path.stat().st_size > MAX_REPORT_BYTES:
        raise InsightError(f"{path.name} is over {MAX_REPORT_BYTES // 1_000_000} MB")
    try:
        return _safe_xml(path.read_bytes())
    except _BadReport as exc:
        raise InsightError(f"{path.name}: {exc}") from exc


def missing_branches(report: Path, root: Path) -> list[dict[str, Any]]:
    """Lines whose branches were only partly taken, with the arc that wasn't when the report says."""
    if not report.is_file():
        raise InsightError(
            f"{report} doesn't exist; make one with `pytest --cov --cov-branch --cov-report xml`"
        )
    gaps: list[dict[str, Any]] = []
    if report.suffix.lower() == ".json":
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise InsightError(f"{report.name} is malformed JSON: {exc}") from exc
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict):
            raise InsightError(f"{report.name} isn't a coverage.py JSON report (no 'files')")
        for name, entry in sorted(files.items()):
            for arc in entry.get("missing_branches") or []:
                if isinstance(arc, list) and len(arc) == 2:
                    gaps.append(
                        {
                            "file": name,
                            "line": int(arc[0]),
                            "missing_to": int(arc[1]),
                            "taken": None,
                        }
                    )
        if not any(
            f.get("missing_branches") is not None for f in files.values() if isinstance(f, dict)
        ):
            raise InsightError(f"{report.name} has no branch data; run coverage with --branch")
    elif report.suffix.lower() == ".xml":
        tree = _read_xml(report)
        if tree.tag != "coverage":
            raise InsightError(f"{report.name} isn't a Cobertura coverage report")
        sources = [s.text.strip() for s in tree.iter("source") if s.text]
        for cls in tree.iter("class"):
            name = cls.get("filename") or ""
            name = _relative(name, sources, root)
            for line in cls.iter("line"):
                if line.get("branch") != "true":
                    continue
                m = _COND.search(line.get("condition-coverage") or "")
                if m and m.group(1) != m.group(2):
                    missing = [
                        int(x) for x in re.findall(r"-?\d+", line.get("missing-branches") or "")
                    ]
                    gaps.append(
                        {
                            "file": name,
                            "line": int(line.get("number") or 0),
                            "missing_to": missing[0] if missing else None,
                            "taken": f"{m.group(1)} of {m.group(2)}",
                        }
                    )
    else:
        raise InsightError(
            f"{report.name}: unsupported format; give coverage.xml (Cobertura) or coverage.json"
        )
    return sorted(gaps, key=lambda g: (g["file"], g["line"]))


def _relative(name: str, sources: list[str], root: Path) -> str:
    for src in sources:
        candidate = Path(src) / name
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            continue
    if (root / name).is_file():
        return name
    for prefix in ("src/",):
        if (root / prefix / name).is_file():
            return prefix + name
    return name


def failures(junit: Path) -> list[dict[str, Any]]:
    if not junit.is_file():
        raise InsightError(f"{junit} doesn't exist; make one with `pytest --junitxml {junit.name}`")
    tree = _read_xml(junit)
    if tree.tag not in {"testsuites", "testsuite"}:
        raise InsightError(f"{junit.name} isn't JUnit XML")
    out = []
    for case in tree.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                message = (node.get("message") or node.text or "").strip().splitlines()
                out.append(
                    {
                        "test": case.get("name") or "",
                        "classname": case.get("classname") or "",
                        "file": case.get("file") or "",
                        "line": case.get("line") or "",
                        "kind": kind,
                        "message": message[0][:300] if message else "",
                    }
                )
    return out


def _enclosing(root: Path, rel: str, line: int, cache: dict[str, Any]) -> tuple[str, str, str]:
    """The function around a line, its module path and the line's text."""
    if rel not in cache:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
            cache[rel] = (text.splitlines(), ast.parse(text))
        except (OSError, SyntaxError, ValueError):
            cache[rel] = ([], None)
    lines, tree = cache[rel]
    source = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    best = ""
    if tree is not None:
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno <= line <= (node.end_lineno or node.lineno)
                and (not best or node.lineno >= int(best.split("@")[1]))
            ):
                best = f"{node.name}@{node.lineno}"
    module = rel[:-3].replace("/", ".").removeprefix("src.") if rel.endswith(".py") else ""
    return (best.split("@")[0] if best else ""), module, source


def changed_contracts(root: Path, base: str) -> list[dict[str, Any]]:
    from ananke.plexus import intel
    from ananke.plexus.intel.api_contract import ApiContractError, build

    changed = set(git(root, "diff", "--name-only", f"{base}...HEAD").split()) | set(
        git(root, "diff", "--name-only").split()
    )
    api = (intel.analyze(root).get("sections") or {}).get("api") or {}
    files = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    try:
        report = build(root, api, files)
    except ApiContractError:
        return []
    out = []
    for op in report["operations"]:
        where = str(op.get("evidence") or "").split(":")[0].split("#")[0]
        if where in changed:
            label = f"{op['method']} {op['path']}"
            found = [f for f in report["findings"] if f["operation"] == label]
            out.append(
                {
                    "operation": label,
                    "evidence": op["evidence"],
                    "findings": found,
                    "auth": bool(op["auth"]),
                    "responses": sorted(op["responses"]),
                }
            )
    return out


def synthesize(
    root: Path,
    *,
    coverage: Path | None = None,
    junit: Path | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if coverage is None and junit is None and base is None:
        raise InsightError("give at least one source of gaps: --coverage, --junit or --base")
    cache: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []
    if coverage is not None:
        for gap in missing_branches(coverage, root)[:MAX_GAPS]:
            fn, module, source = _enclosing(root, gap["file"], gap["line"], cache)
            way = (
                f"the arc to line {gap['missing_to']}"
                if gap["missing_to"] not in (None, -1)
                else (
                    "the exit from the function" if gap["missing_to"] == -1 else "the untaken way"
                )
            )
            cases.append(
                {
                    "kind": "missing-branch",
                    "name": f"test_{_safe(fn or Path(gap['file']).stem)}_takes_branch_{gap['line']}",
                    "target": f"{module}.{fn}" if module and fn else gap["file"],
                    "evidence": f"{gap['file']}:{gap['line']}",
                    "why": f"`{source}` never took {way}"
                    + (f" ({gap['taken']} taken)" if gap["taken"] else ""),
                    "status": "generated, not run",
                }
            )
    if junit is not None:
        for f in failures(junit)[:MAX_GAPS]:
            cases.append(
                {
                    "kind": "observed-failure",
                    "name": f"test_regression_{_safe(f['test'])}",
                    "target": f"{f['classname']}.{f['test']}".strip("."),
                    "evidence": f"{f['file']}:{f['line']}"
                    if f["file"]
                    else f"{junit.name}: {f['classname']}",
                    "why": f"{f['kind']}: {f['message']}",
                    "status": "generated, not run",
                }
            )
    if base is not None:
        for c in changed_contracts(root, base)[:MAX_GAPS]:
            reasons = [f["rule"] for f in c["findings"]] or ["changed since " + base]
            cases.append(
                {
                    "kind": "changed-contract",
                    "name": f"test_contract_{_safe(c['operation'])}",
                    "target": c["operation"],
                    "evidence": c["evidence"],
                    "why": "; ".join(sorted(set(reasons)))
                    + f"; documented statuses {', '.join(c['responses']) or 'none'}",
                    "status": "generated, not run",
                    "expects": (["401", "403"] if c["auth"] else [])
                    + ([] if any(s.startswith("4") for s in c["responses"]) else ["4xx"]),
                }
            )
    seen: set[str] = set()
    for case in cases:  # names stay unique so the file collects
        base_name, n = case["name"], 2
        while case["name"] in seen:
            case["name"] = f"{base_name}_{n}"
            n += 1
        seen.add(case["name"])
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "summary": {
            "tests": len(cases),
            "missing_branches": sum(1 for c in cases if c["kind"] == "missing-branch"),
            "observed_failures": sum(1 for c in cases if c["kind"] == "observed-failure"),
            "changed_contracts": sum(1 for c in cases if c["kind"] == "changed-contract"),
        },
        "tests": cases,
        "limits": [
            "Generated tests fail until someone writes the assertion: they mark a gap, they don't close it.",
            f"At most {MAX_GAPS} gaps of each kind are listed.",
        ],
    }
    report["digest"] = digest(report)
    return report


def pytest_file(report: dict[str, Any]) -> str:
    lines = [
        '"""Tests for gaps found by `ananke triage test-gaps`. Each one fails until it is written."""',
        "",
        "import pytest",
        "",
    ]
    for case in report["tests"]:
        lines += [
            "",
            f"# {case['kind']}: {case['evidence']}",
            f"def {case['name']}() -> None:",
            f'    """{case["why"].replace(chr(34) * 3, "")}"""',
            f"    # target: {case['target']}",
        ]
        if case.get("expects"):
            lines.append(f"    # expect: {', '.join(case['expects'])}")
        lines += ["    pytest.fail('not written yet: see the evidence above')", ""]
    return "\n".join(lines)


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Test gaps",
        "",
        f"{s['tests']} tests to write: {s['missing_branches']} for missing branches, {s['observed_failures']} for "
        f"observed failures, {s['changed_contracts']} for changed contracts. All generated, none run.",
        "",
        "| Test | Kind | Evidence | Why |",
        "| --- | --- | --- | --- |",
    ]
    for c in report["tests"]:
        lines.append(f"| `{c['name']}` | {c['kind']} | `{c['evidence']}` | {c['why']} |")
    lines += ["", f"Digest: `{report['digest']}`", ""]
    return "\n".join(lines)

"""Rerun selected gates and compare (three-repo plan: CAP-quality-cve-04).

``ananke triage findings`` lists the gates whose reports its findings came from. ``--rerun GATE`` runs the ones you
name, and only those:

* each gate is a fixed argument list (no shell), run from the repository root, with a time limit;
* a tool that isn't installed is reported as *not installed*, never as passing;
* scanners write SARIF into ``ananke-gates/`` where they can, so the new findings are counted the same way the old
  ones were; the result says how many findings the gate had before and how many it has now;
* ``tests`` reruns the failing tests from the JUnit findings (pytest node ids), then nothing else.

Every result is labelled **executed: passed**, **executed: failed**, **not installed** or **not runnable here**.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

OUT_DIR = "ananke-gates"
TIMEOUT = 1800


def _argv(
    gate: str, paths: list[str], out: Path, findings: list[dict[str, Any]]
) -> list[str] | None:
    targets = paths or ["."]
    sarif = str(out / f"{gate}.sarif")
    table: dict[str, list[str]] = {
        "semgrep": [
            "semgrep",
            "scan",
            "--config",
            "auto",
            "--sarif",
            "--output",
            sarif,
            "--quiet",
            *targets,
        ],
        "bandit": ["bandit", "-r", "-q", "-f", "json", "-o", str(out / "bandit.json"), *targets],
        "ruff": ["ruff", "check", "--output-format", "sarif", "--output-file", sarif, *targets],
        "gitleaks": [
            "gitleaks",
            "detect",
            "--source",
            ".",
            "--no-banner",
            "--report-format",
            "sarif",
            "--report-path",
            sarif,
        ],
        "trivy": ["trivy", "fs", "--quiet", "--format", "sarif", "--output", sarif, "."],
        "grype": ["grype", "dir:.", "-q", "-o", f"sarif={sarif}"],
        "eslint": [
            "npx",
            "--no-install",
            "eslint",
            "--format",
            "json",
            "--output-file",
            str(out / "eslint.json"),
            *targets,
        ],
        "dependencies": ["pip-audit", "--format", "json", "--output", str(out / "pip-audit.json")],
    }
    if gate == "tests":
        nodes = sorted(
            {
                str(f.get("test") or f.get("title") or "")
                for f in findings
                if f.get("kind") == "test" and "::" in str(f.get("test") or f.get("title") or "")
            }
        )
        return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodes] if nodes else None
    return table.get(gate)


def _count(gate: str, out: Path) -> int | None:
    """Findings in the report a gate wrote, counted the way its kind is."""
    sarif = out / f"{gate}.sarif"
    try:
        if sarif.is_file():
            data = json.loads(sarif.read_text(encoding="utf-8"))
            return sum(len(run.get("results") or []) for run in data.get("runs") or [])
        if gate == "bandit" and (out / "bandit.json").is_file():
            return len(
                json.loads((out / "bandit.json").read_text(encoding="utf-8")).get("results") or []
            )
        if gate == "eslint" and (out / "eslint.json").is_file():
            return sum(
                len(f.get("messages") or [])
                for f in json.loads((out / "eslint.json").read_text(encoding="utf-8"))
            )
        if gate == "dependencies" and (out / "pip-audit.json").is_file():
            data = json.loads((out / "pip-audit.json").read_text(encoding="utf-8"))
            deps = data.get("dependencies") if isinstance(data, dict) else data
            return sum(len(d.get("vulns") or []) for d in deps or [])
    except (OSError, ValueError, AttributeError):
        return None
    return None


def rerun(root: Path, gates: list[str], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the named gates for a ``prioritize`` result; return one record per gate."""
    root = root.resolve()
    out = root / OUT_DIR
    out.mkdir(exist_ok=True)
    findings = result.get("findings") or []
    listed = {g["gate"] for g in result.get("rerun") or []}
    paths = result.get("changed") or []
    records = []
    for gate in gates:
        before = sum(
            1
            for f in findings
            if str(f.get("source", "")).lower() == gate
            or (gate == "tests" and f.get("kind") == "test")
            or (gate == "dependencies" and f.get("kind") == "dependency")
        )
        argv = _argv(gate, paths, out, findings)
        record: dict[str, Any] = {"gate": gate, "listed": gate in listed, "findings_before": before}
        if argv is None:
            record.update(
                status="not runnable here",
                reason="no command is known for it"
                if gate != "tests"
                else "no failing test ids to rerun",
            )
            records.append(record)
            continue
        tool = argv[0] if argv[0] != "python" else "python"
        if tool != "python" and shutil.which(tool) is None:
            record.update(status="not installed", command=argv)
            records.append(record)
            continue
        if tool == "python":
            import sys

            argv = [sys.executable, *argv[1:]]
        started = time.monotonic()
        try:
            done = subprocess.run(argv, cwd=root, capture_output=True, timeout=TIMEOUT, check=False)  # noqa: S603 - fixed argv per gate
        except (OSError, subprocess.TimeoutExpired) as exc:
            record.update(status="not runnable here", command=argv, reason=str(exc)[:300])
            records.append(record)
            continue
        after = _count(gate, out)
        tail = done.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        failed = (
            done.returncode != 0
            if gate in {"tests", "dependencies"}
            else (after or 0) > 0 or done.returncode not in (0, 1)
        )
        record.update(
            status="executed: failed" if failed else "executed: passed",
            command=argv,
            exit_code=done.returncode,
            seconds=round(time.monotonic() - started, 1),
            findings_after=after,
            output=tail,
        )
        records.append(record)
    return records


def to_markdown(records: list[dict[str, Any]]) -> str:
    lines = [
        "",
        "## Gates rerun",
        "",
        "| Gate | Status | Findings before | After |",
        "| --- | --- | --- | --- |",
    ]
    for r in records:
        after = r.get("findings_after")
        lines.append(
            f"| {r['gate']} | {r['status']} | {r['findings_before']} | {'-' if after is None else after} |"
        )
    return "\n".join(lines) + "\n"

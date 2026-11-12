"""Run replay and incident causality (ADV14): from a failed build to the commit, step, container and logs.

Starting from a downloaded Jenkins console log (``ananke triage jenkins`` finds the stage and the first primary
failure), it links:

1. **Commit:** the revision the build checked out, read from the log (``Checking out Revision ...``, ``GIT_COMMIT``),
   checked against the repository.
2. **Suspects:** with a last good revision, every commit between it and the failing one, ranked by whether it touched
   the file in the failure, the code that file reaches (one step of the what-if graph walk), or nothing related.
   A suspect is a **hypothesis**; the commit and the failing line are **observed**.
3. **Step:** the stage and the shell command running when it failed.
4. **Container:** the containers, Compose services and images the log names near the failure, matched to the
   repository's Compose services.
5. **Logs:** with a ``docker compose logs`` capture, the errors each implicated service logged.

Replaying the run means re-running that stage's commands at the failing commit; the report lists them (from the
Jenkins triage rerun plan) and runs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest, git

SCHEMA = "ananke/incident@1"
_REVISION = [
    re.compile(r"Checking out Revision ([0-9a-f]{7,40})"),
    re.compile(r"\bGIT_COMMIT=([0-9a-f]{7,40})"),
    re.compile(r"HEAD is now at ([0-9a-f]{7,40})"),
    re.compile(r"git checkout -f ([0-9a-f]{40})"),
]
_CONTAINER = re.compile(
    r"\bContainer\s+([A-Za-z0-9_.\-]+)\s+(Started|Starting|Exited|Created|Error|Running|Stopped)"
)
_COMPOSE_CMD = re.compile(
    r"docker[- ]compose\s+(?:-f\s+\S+\s+)*(run|up|build|exec|start)\s+(?:--?\S+\s+)*([A-Za-z0-9_.\-]+)"
)
_IMAGE = re.compile(
    r"docker\s+(?:run|pull|push|build\s+-t)\s+(?:--?\S+\s+)*([A-Za-z0-9_./\-]+:[A-Za-z0-9_.\-]+)"
)
_POD = re.compile(r"\bpod/([a-z0-9\-]+)")
_GHA = re.compile(r"^([^\t]+)\t([^\t]+)\t\d{4}-\d\d-\d\dT[\d:.]+Z ?(.*)$")
_PATH = re.compile(r"((?:[\w.\-]+/)+[\w.\-]+\.(?:py|js|ts|tsx|java|go|kt|rb))")


def _revision(log: str) -> str | None:
    for pattern in _REVISION:
        m = pattern.search(log)
        if m:
            return m.group(1)
    return None


def _containers(lines: list[str], around: int, services: set[str]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for no, line in enumerate(lines, start=1):
        for m in _CONTAINER.finditer(line):
            name = m.group(1)
            service = next(
                (s for s in services if re.search(rf"(^|[-_]){re.escape(s)}([-_]\d+)?$", name)),
                None,
            )
            found.setdefault(
                name,
                {"name": name, "kind": "container", "service": service, "states": [], "lines": []},
            )
            found[name]["states"].append(m.group(2))
            found[name]["lines"].append(no)
        for m in _COMPOSE_CMD.finditer(line):
            svc = m.group(2)
            found.setdefault(
                f"service:{svc}",
                {
                    "name": svc,
                    "kind": "compose service",
                    "service": svc,
                    "states": [m.group(1)],
                    "lines": [],
                },
            )
            found[f"service:{svc}"]["lines"].append(no)
        for m in _IMAGE.finditer(line):
            found.setdefault(
                f"image:{m.group(1)}",
                {"name": m.group(1), "kind": "image", "service": None, "states": [], "lines": []},
            )
            found[f"image:{m.group(1)}"]["lines"].append(no)
        for m in _POD.finditer(line):
            found.setdefault(
                f"pod:{m.group(1)}",
                {"name": m.group(1), "kind": "pod", "service": None, "states": [], "lines": []},
            )
            found[f"pod:{m.group(1)}"]["lines"].append(no)
    rows = list(found.values())
    for r in rows:
        r["nearest_line"] = min(r["lines"], key=lambda n: abs(n - around)) if r["lines"] else None
        r["distance"] = abs(r["nearest_line"] - around) if r["nearest_line"] else None
    return sorted(
        rows, key=lambda r: (r["distance"] if r["distance"] is not None else 10**9, r["name"])
    )


def _suspects(root: Path, good: str, bad: str, failing_files: list[str]) -> list[dict[str, Any]]:
    from ananke.plexus.insight.timemachine import graph_of
    from ananke.plexus.insight.whatif import seeds, simulate

    out = git(
        root,
        "log",
        "--no-merges",
        "--name-only",
        "--format=@@%H%x09%s",
        f"{good}..{bad}",
        timeout=120,
    )
    reached: set[str] = set(failing_files)
    if failing_files:
        graph = graph_of(root)
        start, _ = seeds(graph, failing_files)
        reached |= {
            r["path"] for r in simulate(graph, start, depth=1, both=True).values() if r["path"]
        }
    rows = []
    for block in out.split("@@")[1:]:
        lines = [x for x in block.splitlines() if x.strip()]
        sha, _, subject = lines[0].partition("\t")
        files = lines[1:]
        direct = sorted(set(files) & set(failing_files))
        near = sorted((set(files) & reached) - set(direct))
        rank = 2 if direct else 1 if near else 0
        rows.append(
            {
                "commit": sha[:12],
                "subject": subject,
                "files": len(files),
                "touches_failure": direct,
                "touches_reached_code": near[:10],
                "rank": rank,
                "status": "hypothesis: "
                + (
                    "changed the failing file"
                    if direct
                    else "changed code the failing file reaches"
                    if near
                    else "no link found"
                ),
            }
        )
    return sorted(rows, key=lambda r: -r["rank"])


def investigate(
    console: Path,
    root: Path | None = None,
    *,
    last_good: str | None = None,
    service_logs: Path | None = None,
    jenkinsfile: Path | None = None,
    commit: str | None = None,
) -> dict[str, Any]:
    from ananke.plexus.jenkins_triage import TriageError, triage

    try:
        log = console.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InsightError(f"{console}: {exc}") from exc
    # GitHub Actions logs (`gh run view --log`) prefix each line with job, step and time: keep the step as the stage
    raw_lines = log.splitlines()
    steps: dict[int, str] = {}
    if raw_lines and sum(1 for ln in raw_lines[:50] if _GHA.match(ln)) >= min(5, len(raw_lines)):
        plain = []
        for no, ln in enumerate(raw_lines, start=1):
            m = _GHA.match(ln)
            if m:
                steps[no] = f"{m.group(1)} / {m.group(2)}"
                plain.append(m.group(3))
            else:
                plain.append(ln)
        log = "\n".join(plain)
    try:
        report = triage(
            log,
            jenkinsfile=jenkinsfile.read_text(encoding="utf-8") if jenkinsfile else "",
            source=console.name,
        )
    except TriageError as exc:
        raise InsightError(str(exc)) from exc
    failure = report["first_failure"]
    if failure and steps and not failure.get("stage"):
        failure = {**failure, "stage": steps.get(failure["line"], "")}
    lines = log.splitlines()
    chain: list[dict[str, Any]] = []
    revision = commit or _revision(log)
    commit_info: dict[str, Any] | None = None
    if revision:
        info: dict[str, Any] = {
            "revision": revision,
            "status": "given" if commit else "observed in the log",
        }
        if root is not None and (root / ".git").exists():
            try:
                full = git(
                    root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"
                ).strip()
                subject = git(root, "log", "-1", "--format=%s", full).strip()
                info.update({"commit": full, "subject": subject, "in_repository": True})
            except InsightError:
                info.update({"in_repository": False})
        commit_info = info
        chain.append({"link": "commit", "value": revision, "status": "observed"})
    if failure:
        if failure.get("stage"):
            chain.append({"link": "stage", "value": failure["stage"], "status": "observed"})
        step = next(  # the shell command running when it failed: the last ``+ cmd`` echo before the failure
            (
                lines[i][2:].strip()
                for i in range(failure["line"] - 1, -1, -1)
                if lines[i].startswith("+ ") and lines[i][2:].strip()
            ),
            None,
        )
        if step:
            chain.append({"link": "step", "value": step, "status": "observed"})
        chain.append(
            {
                "link": "failure",
                "value": failure["text"],
                "line": failure["line"],
                "status": "observed",
            }
        )
    services: set[str] = set()
    if root is not None:
        from ananke.plexus.intel.infra import scan

        services = {r["name"] for r in scan([root])["resources"] if r["kind"] == "compose-service"}
    containers = _containers(lines, failure["line"] if failure else len(lines), services)
    for c in containers[:3]:
        chain.append(
            {
                "link": c["kind"],
                "value": c["name"],
                "service": c["service"],
                "status": "observed"
                if c["distance"] is not None and c["distance"] <= 20
                else "hypothesis: named in the log, far from the failure",
            }
        )
    failing_files = sorted({m.group(1) for m in _PATH.finditer(failure["text"])}) if failure else []
    suspects = None
    if last_good:
        if root is None or not (root / ".git").exists():
            raise InsightError("--last-good needs --project pointing at the repository")
        bad = (commit_info or {}).get("commit")
        if not bad:
            raise InsightError("the log doesn't name the commit it built; pass it with --commit")
        suspects = _suspects(root, last_good, bad, failing_files)
        top = next((s for s in suspects if s["rank"] > 0), None)
        if top:
            chain.insert(
                1,
                {
                    "link": "suspect commit",
                    "value": f"{top['commit']} {top['subject']}",
                    "status": top["status"],
                },
            )
    service_errors = None
    if service_logs is not None:
        from ananke.plexus.intel.infra import scan, triage_log

        found = (
            scan([root])
            if root is not None
            else {"resources": [], "edges": [], "cross_project": [], "warnings": [], "projects": []}
        )
        triaged = triage_log(found, service_logs.read_text(encoding="utf-8", errors="replace"))
        implicated = {c["service"] for c in containers if c["service"]}
        service_errors = triaged
        for row in triaged.get("services") or []:
            errors = [f for f in row.get("findings") or [] if f.get("level") == "error"]
            if errors and (
                row["service"] in implicated or row["service"] == triaged.get("first_failing")
            ):
                chain.append(
                    {
                        "link": "service log",
                        "value": f"{row['service']}: {errors[0]['message']}",
                        "status": "observed",
                    }
                )
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "source": console.name,
        "result": report["result"] or ("failed" if failure else "no failure found"),
        "commit": commit_info,
        "failure": failure,
        "chain": chain,
        "containers": containers[:10],
        "failing_files": failing_files,
        "suspects": suspects,
        "service_logs": service_errors,
        "replay": report.get("rerun_plan"),
        "limits": [
            "Suspect commits are hypotheses ranked by what they touched; a flaky test or the environment can fail a "
            "build no commit caused.",
            "Containers are matched to Compose services by name; a renamed project prefix may not match.",
            "Nothing was re-run: the replay lists the stage's commands at the failing commit.",
        ],
    }
    result["digest"] = digest(result)
    return result


def to_markdown(r: dict[str, Any]) -> str:
    lines = [f"# Incident: {r['source']} ({r['result']})", "", "## Causal chain", ""]
    for i, link in enumerate(r["chain"], start=1):
        lines.append(f"{i}. **{link['link']}**: `{link['value']}` ({link['status']})")
    if r["suspects"]:
        lines += [
            "",
            "## Commits since the last good build",
            "",
            "| Commit | Subject | Link | Status |",
            "| --- | --- | --- | --- |",
        ]
        for s in r["suspects"]:
            link = ", ".join(s["touches_failure"] or s["touches_reached_code"][:3]) or "-"
            lines.append(f"| `{s['commit']}` | {s['subject']} | {link} | {s['status']} |")
    if r["replay"]:
        lines += ["", "## Replay", "", "Re-run at the failing commit:", ""]
        lines += [f"- {s}" for s in r["replay"][:10]]
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

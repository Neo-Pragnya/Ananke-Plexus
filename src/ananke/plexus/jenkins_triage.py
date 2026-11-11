"""Jenkins failure triage from files you already have: the console log, the Jenkinsfile and what changed
(three-repo plan: CAP-jenkins-triage-01..04).

It needs no Jenkins access. Download the console log (or copy it from the build page), and it reads:

1. **Stages**, from the pipeline markers (``[Pipeline] { (Test)``), with their lines, status and, when the
   Timestamper plugin is on, their times.
2. **The first primary failure**: the earliest stage that failed on its own (a non-zero exit, a traceback, failing
   tests, an error line). Every later failure, and every stage *skipped due to earlier failure*, is a **cascade**:
   fixing the first one is the plan, not chasing the rest.
3. **What changed near it**: the failing stage's commands from the Jenkinsfile (``sh 'pytest tests/api'``), matched
   to the changed files by the paths they name and the tools they run (pytest and ``.py`` files, npm and
   ``package.json``, docker and ``Dockerfile``). A match is evidence to check, not proof of cause.
4. **A summary and a rerun plan**: the timeline, the first failure with its lines, the likely related changes, and
   which stage to rerun from once it's fixed.

Nothing is sent anywhere and nothing is run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEMA = "triage-report/v1"
_STAGE_OPEN = re.compile(r"^\[Pipeline\] \{ \((.+)\)\s*$")
_STAGE_CLOSE = re.compile(r"^\[Pipeline\] // stage\s*$")
_SKIPPED = re.compile(r'^Stage "(.+)" skipped due to (earlier failure|when conditional)')
_STAMP = re.compile(r"^\s*\[?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?)\]?\s?")
_RESULT = re.compile(r"^Finished: (SUCCESS|FAILURE|UNSTABLE|ABORTED|NOT_BUILT)\b")
PRIMARY = (
    (
        "exit-code",
        re.compile(
            r"script returned exit code ([1-9]\d*)|exit(?:ed)? (?:with )?code ([1-9]\d*)", re.I
        ),
    ),
    ("traceback", re.compile(r"^Traceback \(most recent call last\):")),
    (
        "test-failures",
        re.compile(r"\b(\d+) (?:failed|failures?)\b|\bTests run:.*Failures: [1-9]|FAILED ", re.I),
    ),
    (
        "compile-error",
        re.compile(r"\berror(?: TS\d+)?:|\bcompilation (?:failed|error)|SyntaxError", re.I),
    ),
    (
        "out-of-memory",
        re.compile(r"\bOOMKilled\b|\bOut of memory\b|\bMemoryError\b|exit code 137", re.I),
    ),
    ("error-line", re.compile(r"^(?:\[\w+\] )?(ERROR|FATAL)\b")),
)
TOOLS = {
    "pytest": (
        r"\.py$",
        r"(^|/)(pytest\.ini|pyproject\.toml|setup\.cfg|conftest\.py|requirements[^/]*\.txt|uv\.lock)$",
    ),
    "python": (r"\.py$", r"(^|/)(pyproject\.toml|requirements[^/]*\.txt|uv\.lock)$"),
    "npm": (
        r"\.(js|jsx|ts|tsx|mjs|cjs|vue|svelte)$",
        r"(^|/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|tsconfig[^/]*\.json)$",
    ),
    "yarn": (r"\.(js|jsx|ts|tsx)$", r"(^|/)(package\.json|yarn\.lock)$"),
    "mvn": (r"\.(java|kt)$", r"(^|/)pom\.xml$"),
    "gradle": (r"\.(java|kt|kts)$", r"(^|/)(build\.gradle(\.kts)?|settings\.gradle(\.kts)?)$"),
    "docker": (r"(^|/)Dockerfile[^/]*$", r"(^|/)(docker-compose[^/]*\.ya?ml|\.dockerignore)$"),
    "terraform": (r"\.tf$", r"\.tfvars$"),
    "helm": (r"(^|/)(templates/.*\.ya?ml|values[^/]*\.ya?ml)$", r"(^|/)Chart\.yaml$"),
    "kubectl": (r"\.ya?ml$", r"$^"),
    "make": (r"(^|/)Makefile$", r"$^"),
}


class TriageError(ValueError):
    pass


@dataclass
class Stage:
    name: str
    start: int
    end: int = 0
    status: str = "passed"
    started_at: str | None = None
    finished_at: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


def _seconds(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        return (
            datetime.fromisoformat(b.replace("Z", "+00:00"))
            - datetime.fromisoformat(a.replace("Z", "+00:00"))
        ).total_seconds()
    except ValueError:
        return None


def parse_console(text: str) -> dict[str, Any]:
    """Stages with their lines, times and errors, the build result, and which errors fall outside any stage."""
    stages: list[Stage] = []
    stack: list[Stage] = []
    skipped: list[str] = []
    result = None
    outside: list[dict[str, Any]] = []
    for no, raw in enumerate(text.splitlines(), start=1):
        stamp = _STAMP.match(raw)
        when = stamp.group(1) if stamp else None
        line = raw[stamp.end() :] if stamp else raw
        if m := _STAGE_OPEN.match(line):
            stage = Stage(m.group(1), no, started_at=when)
            stages.append(stage)
            stack.append(stage)
            continue
        if _STAGE_CLOSE.match(line) and stack:
            done = stack.pop()
            done.end, done.finished_at = no, when or done.finished_at
            continue
        if m := _SKIPPED.match(line):
            skipped.append(m.group(1))
            if stack and stack[-1].name == m.group(1):
                stack[-1].status = "skipped"
            continue
        if m := _RESULT.match(line):
            result = m.group(1)
            continue
        for rule, pattern in PRIMARY:
            if pattern.search(line):
                error = {"rule": rule, "line": no, "text": line.strip()[:300]}
                (stack[-1].errors if stack else outside).append(error)
                if stack:
                    stack[-1].status = "failed"
                    stack[-1].finished_at = when or stack[-1].finished_at
                break
        if stack and when:
            stack[-1].finished_at = when
    for s in stages:
        if s.name in skipped and not s.errors:
            s.status = "skipped"
    return {"stages": stages, "result": result, "outside": outside, "skipped": skipped}


def jenkinsfile_commands(text: str) -> dict[str, list[str]]:
    """``sh``/``bat``/``powershell`` commands per ``stage('Name')`` in a Jenkinsfile (declarative or scripted)."""
    commands: dict[str, list[str]] = {}
    current: str | None = None
    depth_at: list[tuple[str, int]] = []
    depth = 0
    step = re.compile(
        r"""\b(?:sh|bat|powershell)\s*\(?\s*(?:script:\s*)?(['"]{1,3})(.*?)\1""", re.S
    )
    for line in text.splitlines():
        if m := re.search(r"""\bstage\s*\(\s*['"](.+?)['"]\s*\)""", line):
            current = m.group(1)
            commands.setdefault(current, [])
            depth_at.append((current, depth))
        for found in step.finditer(line):
            if current is not None:
                commands[current].append(found.group(2).strip())
        depth += line.count("{") - line.count("}")
        while depth_at and depth <= depth_at[-1][1] and "{" not in line:
            depth_at.pop()
            current = depth_at[-1][0] if depth_at else None
    return commands


def related_changes(commands: list[str], changed: list[str]) -> list[dict[str, Any]]:
    """Changed files a stage's commands point at: by a path they name, or by the tool they run."""
    out: dict[str, set[str]] = {}
    words = " ".join(commands)
    tokens = set(re.findall(r"[\w./-]+", words))
    for path in changed:
        why = set()
        for token in tokens:
            if "/" in token or "." in token:
                t = token.strip("./")
                if t and (path == t or path.startswith(t.rstrip("/") + "/")):
                    why.add(f"named in a command: {token}")
        for tool, (main, config) in TOOLS.items():
            if re.search(rf"(^|[\s/]){re.escape(tool)}\b", words):
                if re.search(main, path):
                    why.add(
                        f"{tool} works on {path.rsplit('.', 1)[-1] if '.' in path else path} files"
                    )
                elif re.search(config, path):
                    why.add(f"{tool} reads {path.rsplit('/', 1)[-1]}")
        if why:
            out[path] = why
    return [
        {"path": p, "why": sorted(w)}
        for p, w in sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


_OUTCOME = {
    "FAILURE": "failed",
    "UNSTABLE": "was unstable",
    "ABORTED": "was aborted",
    "NOT_BUILT": "wasn't built",
}


def _outcome(result: str | None) -> str:
    return _OUTCOME.get(result or "", "failed")


def triage(
    console: str,
    *,
    jenkinsfile: str = "",
    changed: list[str] | None = None,
    source: str = "console",
) -> dict[str, Any]:
    parsed = parse_console(console)
    stages: list[Stage] = parsed["stages"]
    if not stages and not parsed["outside"] and parsed["result"] is None:
        raise TriageError(
            f"{source}: no pipeline stages, errors or build result found; is this a Jenkins console log?"
        )
    failed = [s for s in stages if s.status == "failed"]
    first = min(failed, key=lambda s: s.errors[0]["line"]) if failed else None
    order = {s.name: i for i, s in enumerate(stages)}
    cascade = []
    if first:
        for s in stages:
            if s is first:
                continue
            after = order[s.name] > order[first.name] or (
                s.errors and s.errors[0]["line"] > first.errors[0]["line"]
            )
            if s.status == "skipped" or (s.status == "failed" and after):
                cascade.append(s.name)
    commands = jenkinsfile_commands(jenkinsfile) if jenkinsfile else {}
    related = related_changes(commands.get(first.name, []), changed or []) if first else []
    timeline = [
        {
            "stage": s.name,
            "status": "cascade" if s.name in cascade and s.status == "failed" else s.status,
            "lines": [s.start, s.end or s.start],
            "seconds": _seconds(s.started_at, s.finished_at),
            "errors": len(s.errors),
        }
        for s in stages
    ]
    if first:
        cause = first.errors[0]
        summary = (
            f"The build {_outcome(parsed['result'])} at stage '{first.name}' "
            f"({cause['rule'].replace('-', ' ')}, line {cause['line']})."
            + (
                f" {len(cascade)} later stage(s) failed or were skipped because of it."
                if cascade
                else ""
            )
            + (f" {len(related)} changed file(s) relate to that stage." if related else "")
        )
        plan = [
            f"Fix the first failure in '{first.name}': {cause['text']}",
            *(
                ["Check the related changes first: " + ", ".join(r["path"] for r in related[:5])]
                if related
                else []
            ),
            f"Rerun from stage '{first.name}'"
            + (
                f"; the cascade ({', '.join(cascade)}) should clear without its own fix"
                if cascade
                else ""
            ),
        ]
    elif parsed["outside"]:
        cause = parsed["outside"][0]
        summary = f"The build {_outcome(parsed['result'])} outside any stage ({cause['rule'].replace('-', ' ')}, line {cause['line']})."
        plan = [
            f"Fix: {cause['text']}",
            "Rerun the whole pipeline: the failure came before or between stages",
        ]
    else:
        summary = f"No failure found; the build result is {parsed['result'] or 'unknown'}."
        plan = []
    return {
        "schema": SCHEMA,
        "source": source,
        "result": parsed["result"],
        "summary": summary,
        "first_failure": (
            {"stage": first.name, **first.errors[0], "other_errors": len(first.errors) - 1}
            if first
            else ({"stage": None, **parsed["outside"][0]} if parsed["outside"] else None)
        ),
        "cascade": cascade,
        "timeline": timeline,
        "related_changes": related,
        "stage_commands": commands.get(first.name, []) if first else [],
        "rerun_plan": plan,
        "limits": [
            "Read from the console log only: stages are found from the pipeline markers, so a freestyle job has none.",
            "Related changes are matched by paths and tools a stage's commands name: evidence to check, not proof of cause.",
        ],
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Build triage: {report['source']}", "", report["summary"], ""]
    if report["timeline"]:
        lines += ["| Stage | Status | Lines | Time | Errors |", "|---|---|---|---|---|"]
        for t in report["timeline"]:
            secs = f"{t['seconds']:.0f}s" if t["seconds"] is not None else "—"
            lines.append(
                f"| {t['stage']} | {t['status']} | {t['lines'][0]} to {t['lines'][1]} | {secs} | {t['errors']} |"
            )
        lines.append("")
    if report["related_changes"]:
        lines += ["## Changes related to the failing stage", ""]
        lines += [f"- `{r['path']}`: {'; '.join(r['why'])}" for r in report["related_changes"][:15]]
        lines.append("")
    if report["rerun_plan"]:
        lines += (
            ["## Plan", ""]
            + [f"{i}. {step}" for i, step in enumerate(report["rerun_plan"], start=1)]
            + [""]
        )
    lines += ["## Limits", ""] + [f"- {x}" for x in report["limits"]]
    return "\n".join(lines).replace("<", "&lt;") + "\n"


__all__ = [
    "SCHEMA",
    "TriageError",
    "jenkinsfile_commands",
    "parse_console",
    "related_changes",
    "to_markdown",
    "triage",
]

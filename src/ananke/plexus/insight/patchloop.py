"""Review-to-fix verification loop (ADV10): proposed patch → tests → risk delta → approval → branch for a PR.

1. **Applies cleanly?** The patch is checked and applied in a throwaway git worktree at the base revision; the
   working tree you're in is never touched.
2. **Tests:** the tests the change reaches (from the what-if simulation), or the command you give. They run only
   with ``run_tests``; otherwise they're listed as *to run*, never reported as passing.
3. **Risk delta:** the code graph before and after (relationships and links between components added or removed),
   API contract findings before and after, and the size of the change.
4. **Approval:** a digest of the patch and the evidence. ``commit`` with that exact digest commits the patch to a new
   local branch from the worktree, ready for a pull request (push and open it with the Dex GitHub server or your own
   tools). A different digest, or a patch that changed, is refused.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ananke.plexus.insight import InsightError, digest, git, resolve_ref

SCHEMA = "ananke/patch-loop@1"
MAX_PATCH_BYTES = 10_000_000
TEST_TIMEOUT = 1800


def _changed_files(patch_text: str) -> list[str]:
    files = []
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name != "/dev/null":
                files.append(name[2:] if name.startswith(("a/", "b/")) else name)
        elif line.startswith("--- ") and line[4:].strip() != "/dev/null":
            name = line[4:].strip()
            name = name[2:] if name.startswith(("a/", "b/")) else name
            if name not in files:
                files.append(name)
    return sorted(set(files))


def _size(patch_text: str) -> dict[str, int]:
    added = sum(
        1 for ln in patch_text.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    )
    removed = sum(
        1 for ln in patch_text.splitlines() if ln.startswith("-") and not ln.startswith("---")
    )
    return {"lines_added": added, "lines_removed": removed}


def _findings(root: Path) -> dict[str, int]:
    from ananke.plexus import intel
    from ananke.plexus.intel.api_contract import ApiContractError, build

    sections = intel.analyze(root).get("sections") or {}
    files = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    counts: dict[str, int] = {}
    try:
        for f in build(root, sections.get("api") or {}, files)["findings"]:
            counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    except ApiContractError:
        pass
    quality = sections.get("quality") or {}
    totals = quality.get("totals") or {}
    if isinstance(totals.get("max_complexity"), int):
        counts["quality:max_complexity"] = totals["max_complexity"]
    if isinstance(totals.get("test_functions"), int):
        counts["tests:test_functions"] = totals["test_functions"]
    return counts


class _Worktree:
    def __init__(self, root: Path, commit: str) -> None:
        self.root = root
        self.commit = commit
        self.tmp = tempfile.TemporaryDirectory(prefix="ananke-patch-")
        self.path = Path(self.tmp.name) / "wt"

    def __enter__(self) -> Path:
        git(self.root, "worktree", "add", "--detach", str(self.path), self.commit, timeout=300)
        return self.path

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(InsightError):
            git(self.root, "worktree", "remove", "--force", str(self.path), timeout=120)
        self.tmp.cleanup()


def evaluate(
    root: Path,
    patch: Path,
    *,
    base: str = "HEAD",
    test_command: str | None = None,
    run_tests: bool = False,
) -> dict[str, Any]:
    from ananke.plexus.insight.timemachine import compare, graph_of
    from ananke.plexus.insight.whatif import run as whatif

    root = root.resolve()
    if not (root / ".git").exists():
        raise InsightError(
            f"{root} isn't a git repository; the loop applies the patch in a git worktree"
        )
    if not patch.is_file():
        raise InsightError(f"{patch} doesn't exist")
    if patch.stat().st_size > MAX_PATCH_BYTES:
        raise InsightError(f"{patch.name} is over {MAX_PATCH_BYTES // 1_000_000} MB")
    raw = patch.read_bytes()
    text = raw.decode("utf-8", "replace")
    files = _changed_files(text)
    if not files:
        raise InsightError(
            f"{patch.name} changes no files; give a unified diff (`git diff > fix.patch`)"
        )
    commit = resolve_ref(root, base)
    patch_sha = hashlib.sha256(raw).hexdigest()
    with _Worktree(root, commit) as wt:
        before_graph = graph_of(wt)
        before_findings = _findings(wt)
        check = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(wt), "apply", "--check", str(patch.resolve())],  # noqa: S607
            capture_output=True,
            timeout=120,
            check=False,
        )
        if check.returncode != 0:
            return _report(
                patch,
                patch_sha,
                commit,
                files,
                text,
                applies=False,
                apply_error=check.stderr.decode("utf-8", "replace").strip()[:500],
            )
        git(wt, "apply", str(patch.resolve()), timeout=120)
        after_graph = graph_of(wt)
        after_findings = _findings(wt)
        delta = compare(before_graph, after_graph)
        sources = [f for f in files if (wt / f).is_file()]
        # tests reach a change through its callers, so choosing them follows dependants, three steps deep
        reached = whatif(wt, sources, depth=3, graph=after_graph) if sources else None
        tests = reached["tests_to_run"] if reached else []
        command = (
            shlex.split(test_command)
            if test_command
            else (
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]
                if tests
                else []
            )
        )
        result: dict[str, Any] = {
            "command": command,
            "status": "to run" if command else "nothing to run",
        }
        if run_tests and command:
            try:
                # the worktree's own code first, so the patched source is what's tested, not an installed copy
                paths = [str(wt / "src")] if (wt / "src").is_dir() else []
                env = {
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [*paths, str(wt), os.environ.get("PYTHONPATH", "")]
                    ),
                }
                done = subprocess.run(  # noqa: S603 - the user's own test command, given explicitly
                    command, cwd=wt, capture_output=True, timeout=TEST_TIMEOUT, check=False, env=env
                )
                tail = done.stdout.decode("utf-8", "replace").strip().splitlines()[-5:]
                result = {
                    "command": command,
                    "status": "passed" if done.returncode == 0 else "failed",
                    "exit_code": done.returncode,
                    "tail": tail,
                }
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = {"command": command, "status": "could not run", "error": str(exc)[:300]}
    findings_delta = {
        k: {"before": before_findings.get(k, 0), "after": after_findings.get(k, 0)}
        for k in sorted(set(before_findings) | set(after_findings))
        if before_findings.get(k, 0) != after_findings.get(k, 0)
    }
    return _report(
        patch,
        patch_sha,
        commit,
        files,
        text,
        applies=True,
        tests=result,
        graph_delta=delta["summary"],
        component_links=delta["component_links_added"],
        findings_delta=findings_delta,
        impacted=(reached or {}).get("summary"),
    )


def _report(
    patch: Path, patch_sha: str, commit: str, files: list[str], text: str, **parts: Any
) -> dict[str, Any]:
    risk = []
    if not parts.get("applies"):
        risk.append("the patch doesn't apply to the base revision")
    for k, v in (parts.get("findings_delta") or {}).items():
        if v["after"] > v["before"] and not k.startswith("tests:"):
            risk.append(f"{k} rose from {v['before']} to {v['after']}")
    if parts.get("component_links"):
        risk.append(f"{len(parts['component_links'])} new links between components")
    tests = parts.get("tests") or {}
    if tests.get("status") == "failed":
        risk.append("the tests failed")
    elif tests.get("status") in {"to run", "nothing to run"}:
        risk.append("tests weren't run (pass --run-tests to run them in the worktree)")
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "patch": {"file": patch.name, "sha256": patch_sha, "files": files, **_size(text)},
        "base": commit,
        **parts,
        "risk": risk,
        # ready means it applies and its tests pass; rises in the risk measures are for the reviewer to weigh
        "ready": bool(parts.get("applies")) and tests.get("status") == "passed",
    }
    stable = {k: v for k, v in report.items() if k not in {"approval", "tests"}}
    stable["tests"] = {k: v for k, v in tests.items() if k in {"command", "status", "exit_code"}}
    report["approval"] = digest(stable)  # timings and output text vary run to run; the facts don't
    report["digest"] = report["approval"]
    return report


def commit_branch(
    root: Path, patch: Path, report: dict[str, Any], approval: str, branch: str, message: str
) -> str:
    """Commit the evaluated patch to a new local branch, only for the exact approval digest of a passing report."""
    root = root.resolve()
    if approval != report["approval"]:
        raise InsightError(
            "the approval digest doesn't match this evaluation; re-run and approve what it prints"
        )
    if hashlib.sha256(patch.read_bytes()).hexdigest() != report["patch"]["sha256"]:
        raise InsightError("the patch changed since it was evaluated; evaluate it again")
    if not report.get("ready"):
        raise InsightError(
            "the evaluation isn't ready (it must apply and its tests must pass): "
            + "; ".join(report["risk"])
        )
    if not branch or branch.startswith("-") or " " in branch:
        raise InsightError(f"{branch!r} isn't a branch name")
    try:
        git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        raise InsightError(f"branch {branch} already exists; choose another name")
    except InsightError as exc:
        if "already exists" in str(exc):
            raise
    with _Worktree(root, report["base"]) as wt:
        git(wt, "switch", "-c", branch, timeout=60)
        git(wt, "apply", "--index", str(patch.resolve()), timeout=120)
        git(
            wt,
            "-c",
            "user.name=Ananke patch loop",
            "-c",
            "user.email=ananke@localhost.invalid",
            "commit",
            "-q",
            "-m",
            message,
            "-m",
            f"Evaluated by ananke triage patch: {approval}",
            timeout=120,
        )
        sha = git(wt, "rev-parse", "HEAD").strip()
    return sha


def to_markdown(r: dict[str, Any]) -> str:
    p = r["patch"]
    lines = [
        f"# Patch {p['file']}: {'ready for review' if r['ready'] else 'not ready'}",
        "",
        f"{len(p['files'])} files, +{p['lines_added']} -{p['lines_removed']} lines, against `{r['base'][:12]}`.",
        "",
    ]
    if not r.get("applies"):
        lines += ["It doesn't apply:", "", "```", r.get("apply_error", ""), "```", ""]
    else:
        t = r["tests"]
        lines += [f"Tests: **{t['status']}** (`{' '.join(t['command']) or '-'}`)", ""]
        if t.get("tail"):
            lines += ["```", *t["tail"], "```", ""]
        g = r["graph_delta"]
        lines += [
            f"Graph: relationships +{g['edges_added']} -{g['edges_removed']}, nodes +{g['nodes_added']} "
            f"-{g['nodes_removed']}.",
            "",
        ]
        for k, v in r["findings_delta"].items():
            lines.append(f"- {k}: {v['before']} → {v['after']}")
    lines += ["", "## Risk", "", *(f"- {x}" for x in r["risk"] or ["none found"]), ""]
    lines += [f"Approval digest: `{r['approval']}`", ""]
    return "\n".join(lines)

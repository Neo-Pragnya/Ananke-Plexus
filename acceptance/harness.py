"""Capability acceptance harness: Gherkin scenarios that drive the real CLI, plus a release gate.

Shared by Ananke Plexus and Dex Trove (keep the two copies identical; repo-specific steps live in conftest.py).

* Every scenario runs in a fresh **sandbox**: its own project folder (a git repo), its own HOME, no colour, wide
  columns. Nothing touches the developer's machine.
* Commands run through ``bash -c`` with the **target's** bin directory first on PATH, so ``ananke``, ``apm``,
  ``dex`` and ``python`` are the build under test (the dev venv by default; set ``ACCEPTANCE_BIN_DIR`` to a venv
  that has the built wheel installed to test exactly what ships).
* Scenarios are tagged with the capabilities they prove (``@cap_<id>`` from capabilities.yaml). After the run the
  **release gate** turns per-capability evidence into PROMOTE / CONDITIONAL / BLOCK, the way the WWI Week-12
  agent-evaluation gate did: blockers veto, unverified or known issues hold the release at CONDITIONAL, and every
  rule cites its evidence.
* ``@known_issue`` marks a scenario that states the *correct* behaviour of something known to be broken. It runs
  as a strict xfail: it is reported, it caps the decision at CONDITIONAL, and it fails loudly once the bug is fixed
  (so the tag gets removed).

Reports: ``acceptance/report/acceptance.json`` and ``summary.md``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pytest_bdd import given, parsers, then, when

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FIXTURES = HERE / "fixtures"
REPORT_DIR = HERE / "report"
CATALOG: dict[str, Any] = yaml.safe_load((HERE / "capabilities.yaml").read_text(encoding="utf-8"))
GENERIC_TAGS = {
    "known_issue": "states correct behaviour that is currently broken (strict xfail; caps the gate at CONDITIONAL)",
    "adversarial": "an attack probe; its legitimate twin must still work",
    "slow": "takes more than ~20 s",
    "needs_network": "needs internet access (skipped when ACCEPTANCE_OFFLINE=1)",
    "needs_chrome": "needs Google Chrome for browser journeys",
    "needs_site": "needs a built docs site (ACCEPTANCE_SITE_DIST=path/to/dist)",
}
DEFAULT_TIMEOUT = int(os.environ.get("ACCEPTANCE_TIMEOUT", "600"))


def bin_dir() -> Path:
    env = os.environ.get("ACCEPTANCE_BIN_DIR")
    return Path(env).resolve() if env else Path(sys.executable).parent


def cap_marker(cap_id: str) -> str:
    return "cap_" + re.sub(r"[^a-z0-9]+", "_", cap_id.lower()).strip("_")


# ---------------------------------------------------------------------------------------------------- sandbox
@dataclass
class Run:
    command: str
    code: int
    stdout: str
    stderr: str
    seconds: float

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def describe(self, limit: int = 2500) -> str:
        out = self.output
        if len(out) > limit:
            out = out[: limit // 2] + "\n…\n" + out[-limit // 2 :]
        return f"$ {self.command}\n[exit {self.code}, {self.seconds:.1f}s]\n{out}"


@dataclass
class Sandbox:
    base: Path
    runs: list[Run] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    json_cache: Any = None

    def __post_init__(self) -> None:
        self.project = self.base / "project"
        self.home = self.base / "home"
        self.project.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)

    @property
    def last(self) -> Run:
        assert self.runs, "no command has been run yet"
        return self.runs[-1]

    def env(self) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("ANANKE_", "DEX_", "UV_PUBLISH", "GITHUB_TOKEN", "VIRTUAL_ENV"))
        }
        env.update(
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            PATH=f"{bin_dir()}{os.pathsep}{os.environ.get('PATH', '')}",
            NO_COLOR="1",
            TERM="dumb",
            COLUMNS="200",
            PYTHONUTF8="1",
            PYTHONDONTWRITEBYTECODE="1",
            GIT_AUTHOR_NAME="Acceptance",
            GIT_AUTHOR_EMAIL="acceptance@example.invalid",
            GIT_COMMITTER_NAME="Acceptance",
            GIT_COMMITTER_EMAIL="acceptance@example.invalid",
            PROJECT=str(self.project),
            FIXTURES=str(FIXTURES),
            REPO=str(REPO),
            SANDBOX=str(self.base),
            SITE_DIST=os.environ.get("ACCEPTANCE_SITE_DIST", ""),
        )
        env.update(self.extra_env)
        return env

    def run(self, command: str, stdin: str | None = None, timeout: int | None = None) -> Run:
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=self.project,
                env=self.env(),
                input=stdin,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or DEFAULT_TIMEOUT,
                check=False,
            )
            run = Run(
                command, proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - started
            )
        except subprocess.TimeoutExpired as exc:
            out = (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            run = Run(
                command,
                -9,
                out,
                f"TIMEOUT after {timeout or DEFAULT_TIMEOUT}s",
                time.perf_counter() - started,
            )
        self.runs.append(run)
        self.json_cache = None
        return run

    def json(self) -> Any:
        if self.json_cache is None:
            text = self.last.stdout.strip()
            start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
            assert start >= 0, f"expected JSON output\n{self.last.describe()}"
            self.json_cache = json.loads(text[start:])
        return self.json_cache

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=self.project, env=self.env(), capture_output=True, check=True
        )

    def commit_all(self, message: str = "acceptance fixture") -> None:
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", message, "--allow-empty")


@pytest.fixture
def sandbox(tmp_path: Path, request: pytest.FixtureRequest) -> Sandbox:
    box = Sandbox(tmp_path)
    request.node._acceptance_sandbox = box  # read back by the gate for evidence
    return box


# ---------------------------------------------------------------------------------------------------- JSON paths
def json_get(data: Any, path: str) -> Any:
    """Tiny path syntax: ``a.b[0].c``, ``[2]``, ``length`` (len of the current value)."""
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        elif part == "length":
            cur = len(cur)
        elif isinstance(cur, list):
            cur = [item.get(part) if isinstance(item, dict) else None for item in cur]
        else:
            assert isinstance(cur, dict) and part in cur, f"no key {part!r} in {str(cur)[:300]}"
            cur = cur[part]
    return cur


def _literal(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip('"')


# ---------------------------------------------------------------------------------------------------- Given
@given("a fresh project")
def fresh_project(sandbox: Sandbox) -> None:
    shutil.copytree(
        FIXTURES / "projects" / CATALOG.get("default_fixture", "default"),
        sandbox.project,
        dirs_exist_ok=True,
    )
    sandbox.git("init", "-q", "-b", "main")
    sandbox.commit_all()


@given(parsers.parse('a fresh project from fixture "{name}"'))
def fresh_project_from(sandbox: Sandbox, name: str) -> None:
    shutil.copytree(FIXTURES / "projects" / name, sandbox.project, dirs_exist_ok=True)
    sandbox.git("init", "-q", "-b", "main")
    sandbox.commit_all()


@given("an empty directory")
def empty_directory(sandbox: Sandbox) -> None:
    assert not any(sandbox.project.iterdir())


@given(parsers.parse('the fixture "{name}" is copied to "{dest}"'))
def copy_fixture(sandbox: Sandbox, name: str, dest: str) -> None:
    src = FIXTURES / name
    target = sandbox.project / dest
    if src.is_dir():
        shutil.copytree(src, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


@given(parsers.parse('the file "{path}" with content:'))
def write_file(sandbox: Sandbox, path: str, docstring: str) -> None:
    target = sandbox.project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(docstring + "\n", encoding="utf-8")


def fake_credential(kind: str) -> str:
    """A freshly random, correctly *formatted* credential. Built at runtime so no secret-shaped string is ever
    committed (scanners and push protection rightly flag those). Never valid anywhere."""
    import secrets
    import string

    upper = string.ascii_uppercase + string.digits
    alnum = string.ascii_letters + string.digits

    def pick(alphabet: str, n: int) -> str:
        return "".join(secrets.choice(alphabet) for _ in range(n))

    kinds = {
        "aws-access-key": lambda: "AKIA" + pick(upper, 16),
        "aws-secret-key": lambda: pick(alnum + "/+", 40),
        "github-token": lambda: "ghp_" + pick(alnum, 36),
        "slack-token": lambda: (
            "xoxb-"
            + pick(string.digits, 12)
            + "-"
            + pick(string.digits, 12)
            + "-"
            + pick(alnum, 24)
        ),
    }
    assert kind in kinds, f"unknown credential kind {kind!r}; known: {sorted(kinds)}"
    return kinds[kind]()


@given(parsers.parse('the file "{path}" with content containing a fake "{kind}" credential:'))
def write_file_with_credential(sandbox: Sandbox, path: str, kind: str, docstring: str) -> None:
    """The docstring's ``{credential}`` placeholder becomes a fresh fake credential of that kind."""
    target = sandbox.project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        docstring.replace("{credential}", fake_credential(kind)) + "\n", encoding="utf-8"
    )


@given(parsers.parse('the environment variable "{name}" is "{value}"'))
def set_env(sandbox: Sandbox, name: str, value: str) -> None:
    """``$PROJECT``, ``$SANDBOX``, ``$HOME`` and ``$FIXTURES`` in the value are expanded."""
    for var, path in (
        ("$PROJECT", sandbox.project),
        ("$SANDBOX", sandbox.base),
        ("$HOME", sandbox.home),
        ("$FIXTURES", FIXTURES),
    ):
        value = value.replace(var, str(path))
    sandbox.extra_env[name] = value


@given(parsers.parse('I have run "{command}"'))
def have_run(sandbox: Sandbox, command: str) -> None:
    run = sandbox.run(command)
    assert run.code == 0, f"setup command failed\n{run.describe()}"


@given("the changes are committed")
def committed(sandbox: Sandbox) -> None:
    sandbox.commit_all("acceptance change")


# ---------------------------------------------------------------------------------------------------- When
@when(parsers.parse('I run "{command}"'))
def run_command(sandbox: Sandbox, command: str) -> None:
    sandbox.run(command)


@when(parsers.parse('I run "{command}" with input:'))
def run_with_input(sandbox: Sandbox, command: str, docstring: str) -> None:
    sandbox.run(command, stdin=docstring + "\n")


@when("I run:")
def run_script(sandbox: Sandbox, docstring: str) -> None:
    sandbox.run(docstring)


# ---------------------------------------------------------------------------------------------------- Then
@then("it succeeds")
def succeeds(sandbox: Sandbox) -> None:
    assert sandbox.last.code == 0, f"expected success\n{sandbox.last.describe()}"


@then("it fails")
def fails(sandbox: Sandbox) -> None:
    assert sandbox.last.code not in (0, -9), f"expected a clean failure\n{sandbox.last.describe()}"


@then(parsers.parse("the exit code is {code:d}"))
def exit_code(sandbox: Sandbox, code: int) -> None:
    assert sandbox.last.code == code, f"expected exit {code}\n{sandbox.last.describe()}"


@then("it fails cleanly")
def fails_cleanly(sandbox: Sandbox) -> None:
    """A non-zero exit with a readable error: no Python traceback, no success mark."""
    run = sandbox.last
    assert run.code not in (0, -9), f"expected a failure\n{run.describe()}"
    assert "Traceback" not in run.output, (
        f"crashed with a traceback instead of an error\n{run.describe()}"
    )
    first = next((line for line in run.output.splitlines() if line.strip()), "")
    assert not first.lstrip().startswith("✓"), (
        f"a failure must not be reported with a success mark\n{run.describe()}"
    )


@then("it does not crash")
def no_crash(sandbox: Sandbox) -> None:
    assert "Traceback (most recent call last)" not in sandbox.last.output, sandbox.last.describe()
    assert sandbox.last.code != -9, sandbox.last.describe()


@then(parsers.parse('the output contains "{text}"'))
def output_contains(sandbox: Sandbox, text: str) -> None:
    assert text in sandbox.last.output, f"missing {text!r}\n{sandbox.last.describe()}"


@then(parsers.parse('the output does not contain "{text}"'))
def output_lacks(sandbox: Sandbox, text: str) -> None:
    assert text not in sandbox.last.output, f"unexpected {text!r}\n{sandbox.last.describe()}"


@then(parsers.parse('the output matches "{pattern}"'))
def output_matches(sandbox: Sandbox, pattern: str) -> None:
    assert re.search(pattern, sandbox.last.output, re.M), (
        f"no match for /{pattern}/\n{sandbox.last.describe()}"
    )


@then("the output contains each of:")
def output_contains_all(sandbox: Sandbox, docstring: str) -> None:
    missing = [
        ln.strip()
        for ln in docstring.splitlines()
        if ln.strip() and ln.strip() not in sandbox.last.output
    ]
    assert not missing, f"missing {missing}\n{sandbox.last.describe()}"


@then(parsers.parse('the file "{path}" exists'))
def file_exists(sandbox: Sandbox, path: str) -> None:
    assert (sandbox.project / path).exists(), (
        f"{path} does not exist\n{sandbox.last.describe() if sandbox.runs else ''}"
    )


@then(parsers.parse('the file "{path}" does not exist'))
def file_absent(sandbox: Sandbox, path: str) -> None:
    assert not (sandbox.project / path).exists(), f"{path} should not exist"


@then(parsers.parse('the file "{path}" contains "{text}"'))
def file_contains(sandbox: Sandbox, path: str, text: str) -> None:
    content = (sandbox.project / path).read_text(encoding="utf-8", errors="replace")
    assert text in content, f"{path} lacks {text!r}:\n{content[:1500]}"


@then(parsers.parse('the file "{path}" does not contain "{text}"'))
def file_lacks(sandbox: Sandbox, path: str, text: str) -> None:
    content = (sandbox.project / path).read_text(encoding="utf-8", errors="replace")
    assert text not in content, f"{path} unexpectedly contains {text!r}"


@then(parsers.parse('a file matching "{pattern}" exists'))
def glob_exists(sandbox: Sandbox, pattern: str) -> None:
    assert list(sandbox.project.glob(pattern)), f"nothing matches {pattern}"


@then(parsers.parse('no file matching "{pattern}" exists'))
def glob_absent(sandbox: Sandbox, pattern: str) -> None:
    hits = list(sandbox.project.glob(pattern))
    assert not hits, f"unexpected {[str(h.relative_to(sandbox.project)) for h in hits]}"


@then(parsers.parse('the path "{path}" does not exist anywhere'))
def abs_absent(sandbox: Sandbox, path: str) -> None:
    resolved = Path(os.path.expandvars(path.replace("$SANDBOX", str(sandbox.base))))
    assert not resolved.exists(), (
        f"{resolved} exists: something executed code it should only have read"
    )


@then(parsers.parse('the JSON output at "{path}" equals {value}'))
def json_equals(sandbox: Sandbox, path: str, value: str) -> None:
    got = json_get(sandbox.json(), path)
    assert got == _literal(value), f"{path} = {got!r}, expected {value}\n{sandbox.last.describe()}"


@then(parsers.parse('the JSON output at "{path}" contains {value}'))
def json_contains(sandbox: Sandbox, path: str, value: str) -> None:
    got = json_get(sandbox.json(), path)
    want = _literal(value)
    assert want in got, f"{path} = {str(got)[:500]!r} lacks {want!r}"


@then(parsers.parse('the JSON output at "{path}" has at least {n:d} items'))
def json_min_items(sandbox: Sandbox, path: str, n: int) -> None:
    got = sandbox.json() if path in ("", ".") else json_get(sandbox.json(), path)
    assert len(got) >= n, f"{path} has {len(got)} items, expected ≥ {n}"


@then(parsers.parse("the JSON output has at least {n:d} items"))
def json_root_min(sandbox: Sandbox, n: int) -> None:
    assert len(sandbox.json()) >= n, (
        f"{len(sandbox.json())} items, expected ≥ {n}\n{sandbox.last.describe()}"
    )


@then(parsers.parse('the JSON file "{pattern}" at "{path}" equals {value}'))
def json_file_equals(sandbox: Sandbox, pattern: str, path: str, value: str) -> None:
    files = sorted(sandbox.project.glob(pattern), key=lambda p: p.stat().st_mtime)
    assert files, f"nothing matches {pattern}"
    got = json_get(json.loads(files[-1].read_text(encoding="utf-8")), path)
    assert got == _literal(value), f"{files[-1].name}:{path} = {got!r}, expected {value}"


@then(parsers.parse('the JSON file "{pattern}" at "{path}" contains {value}'))
def json_file_contains(sandbox: Sandbox, pattern: str, path: str, value: str) -> None:
    files = sorted(sandbox.project.glob(pattern), key=lambda p: p.stat().st_mtime)
    assert files, f"nothing matches {pattern}"
    got = json_get(json.loads(files[-1].read_text(encoding="utf-8")), path)
    want = _literal(value)
    assert want in got, f"{files[-1].name}:{path} = {str(got)[:400]!r} lacks {want!r}"


@then(parsers.parse("it finishes within {seconds:g} seconds"))
def within(sandbox: Sandbox, seconds: float) -> None:
    assert sandbox.last.seconds <= seconds, f"took {sandbox.last.seconds:.1f}s, budget {seconds}s"


# ---------------------------------------------------------------------------------------------------- CLI surface coverage
_SURFACE: list[str] = sorted(CATALOG.get("surface", []), key=lambda c: -len(c.split()))


def surface_commands(command: str) -> set[str]:
    """Which catalogued CLI commands a shell line exercises (options and env assignments ignored)."""
    hits: set[str] = set()
    binaries = set(CATALOG.get("binaries", []))
    for segment in re.split(r"&&|\|\||[;|]|\$\(", command):
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            continue
        words = [w for w in words if not w.startswith("-")]
        while words and "=" in words[0]:
            words = words[1:]
        if not words or Path(words[0]).name not in binaries:
            continue
        words[0] = Path(words[0]).name
        line = " ".join(words)
        for cmd in _SURFACE:
            if line == cmd or line.startswith(cmd + " "):
                hits.add(cmd)
                break
    return hits


# ---------------------------------------------------------------------------------------------------- gate (pytest hooks)
_RESULTS: dict[str, dict[str, Any]] = {}


def pytest_configure(config: pytest.Config) -> None:
    for cap in CATALOG["capabilities"]:
        config.addinivalue_line("markers", f"{cap_marker(cap['id'])}: {cap['title']}")
    for tag, text in GENERIC_TAGS.items():
        config.addinivalue_line("markers", f"{tag}: {text}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    offline = os.environ.get("ACCEPTANCE_OFFLINE") == "1"
    chrome = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
        chrome = chrome or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    for item in items:
        if item.get_closest_marker("known_issue"):
            item.add_marker(
                pytest.mark.xfail(strict=True, reason="known issue: states the correct behaviour")
            )
        if offline and item.get_closest_marker("needs_network"):
            item.add_marker(pytest.mark.skip(reason="ACCEPTANCE_OFFLINE=1"))
        if not chrome and item.get_closest_marker("needs_chrome"):
            item.add_marker(pytest.mark.skip(reason="Google Chrome not found (set CHROME=…)"))
        if not os.environ.get("ACCEPTANCE_SITE_DIST") and item.get_closest_marker("needs_site"):
            item.add_marker(
                pytest.mark.skip(reason="no built site (set ACCEPTANCE_SITE_DIST=…/dist)")
            )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if call.when not in ("setup", "call"):
        return report
    known = bool(item.get_closest_marker("known_issue"))
    if call.excinfo is None:
        outcome = "failed" if (call.when == "call" and known) else "passed"
        error = (
            "known_issue scenario now passes: remove the @known_issue tag"
            if outcome == "failed"
            else ""
        )
    elif call.excinfo.errisinstance(pytest.skip.Exception):
        outcome, error = "skipped", ""
    else:
        outcome, error = ("known_issue" if known else "failed"), str(call.excinfo.value)[:1500]
    box = getattr(item, "_acceptance_sandbox", None)
    evidence = {
        "scenario": item.name,
        "caps": sorted({m.name for m in item.iter_markers() if m.name.startswith("cap_")}),
        "phase": call.when,
        "outcome": outcome,
        "error": error,
        "seconds": round(call.duration, 2),
        "commands": [
            {"command": r.command, "exit": r.code, "seconds": round(r.seconds, 2)} for r in box.runs
        ]
        if box
        else [],
    }
    report.user_properties.append(("acceptance", json.dumps(evidence)))
    return report


_RANK = {"passed": 0, "skipped": 1, "known_issue": 2, "failed": 3}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    for key, value in report.user_properties:
        if key != "acceptance":
            continue
        ev = json.loads(value)
        cur = _RESULTS.get(report.nodeid)
        if cur is None:
            _RESULTS[report.nodeid] = ev
            continue
        cur["seconds"] += ev["seconds"]
        cur["commands"] = ev["commands"] or cur["commands"]
        if _RANK[ev["outcome"]] > _RANK[cur["outcome"]]:
            cur["outcome"], cur["error"] = ev["outcome"], ev["error"]


_PARTIAL = {"value": False}


def evaluate(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    caps = []
    for cap in CATALOG["capabilities"]:
        marker = cap_marker(cap["id"])
        mine = [r for r in results.values() if marker in r["caps"]]
        outcomes = [r["outcome"] for r in mine]
        if not mine:
            status = "not_run" if _PARTIAL["value"] else "missing"
        elif "failed" in outcomes:
            status = "fail"
        elif "known_issue" in outcomes:
            status = "known_issue"
        elif "passed" in outcomes:
            status = "pass"
        else:
            status = "unverified"
        caps.append(
            {
                "id": cap["id"],
                "title": cap["title"],
                "dimension": cap.get("dimension", "capability"),
                "blocker": bool(cap.get("blocker")),
                "status": status,
                "scenarios": {o: outcomes.count(o) for o in sorted(set(outcomes))},
                "evidence": [
                    r["scenario"] for r in mine if r["outcome"] in ("failed", "known_issue")
                ][:6]
                or [r["scenario"] for r in mine][:3],
            }
        )
    surface = CATALOG.get("surface", [])
    exercised: set[str] = set()
    for r in results.values():
        for c in r.get("commands", []):
            exercised |= surface_commands(c["command"])
    covered = sorted(c for c in surface if c in exercised)
    coverage = len(covered) / len(surface) if surface else 1.0
    target = float(CATALOG.get("surface_target", 0.6))
    rules = []
    for c in caps:
        if c["status"] in ("pass", "not_run"):
            continue
        level = "block" if c["blocker"] and c["status"] in ("fail", "missing") else "hold"
        rules.append(
            {"rule": f"{c['id']}: {c['status']}", "level": level, "evidence": c["evidence"]}
        )
    if coverage < target and not _PARTIAL["value"]:
        rules.append(
            {
                "rule": f"CLI surface coverage {coverage:.0%} < {target:.0%}",
                "level": "hold",
                "evidence": "see uncovered",
            }
        )
    decision = (
        "BLOCK"
        if any(r["level"] == "block" for r in rules)
        else "CONDITIONAL"
        if rules
        else "PROMOTE"
    )
    return {
        "decision": decision,
        "partial": _PARTIAL["value"],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": str(bin_dir()),
        "capabilities": caps,
        "rules": rules,
        "surface": {
            "covered": len(covered),
            "total": len(surface),
            "coverage": round(coverage, 3),
            "target": target,
            "uncovered": sorted(set(surface) - set(covered)),
        },
        "scenarios": list(results.values()),
    }


ICON = {
    "pass": "✅",
    "fail": "❌",
    "missing": "⬜",
    "unverified": "❔",
    "known_issue": "⚠️",
    "not_run": "·",
}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Acceptance report: {CATALOG.get('product', REPO.name)}",
        "",
        f"**Decision: {report['decision']}**{' (partial run: only the selected scenarios)' if report.get('partial') else ''}"
        f" · {report['generated_at']} · target `{report['target']}`",
        "",
        "| | Capability | Dimension | Blocker | Scenarios |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in report["capabilities"]:
        counts = ", ".join(f"{v} {k}" for k, v in c["scenarios"].items()) or "none"
        lines.append(
            f"| {ICON[c['status']]} | `{c['id']}`: {c['title']} | {c['dimension']} | {'yes' if c['blocker'] else ''} | {counts} |"
        )
    s = report["surface"]
    lines += [
        "",
        f"CLI surface exercised: {s['covered']}/{s['total']} ({s['coverage']:.0%}, target {s['target']:.0%})",
        "",
    ]
    if report["rules"]:
        lines += ["## Why not PROMOTE", ""]
        lines += [
            f"- **{r['level']}**: {r['rule']} ({'; '.join(r['evidence']) if isinstance(r['evidence'], list) else r['evidence']})"
            for r in report["rules"]
        ]
        lines.append("")
    if s["uncovered"]:
        lines += [
            "## Commands no scenario runs yet",
            "",
            ", ".join(f"`{c}`" for c in s["uncovered"]),
            "",
        ]
    return "\n".join(lines)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--acceptance-require",
        choices=["block", "conditional", "promote"],
        default="block",
        help="fail the session unless the gate reaches at least this decision (block = only fail on BLOCK)",
    )


def _is_partial(config: pytest.Config) -> bool:
    """A run filtered with -k / -m or pointed at single files judges only what it ran."""
    if config.getoption("keyword", "") or config.getoption("markexpr", ""):
        return True
    return any(
        Path(a.split("::")[0]).suffix in (".py", ".feature") or "::" in a for a in config.args
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput") or not _RESULTS:
        return  # xdist workers report to the controller, which writes the gate
    _PARTIAL["value"] = _is_partial(session.config)
    report = evaluate(_RESULTS)
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "acceptance.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (REPORT_DIR / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    require = session.config.getoption("--acceptance-require")
    rank = {"BLOCK": 0, "CONDITIONAL": 1, "PROMOTE": 2}
    needed = {"block": 1, "conditional": 1, "promote": 2}[require]
    if rank[report["decision"]] < needed:
        session.exitstatus = 1
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(report) + "\n")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    path = REPORT_DIR / "acceptance.json"
    if not _RESULTS or not path.exists():
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    tr = terminalreporter
    tr.section(
        f"acceptance gate: {report['decision']}{' (partial run)' if report.get('partial') else ''}"
    )
    for c in report["capabilities"]:
        tr.write_line(
            f"  {ICON[c['status']]} {c['id']:<28} {c['status']:<12} {'(blocker)' if c['blocker'] else ''}"
        )
    s = report["surface"]
    tr.write_line(
        f"  CLI surface {s['covered']}/{s['total']} ({s['coverage']:.0%}); report: {REPORT_DIR / 'summary.md'}"
    )

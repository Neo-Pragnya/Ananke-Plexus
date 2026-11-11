"""CAP-jenkins-triage-01..04: stages, the first primary failure and its cascade, related changes, a rerun plan."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.connectors import run
from ananke.plexus.jenkins_triage import (
    TriageError,
    jenkinsfile_commands,
    related_changes,
    to_markdown,
    triage,
)

CONSOLE = """Started by user admin
[2026-11-10T10:00:00.000Z] [Pipeline] stage
[2026-11-10T10:00:00.000Z] [Pipeline] { (Build)
[2026-11-10T10:00:05.000Z] + uv sync
[2026-11-10T10:00:40.000Z] [Pipeline] }
[2026-11-10T10:00:40.000Z] [Pipeline] // stage
[2026-11-10T10:00:40.000Z] [Pipeline] stage
[2026-11-10T10:00:40.000Z] [Pipeline] { (Test)
[2026-11-10T10:00:41.000Z] + pytest tests/api -q
[2026-11-10T10:01:30.000Z] FAILED tests/api/test_orders.py::test_create - assert 500 == 201 <b>
[2026-11-10T10:01:30.000Z] 1 failed, 41 passed in 48.2s
[2026-11-10T10:01:31.000Z] ERROR: script returned exit code 1
[2026-11-10T10:01:31.000Z] [Pipeline] }
[2026-11-10T10:01:31.000Z] [Pipeline] // stage
[2026-11-10T10:01:31.000Z] [Pipeline] stage
[2026-11-10T10:01:31.000Z] [Pipeline] { (Deploy)
Stage "Deploy" skipped due to earlier failure(s)
[2026-11-10T10:01:31.000Z] [Pipeline] }
[2026-11-10T10:01:31.000Z] [Pipeline] // stage
[2026-11-10T10:01:32.000Z] [Pipeline] stage
[2026-11-10T10:01:32.000Z] [Pipeline] { (Report)
[2026-11-10T10:01:33.000Z] ERROR: no test report found in reports/*.xml
[2026-11-10T10:01:33.000Z] [Pipeline] }
[2026-11-10T10:01:33.000Z] [Pipeline] // stage
Finished: FAILURE
"""
JENKINSFILE = """pipeline {
  agent any
  stages {
    stage('Build') { steps { sh 'uv sync' } }
    stage('Test') {
      steps {
        sh 'pytest tests/api -q'
      }
    }
    stage('Deploy') { steps { sh "docker build -t shop ." } }
  }
}
"""
CHANGED = [
    "backend/app/orders.py",
    "tests/api/test_orders.py",
    "frontend/src/App.tsx",
    "pyproject.toml",
    "Dockerfile",
]


def test_stages_first_failure_and_cascade() -> None:
    r = triage(CONSOLE, source="console.log")
    assert r["result"] == "FAILURE"
    assert r["first_failure"]["stage"] == "Test" and r["first_failure"]["line"] == 10
    assert r["first_failure"]["other_errors"] == 2
    assert r["cascade"] == ["Deploy", "Report"]
    status = {t["stage"]: t["status"] for t in r["timeline"]}
    assert status == {"Build": "passed", "Test": "failed", "Deploy": "skipped", "Report": "cascade"}
    seconds = {t["stage"]: t["seconds"] for t in r["timeline"]}
    assert seconds["Build"] == 40 and seconds["Test"] == 51
    assert r["summary"].startswith("The build failed at stage 'Test'")


def test_jenkinsfile_commands_per_stage() -> None:
    assert jenkinsfile_commands(JENKINSFILE) == {
        "Build": ["uv sync"],
        "Test": ["pytest tests/api -q"],
        "Deploy": ["docker build -t shop ."],
    }


def test_changes_are_related_by_named_paths_and_tools_only() -> None:
    related = {r["path"]: r["why"] for r in related_changes(["pytest tests/api -q"], CHANGED)}
    assert "tests/api/test_orders.py" in related and any(
        "named in a command" in w for w in related["tests/api/test_orders.py"]
    )
    assert "backend/app/orders.py" in related and "pyproject.toml" in related
    assert (
        "frontend/src/App.tsx" not in related and "Dockerfile" not in related
    )  # pytest doesn't touch them
    docker = {r["path"] for r in related_changes(["docker build -t shop ."], CHANGED)}
    assert docker == {"Dockerfile"}


def test_the_plan_reruns_from_the_first_failure() -> None:
    r = triage(CONSOLE, jenkinsfile=JENKINSFILE, changed=CHANGED, source="console.log")
    assert r["stage_commands"] == ["pytest tests/api -q"]
    assert r["rerun_plan"][0].startswith("Fix the first failure in 'Test'")
    assert (
        r["rerun_plan"][-1].startswith("Rerun from stage 'Test'")
        and "Deploy, Report" in r["rerun_plan"][-1]
    )
    text = to_markdown(r)
    assert "| Report | cascade |" in text and "<b>" not in text and "&lt;b>" in text


def test_failures_outside_stages_success_and_non_jenkins_logs() -> None:
    outside = triage(
        "Started by timer\nERROR: Couldn't find any revision to build\nFinished: FAILURE\n"
    )
    assert outside["first_failure"]["stage"] is None and "outside any stage" in outside["summary"]
    ok = triage("[Pipeline] { (Build)\n+ make\n[Pipeline] // stage\nFinished: SUCCESS\n")
    assert ok["first_failure"] is None and ok["rerun_plan"] == [] and "SUCCESS" in ok["summary"]
    with pytest.raises(TriageError, match="Jenkins console"):
        triage("just some text\nwith nothing in it\n")
    unstable = triage(CONSOLE.replace("Finished: FAILURE", "Finished: UNSTABLE"))
    assert unstable["summary"].startswith("The build was unstable")


def test_the_connector_verifies_the_report(tmp_path: Path) -> None:
    (tmp_path / "console.log").write_text(CONSOLE)
    out = run("jenkins-triage", tmp_path / "console.log")
    assert out["schema"] == "triage-report/v1" and out["cascade"] == ["Deploy", "Report"]


def test_cli_reads_local_files_and_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    (repo / "tests" / "api").mkdir(parents=True)
    (repo / "tests" / "api" / "test_orders.py").write_text("def test(): pass\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "change"], check=True)
    (tmp_path / "console.log").write_text(CONSOLE)
    (tmp_path / "Jenkinsfile").write_text(JENKINSFILE)
    res = CliRunner().invoke(
        app,
        [
            "triage",
            "jenkins",
            str(tmp_path / "console.log"),
            "--jenkinsfile",
            str(tmp_path / "Jenkinsfile"),
            "--base",
            "main~1",
            "--project",
            str(repo),
            "--json",
        ],
    )
    assert res.exit_code == 1  # a failure was found
    report = json.loads(res.stdout)
    assert [r["path"] for r in report["related_changes"]] == ["tests/api/test_orders.py"]
    bad = CliRunner().invoke(app, ["triage", "jenkins", str(tmp_path / "missing.log")])
    assert bad.exit_code == 2

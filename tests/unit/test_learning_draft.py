"""CAP-session-learning-01 and -03: a lesson drafted from a session, and checked against Dex for overlap."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus import learning_draft
from ananke.plexus.cli.app import app


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        check=True,
    )


@pytest.fixture
def session(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("X = 1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    (root / "app.py").write_text("X = 2  # SECRET_TOKEN=should-not-appear\n")
    (root / "junit.xml").write_text(
        '<testsuite><testcase classname="tests.test_app" name="test_x"><failure message="X was 1"/></testcase></testsuite>'
    )
    _git(root, "add", "-A")
    _git(
        root,
        "commit",
        "-qm",
        "Freeze time in the flaky clock tests",
        "-m",
        "Chose a fixed clock over retries because retries hid a real ordering bug.\nCo-Authored-By: someone",
    )
    return root


def test_a_draft_holds_names_decisions_and_failures_never_contents(session: Path) -> None:
    out = learning_draft.draft(
        session,
        "main~1",
        runs=[{"warnings": [{"code": "ARTIFACT_FAILED", "message": "report: no file"}]}],
    )
    md = out["markdown"]
    assert out["title"] == "Freeze time in the flaky clock tests" and out["files"] == [
        "app.py",
        "junit.xml",
    ]
    assert (
        out["decisions"][0].startswith("Chose a fixed clock over retries because")
        and len(out["decisions"]) == 1
    )
    assert (
        "tests.test_app.test_x: X was 1" in md
        and "run warning: ARTIFACT_FAILED: report: no file" in md
    )
    assert "SECRET_TOKEN" not in md and "X = 2" not in md  # names and messages only
    assert "## When this applies" in md and "## Technique" in md
    with pytest.raises(ValueError):
        learning_draft.draft(session, "no-such-ref")


def test_overlap_without_dex_says_it_wasnt_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(learning_draft.shutil, "which", lambda name: None)
    out = learning_draft.overlap("# Freeze time\n\nFlaky clock tests need a fixed clock.")
    assert out == {
        "checked": False,
        "reason": "Dex Trove isn't installed (pip install 'ananke-plexus[dex]')",
        "similar": [],
    }


def test_overlap_scores_dex_results_by_shared_words(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    items = [
        {
            "id": "x/flaky-clock",
            "title": "Flaky clock tests",
            "description": "freeze time with a fixed clock in flaky tests",
        },
        {"id": "y/css-grid", "title": "CSS grid", "description": "layout pages with grid"},
    ]

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(items), "")

    monkeypatch.setattr(learning_draft.shutil, "which", lambda name: "/usr/bin/dex")
    monkeypatch.setattr(learning_draft.subprocess, "run", fake_run)
    out = learning_draft.overlap(
        "# Freeze time\n\nFlaky clock tests need a fixed clock, frozen time and no retries."
    )
    assert (
        out["checked"]
        and seen["argv"][:2] == ["/usr/bin/dex", "search"]
        and "--json" in seen["argv"]
    )
    assert [s["id"] for s in out["similar"]] == ["x/flaky-clock"]
    assert {"clock", "flaky", "freeze", "time"} <= set(out["similar"][0]["shared"])


def test_cli(session: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    lesson = tmp_path / "lesson.md"
    res = runner.invoke(
        app, ["learning", "draft", "--base", "main~1", "-w", str(session), "-o", str(lesson)]
    )
    assert res.exit_code == 0, res.output
    assert lesson.read_text().startswith("# Freeze time in the flaky clock tests")
    again = runner.invoke(
        app, ["learning", "draft", "--base", "main~1", "-w", str(session), "-o", str(lesson)]
    )
    assert again.exit_code == 2  # never overwrites a draft someone may be editing
    res = runner.invoke(app, ["learning", "overlap", str(lesson), "--json"])
    assert res.exit_code == 0 and "checked" in json.loads(res.stdout)

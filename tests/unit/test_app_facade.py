"""The app facade contract (ananke-app/1): negotiation, idempotent runs, events, artifacts, cancellation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ananke.plexus import app as app_mod
from ananke.plexus.app import AppError, AppFacade


def _git(*args: str, cwd: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def ws(tmp_path: Path) -> AppFacade:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "pkg" / "cli.py").write_text(
        "from pkg.core import add\n\ndef main():\n    return add(1, 2)\n"
    )
    (tmp_path / ".gitignore").write_text(".ananke/\n")
    _git("init", "-q", cwd=tmp_path)
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    return AppFacade(tmp_path)


def test_negotiation_and_capabilities(ws: AppFacade) -> None:
    assert AppFacade.negotiate(["ananke-app/2", "ananke-app/1"]) == "ananke-app/1"
    with pytest.raises(AppError) as old:
        AppFacade.negotiate(["ananke-app/0"])
    assert (
        old.value.code == "UNSUPPORTED_VERSION"
        and "nothing is installed automatically" in old.value.message
    )
    caps = ws.capabilities()
    assert (
        caps["protocol"] == "ananke-app/1"
        and "graph.build" in caps["run_kinds"]
        and "submit_run" in caps["operations"]
    )


def test_a_graph_run_maps_to_the_real_harness(ws: AppFacade) -> None:
    run = ws.submit_run(ws.prepare_run("graph.build"), "key-1")
    assert (
        run["state"] == "succeeded"
        and run["complete"] is True
        and run["source_snapshot"].startswith("git:")
    )
    [art] = ws.list_artifacts(run["run_id"])
    assert art["name"] == "graph-snapshot.json" and art["producer"] == "ananke.graph.native"
    graph = json.loads(ws.read_artifact(run["run_id"], "graph-snapshot.json"))
    assert any("pkg/core.py" in json.dumps(n) for n in graph.get("nodes", []))
    events = ws.stream_events(run["run_id"])["events"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["type"] for e in events] == [
        "run.queued",
        "run.running",
        "artifact.registered",
        "run.succeeded",
    ]
    assert [e["type"] for e in ws.stream_events(run["run_id"], after=2)["events"]] == [
        "artifact.registered",
        "run.succeeded",
    ]


def test_idempotency_and_stale_plans(ws: AppFacade) -> None:
    prepared = ws.prepare_run("graph.build")
    first = ws.submit_run(prepared, "same-key")
    assert (
        ws.submit_run(prepared, "same-key")["run_id"] == first["run_id"]
    )  # duplicate submit → same run
    other = ws.prepare_run("graph.impact", {"changed_files": ["pkg/core.py"]})
    with pytest.raises(AppError) as conflict:
        ws.submit_run(other, "same-key")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    stale = ws.prepare_run("graph.build")
    (ws.workspace / "pkg" / "core.py").write_text("def add(a, b):\n    return b + a\n")
    with pytest.raises(AppError) as old:
        ws.submit_run(stale, "fresh-key")
    assert old.value.code == "STALE_PLAN" and old.value.retryable


def test_cancel_is_cooperative(ws: AppFacade) -> None:
    queued = ws.submit_run(ws.prepare_run("graph.build"), "q", start=False)
    assert queued["state"] == "queued"
    assert ws.cancel_run(queued["run_id"])["state"] == "cancelled"
    assert ws.drive(queued["run_id"])["state"] == "cancelled"  # a cancelled run never starts
    done = ws.submit_run(ws.prepare_run("graph.build"), "d")
    after = ws.cancel_run(done["run_id"])
    assert after["state"] == "succeeded" and after["warnings"][-1]["code"] == "ALREADY_FINISHED"


def test_external_renderer_registration_and_visible_failure(ws: AppFacade) -> None:
    run = ws.submit_run(ws.prepare_run("graph.build"), "r")
    ref = ws.register_artifact(
        run["run_id"],
        "explorer.html",
        b"<html></html>",
        media_type="text/html",
        producer="dev-guru/explorer-html@1",
        input_refs=["graph-snapshot.json"],
    )
    assert ref["sha256"] and ref["input_refs"] == ["graph-snapshot.json"]
    assert ws.read_artifact(run["run_id"], "explorer.html") == b"<html></html>"
    failed = ws.register_failure(run["run_id"], "dev-guru/pdf", "renderer crashed")
    assert failed["complete"] is False and failed["warnings"][-1]["code"] == "ARTIFACT_FAILED"
    assert ws.stream_events(run["run_id"])["events"][-1]["type"] == "artifact.failed"
    (ws.state / "runs" / run["run_id"] / "artifacts" / "explorer.html").write_bytes(b"tampered")
    with pytest.raises(AppError) as tamper:
        ws.read_artifact(run["run_id"], "explorer.html")
    assert tamper.value.code == "DIGEST_MISMATCH"
    with pytest.raises(AppError):
        ws.register_artifact(
            run["run_id"], "../escape", b"x", media_type="text/plain", producer="x"
        )


def test_event_gaps_are_reported_with_the_run_state(
    ws: AppFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mod, "EVENT_RETENTION", 2)
    run = ws.submit_run(ws.prepare_run("graph.build"), "g")
    out = ws.stream_events(run["run_id"], after=0)
    assert out["gap"] == {"from": 1, "to": 2} and out["run"]["state"] == "succeeded"
    assert [e["seq"] for e in out["events"]] == [3, 4]


def test_failures_are_states_not_exceptions(ws: AppFacade) -> None:
    bad = ws.submit_run(ws.prepare_run("graph.impact", {"changed_files": []}), "bad")
    assert bad["state"] == "failed" and bad["warnings"][0]["code"] == "INVALID_REQUEST"
    with pytest.raises(AppError) as kind:
        ws.prepare_run("nope")
    assert kind.value.code == "UNKNOWN_RUN_KIND"
    with pytest.raises(AppError) as missing:
        ws.get_run("run-missing")
    assert missing.value.code == "RUN_NOT_FOUND"


def test_read_only_projections_filter_cite_and_refuse_escapes(ws: AppFacade) -> None:
    with pytest.raises(AppError) as none:
        ws.code_view()
    assert none.value.code == "GRAPH_MISSING"
    ws.submit_run(ws.prepare_run("graph.build"), "p")
    view = ws.code_view(path_prefix="pkg/cli", kinds=["file", "function"])
    assert {n["path"] for n in view["nodes"]} == {"pkg/cli.py"} and view[
        "source_snapshot"
    ].startswith("git:")
    calls = ws.code_view(query="pkg")
    assert all(e["origin"] in ("extracted", "inferred") for e in calls["edges"])
    src = ws.read_source("pkg/core.py", start=1, end=1)
    assert (
        src["lines"] == ["def add(a, b):"] and src["total_lines"] == 2 and len(src["sha256"]) == 64
    )
    for bad, code in (
        ("../outside.py", "PATH_ESCAPE"),
        (".ananke/app/idempotency.json", "PATH_DENIED"),
        ("pkg/none.py", "SOURCE_NOT_FOUND"),
    ):
        with pytest.raises(AppError) as exc:
            ws.read_source(bad)
        assert exc.value.code == code
    (ws.workspace / ".env").write_text("X=1\n")
    with pytest.raises(AppError) as env:
        ws.read_source(".env")
    assert env.value.code == "PATH_DENIED"
    ev = ws.evidence_view()
    assert ev["total"] == 1 and ev["runs"][0]["artifact_refs"][0]["name"] == "graph-snapshot.json"


def test_architecture_view_never_creates_a_model(ws: AppFacade) -> None:
    from ananke.plexus.architecture.calm import _calm_path, init_calm

    with pytest.raises(AppError) as missing:
        ws.architecture_view()
    assert missing.value.code == "ARCHITECTURE_MISSING" and not _calm_path(ws.workspace).exists()
    init_calm(ws.workspace)
    view = ws.architecture_view()
    assert {"model", "validation", "drift"} <= set(view)

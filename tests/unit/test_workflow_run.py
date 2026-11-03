"""Running a compiled workflow through the facade, on the real scheduler and the real fake backend."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest
from registry_support import add

from ananke.plexus.app import AppFacade
from ananke.plexus.backends.base import BackendResult
from ananke.plexus.backends.registry import BackendRegistry
from ananke.plexus.registry.registry import Registry

SKILL = """---
name: {name}
description: A skill used to test running workflows.
version: 1.0.0
---
# {name}

Say ping.
"""


def learn(root: Path, name: str) -> None:
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL.format(name=name))
    Registry.for_project(root, create=True).learn(str(folder), namespace="local")


@pytest.fixture
def app(tmp_path: Path) -> AppFacade:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    learn(tmp_path, "first")
    learn(tmp_path, "second")
    return AppFacade(tmp_path)


DRAFT: dict[str, Any] = {
    "schema_version": "1.0.0",
    "workflow_id": "demo/flow",
    "nodes": [
        {
            "id": "a",
            "capability_ref": "local/first@1.0.0",
            "kind": "skill",
            "inputs": [{"name": "topic", "schema_ref": "text/v1"}],
            "outputs": [{"name": "report", "schema_ref": "report/v1"}],
        },
        {
            "id": "b",
            "capability_ref": "local/second@1.0.0",
            "kind": "skill",
            "inputs": [{"name": "report", "schema_ref": "report/v1"}],
            "outputs": [],
            "approval_class": "PLAN",
        },
    ],
    "edges": [
        {"id": "e", "source": "a", "source_port": "report", "target": "b", "target_port": "report"}
    ],
}


def start(app: AppFacade, key: str = "k", **overrides: Any) -> dict[str, Any]:
    draft = overrides.pop("draft", DRAFT)
    params = {
        "draft": draft,
        "backend": "fake",
        "plan_hash": app.preview_workflow(draft).get("plan_hash"),
        "approved": ["b"],
        "inputs": {"a.topic": "cats"},
        **overrides,
    }
    return app.submit_run(app.prepare_run("workflow.run", params), key)


def codes(run: dict[str, Any]) -> set[str]:
    return {w["code"] for w in run["warnings"]}


def failed_with(run: dict[str, Any], code: str, *mentions: str) -> None:
    assert run["state"] == "failed", run
    warning = next(w for w in run["warnings"] if w["code"] == code)
    assert all(m in warning["message"] for m in mentions)
    assert not [a for a in run["artifact_refs"] if a["name"].endswith(".output.txt")]


def test_steps_run_in_order_passing_outputs_with_evidence(
    app: AppFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    fake = BackendRegistry.load(app.workspace).get_backend("fake")
    original = type(fake).send

    def spy(self: Any, session: Any, task: Any) -> BackendResult:
        seen.append(task)
        return original(self, session, task)

    monkeypatch.setattr(type(fake), "send", spy)
    run = start(app)
    assert run["state"] == "succeeded"
    assert [t.task_id for t in seen] == ["a", "b"]
    assert "# first" in seen[0].instruction and seen[0].context["inputs"] == {"topic": "cats"}
    names = [a["name"] for a in run["artifact_refs"]]
    assert names == ["workflow-plan.json", "01-a.output.txt", "02-b.output.txt"]
    first_output = (
        app.state / "runs" / run["run_id"] / "artifacts" / "01-a.output.txt"
    ).read_text()
    assert seen[1].context["inputs"] == {"report": first_output}
    second = run["artifact_refs"][2]
    assert "artifact:01-a.output.txt" in second["input_refs"]
    assert any(r.startswith("capability:local/second@1.0.0#sha256:") for r in second["input_refs"])
    types = [e["type"] for e in app.stream_events(run["run_id"])["events"]]
    assert types.count("step.started") == 2 and types[-1] == "run.succeeded"


def test_a_simulated_run_is_labelled_and_never_complete(app: AppFacade) -> None:
    run = start(app)
    assert "SIMULATED_BACKEND" in codes(run) and run["complete"] is False


def test_only_the_plan_that_was_previewed_runs(app: AppFacade) -> None:
    failed_with(start(app, plan_hash="sha256:old"), "STALE_PLAN", "preview it again")


def test_a_capability_changing_after_preview_is_stale(app: AppFacade) -> None:
    previewed = app.preview_workflow(DRAFT)["plan_hash"]
    changed = copy.deepcopy(DRAFT)
    changed["nodes"][1]["retries"] = 2
    failed_with(start(app, draft=changed, plan_hash=previewed), "STALE_PLAN")


def test_nothing_runs_without_approval(app: AppFacade) -> None:
    failed_with(start(app, approved=[]), "APPROVAL_REQUIRED", "b")
    failed_with(start(app, key="k2", approved=["b", "ghost"]), "INVALID_REQUEST", "ghost")


@pytest.mark.parametrize(
    ("backend", "code"), [("nope", "BACKEND_NOT_FOUND"), ("", "INVALID_REQUEST")]
)
def test_the_backend_is_named_and_must_exist(app: AppFacade, backend: str, code: str) -> None:
    failed_with(start(app, backend=backend), code)


def test_an_unavailable_backend_is_retryable(
    app: AppFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = BackendRegistry.load(app.workspace).get_backend("fake")
    monkeypatch.setattr(type(fake), "available", lambda self: False)
    run = start(app)
    failed_with(run, "BACKEND_UNAVAILABLE")
    assert next(w for w in run["warnings"] if w["code"] == "BACKEND_UNAVAILABLE")["retryable"]


def test_missing_inputs_are_refused_before_anything_starts(app: AppFacade) -> None:
    failed_with(start(app, inputs={}), "INVALID_REQUEST", "a.topic")


def test_a_capability_that_would_run_code_is_refused_whole_even_if_the_draft_calls_it_a_skill(
    app: AppFacade,
) -> None:
    add(
        Registry.for_project(app.workspace),
        name="runner",
        kind="tool",
        namespace="local",
        approve=True,
    )
    disguised = copy.deepcopy(DRAFT)
    disguised["nodes"][1]["capability_ref"] = "local/runner@1.0.0"
    run = start(app, draft=disguised)
    failed_with(run, "NO_EXECUTOR", "b", "Nothing was started")
    assert "step.started" not in {e["type"] for e in app.stream_events(run["run_id"])["events"]}


def test_a_permission_the_backend_cant_provide_is_refused(app: AppFacade) -> None:
    add(
        Registry.for_project(app.workspace),
        name="needs-shell",
        namespace="local",
        approve=True,
        permissions={"shell": {"allow": ["make"]}},
    )
    draft = copy.deepcopy(DRAFT)
    draft["nodes"][1]["capability_ref"] = "local/needs-shell@1.0.0"
    draft["nodes"][1]["permission_profile"] = ["shell.allow"]
    failed_with(start(app, draft=draft), "PERMISSION_NOT_GRANTED", "shell.allow")


def test_a_step_that_fails_stops_its_dependents_and_fails_the_run(
    app: AppFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = BackendRegistry.load(app.workspace).get_backend("fake")
    sent: list[str] = []

    def fails(self: Any, session: Any, task: Any) -> BackendResult:
        sent.append(task.task_id)
        return BackendResult(task_id=task.task_id, ok=False, error="the agent gave up")

    monkeypatch.setattr(type(fake), "send", fails)
    run = start(app)
    failed_with(run, "WORKFLOW_FAILED", "a: the agent gave up")
    assert sent == ["a"]


def test_cancelling_between_steps_stops_the_rest_and_stays_cancelled(
    app: AppFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = BackendRegistry.load(app.workspace).get_backend("fake")
    sent: list[str] = []

    def cancels(self: Any, session: Any, task: Any) -> BackendResult:
        sent.append(task.task_id)
        run_id = next(d.name for d in (app.state / "runs").iterdir())
        app.cancel_run(run_id)
        return BackendResult(task_id=task.task_id, ok=True, output="done")

    monkeypatch.setattr(type(fake), "send", cancels)
    run = start(app)
    assert run["state"] == "cancelled" and sent == ["a"]
    assert run["complete"] is False


def test_the_same_key_returns_the_same_run_without_running_again(app: AppFacade) -> None:
    first = start(app)
    assert start(app)["run_id"] == first["run_id"]


def test_a_quarantined_capability_blocks_the_run(app: AppFacade) -> None:
    previewed = app.preview_workflow(DRAFT)["plan_hash"]
    Registry.for_project(app.workspace).quarantine("local/second@1.0.0", reason="under review")
    failed_with(start(app, plan_hash=previewed), "WORKFLOW_INVALID")

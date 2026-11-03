"""The facade used the way a gateway uses it: a worker, pollers and cancels at once."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.app import AppFacade


@pytest.fixture
def app(tmp_path: Path) -> AppFacade:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return AppFacade(tmp_path)


def in_parallel(count: int, work: Any) -> list[Any]:
    """Run ``work(i)`` in threads released together; return results, re-raising the first error."""
    barrier = threading.Barrier(count)
    results: list[Any] = [None] * count
    errors: list[BaseException] = []

    def go(i: int) -> None:
        barrier.wait()
        try:
            results[i] = work(i)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results


def test_a_cancel_survives_a_worker_saving_at_the_same_time(app: AppFacade) -> None:
    run_id = app.submit_run(app.prepare_run("graph.build"), "k", start=False)["run_id"]
    stop = threading.Event()
    errors: list[BaseException] = []

    def churn() -> None:
        while not stop.is_set():
            try:
                app.register_artifact(
                    run_id, "x.txt", b"y" * 50_000, media_type="text/plain", producer="test"
                )
            except BaseException as exc:
                errors.append(exc)
                return

    def poll() -> None:
        while not stop.is_set():
            try:
                app.get_run(run_id)
                app.stream_events(run_id)
            except BaseException as exc:
                errors.append(exc)
                return

    threads = [threading.Thread(target=f) for f in (churn, churn, poll, poll)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    app.cancel_run(run_id)
    time.sleep(0.3)
    state = app.get_run(run_id)["state"]
    stop.set()
    for t in threads:
        t.join()
    assert not errors
    assert state == "cancelled" and app.get_run(run_id)["state"] == "cancelled"


def test_one_key_gives_one_run_however_many_callers(app: AppFacade) -> None:
    prepared = app.prepare_run("graph.build")
    runs = in_parallel(8, lambda _: app.submit_run(prepared, "same", start=False)["run_id"])
    assert len(set(runs)) == 1
    assert len(list((app.state / "runs").iterdir())) == 1


def test_a_run_is_driven_once(app: AppFacade) -> None:
    calls: list[str] = []

    def kind(_app: AppFacade, run_id: str, _params: dict[str, Any]) -> None:
        calls.append(run_id)
        time.sleep(0.05)

    app.kinds["graph.build"] = kind
    run_id = app.submit_run(app.prepare_run("graph.build"), "k", start=False)["run_id"]
    in_parallel(6, lambda _: app.drive(run_id))
    assert calls == [run_id]
    assert app.get_run(run_id)["state"] == "succeeded"


def test_event_numbers_stay_unique_and_ordered(app: AppFacade) -> None:
    run_id = app.submit_run(app.prepare_run("graph.build"), "k", start=False)["run_id"]
    in_parallel(8, lambda i: [app._event(run_id, "step.note", {"i": i}) for _ in range(10)])
    path = app.state / "runs" / run_id / "events.jsonl"
    seqs = [json.loads(line)["seq"] for line in path.read_text().splitlines()]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)) == 81


def test_a_final_state_stands_against_a_late_save(app: AppFacade) -> None:
    run_id = app.submit_run(app.prepare_run("graph.build"), "k", start=False)["run_id"]
    stale = app.get_run(run_id)
    app.cancel_run(run_id)
    stale["warnings"].append({"code": "NOTE", "message": "late", "retryable": False})
    app._save(stale)
    run = app.get_run(run_id)
    assert run["state"] == "cancelled" and run["warnings"][-1]["code"] == "NOTE"
    assert app._transition(stale, "succeeded")["state"] == "cancelled"
    types = [e["type"] for e in app.stream_events(run_id)["events"]]
    assert "run.succeeded" not in types


def test_a_cancel_from_another_process_is_honoured(app: AppFacade, tmp_path: Path) -> None:
    run_id = app.submit_run(app.prepare_run("graph.build"), "k", start=False)["run_id"]
    code = (
        "from pathlib import Path; from ananke.plexus.app import AppFacade; "
        f"AppFacade(Path({str(tmp_path)!r})).cancel_run({run_id!r})"
    )
    import sys

    subprocess.run([sys.executable, "-c", code], check=True)
    assert app.get_run(run_id)["state"] == "cancelled"


def committed(root: Path) -> None:
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        + ["commit", "-qm", "init"],
        check=True,
    )


def test_harness_state_is_not_source(app: AppFacade) -> None:
    committed(app.workspace)
    clean = app.source_snapshot()
    assert "+dirty" not in clean
    first = app.submit_run(app.prepare_run("graph.build"), "one")
    assert first["state"] == "succeeded", first
    assert app.source_snapshot() == clean
    assert app.submit_run(app.prepare_run("graph.build"), "two")["state"] == "succeeded"


def test_a_real_source_change_still_makes_a_prepared_plan_stale(app: AppFacade) -> None:
    from ananke.plexus.app import AppError

    committed(app.workspace)
    prepared = app.prepare_run("graph.build")
    (app.workspace / "a.py").write_text("x = 2\n")
    with pytest.raises(AppError) as exc:
        app.submit_run(prepared, "k")
    assert exc.value.code == "STALE_PLAN"
    (app.workspace / "new.py").write_text("y = 1\n")
    assert app.source_snapshot() != prepared.source_snapshot

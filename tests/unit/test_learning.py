"""Learning candidates: capture with redaction, evaluate, review, promote only when every gate passes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ananke.plexus.learning import LearningError, capture, evaluate, gate, promote, review
from ananke.plexus.registry.providers import CapabilityRef, LocalDirectoryProvider, ProviderSet

DESC = "Stabilise a flaky test by freezing time and seeding randomness. Use when a test fails intermittently."
CASES = [
    {"id": "pos", "kind": "positive", "command": "true"},
    {"id": "reg", "kind": "regression", "command": "test -d ."},
]


def lesson(
    ws: Path, name: str = "freeze-time", cases: list[dict[str, str]] | None = None, **kw: str
) -> Path:
    return capture(
        ws,
        name,
        description=DESC,
        body="Freeze the clock, seed RNGs, re-run 20 times.",
        cases=cases or CASES,
        runs=["run-1"],
        **kw,
    )


def sources(tmp: Path, visibility: str = "internal") -> ProviderSet:
    (tmp / "team").mkdir(exist_ok=True)
    return ProviderSet(
        [
            LocalDirectoryProvider(
                "team", tmp / "team", namespace="team", writable=True, visibility=visibility
            ),
            LocalDirectoryProvider("ro", tmp, namespace="ro"),
        ]
    )


def test_capture_redacts_and_keeps_project_data_out(tmp_path: Path) -> None:
    leaky_body = (
        f"Edit {tmp_path}/src/app.py, then call the API with token " + "ghp_" + "a" * 36 + "."
    )
    folder = capture(tmp_path, "fix-flake", description=DESC, body=leaky_body, cases=CASES)
    text = (folder / "SKILL.md").read_text()
    assert (
        "<workspace>/src/app.py" in text
        and str(tmp_path) not in text
        and "ghp_" + "a" * 36 not in text
    )
    prov = json.loads((folder / "provenance.json").read_text())
    assert prov["visibility"] == "internal" and prov["redactions"] >= 1
    assert sorted(p.name for p in folder.iterdir()) == [
        "SKILL.md",
        "evaluation.json",
        "provenance.json",
    ]
    with pytest.raises(LearningError) as dup:
        capture(tmp_path, "fix-flake", description=DESC, body="x", cases=CASES)
    assert dup.value.code == "CANDIDATE_EXISTS"
    with pytest.raises(LearningError) as bad:
        capture(tmp_path, "Bad Name", description=DESC, body="x", cases=CASES)
    assert bad.value.code == "INVALID_NAME"


def test_failing_or_unreviewed_candidates_cannot_be_promoted(tmp_path: Path) -> None:
    ps = sources(tmp_path)
    folder = lesson(
        tmp_path, cases=[CASES[0], {"id": "reg", "kind": "regression", "command": "false"}]
    )
    assert "not evaluated yet" in gate(folder)
    with pytest.raises(LearningError) as no:
        evaluate(folder, tmp_path)
    assert no.value.code == "COMMANDS_NOT_ALLOWED"
    evaluate(folder, tmp_path, allow_commands=True)
    review(folder, approve=True, reviewer="Lead", reason="useful")
    with pytest.raises(LearningError) as failing:
        promote(folder, ps, "team", approved_by="Lead")
    assert "evaluation cases failed: reg" in failing.value.message
    assert not list((tmp_path / "team").iterdir())


def test_a_passing_approved_lesson_publishes_once_without_gate_files(tmp_path: Path) -> None:
    ps = sources(tmp_path)
    folder = lesson(tmp_path)
    evaluate(folder, tmp_path, allow_commands=True)
    review(folder, approve=False, reviewer="Lead", reason="too specific")
    assert any("review rejected" in p for p in gate(folder))
    time.sleep(0.01)
    review(folder, approve=True, reviewer="Lead", reason="generalised")
    assert gate(folder) == []
    receipt = promote(folder, ps, "team", approved_by="Lead")
    published = tmp_path / "team" / "freeze-time"
    assert sorted(p.name for p in published.iterdir()) == ["SKILL.md"]
    assert ps.resolve("team:team/freeze-time").digest == receipt["digest"]
    assert json.loads((folder / "provenance.json").read_text())["published"][0]["source"] == "team"
    with pytest.raises(LearningError) as again:
        promote(folder, ps, "team", approved_by="Lead")
    assert again.value.code == "VERSION_CONTENT_CONFLICT"
    assert ps.resolve(CapabilityRef.parse("team:freeze-time")).name == "freeze-time"


def test_destination_rules(tmp_path: Path) -> None:
    folder = lesson(tmp_path)
    evaluate(folder, tmp_path, allow_commands=True)
    review(folder, approve=True, reviewer="Lead", reason="ok")
    with pytest.raises(LearningError) as ro:
        promote(folder, sources(tmp_path), "ro", approved_by="Lead")
    assert "read-only" in ro.value.message
    with pytest.raises(LearningError) as public:
        promote(folder, sources(tmp_path, visibility="public"), "team", approved_by="Lead")
    assert "internal lesson can't go to public source" in public.value.message
    later = time.time() + 5
    os.utime(folder / "evaluation.json", (later, later))
    assert any("changed after the review" in p for p in gate(folder))


def test_evaluation_runs_no_shell(tmp_path: Path) -> None:
    """A case can't chain or redirect: the text after ``true`` is only arguments."""
    marker = tmp_path / "ran"
    chained = {"id": "pos", "kind": "positive", "command": f"true && touch {marker}"}
    evaluate(lesson(tmp_path, cases=[chained, CASES[1]]), tmp_path, allow_commands=True)
    assert not marker.exists()


def test_a_case_whose_command_cant_run_fails_instead_of_crashing(tmp_path: Path) -> None:
    missing = {"id": "pos", "kind": "positive", "command": "no-such-command-here"}
    folder = lesson(tmp_path, cases=[missing, CASES[1]])
    evaluate(folder, tmp_path, allow_commands=True)
    cases = json.loads((folder / "evaluation.json").read_text())["cases"]
    assert cases[0]["passed"] is False and cases[0]["exit"] == "FileNotFoundError"

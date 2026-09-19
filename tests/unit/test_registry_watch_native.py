"""Watcher backends: native (watchfiles/notify) and polling share one fingerprint-based scan."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ananke.plexus.registry import watcher
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.watcher import WatchReport, watch


def _skill(root: Path, version: str = "1.0.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ananke.yaml").write_text(
        f"kind: skill\nnamespace: core\nname: w\nversion: {version}\nlicense: {{expression: MIT}}\n"
    )


def _project(tmp_path: Path) -> tuple[Registry, Path]:
    proj = tmp_path / "p"
    reg = Registry.for_project(proj, create=True, actor="t")
    reg.init()
    (proj / ".ananke" / "skills").mkdir(parents=True)
    return reg, proj


def test_poll_backend_reports_itself(tmp_path: Path) -> None:
    reg, proj = _project(tmp_path)
    _skill(proj / ".ananke/skills/w")
    seen: list[WatchReport] = []
    used = watch(reg, interval=0.01, max_rounds=2, on_change=seen.append, backend="poll")
    assert used == "poll" and len(seen) == 1  # second round: nothing changed


def test_auto_falls_back_to_poll_without_watchfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, _proj = _project(tmp_path)
    monkeypatch.setattr(watcher, "native_available", lambda: False)
    assert watch(reg, interval=0.01, max_rounds=1) == "poll"


def test_native_requested_but_unavailable_is_an_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, _proj = _project(tmp_path)
    monkeypatch.setattr(watcher, "native_available", lambda: False)
    with pytest.raises(RegistryError, match="registry-watch"):
        watch(reg, backend="native")


def test_native_backend_reacts_to_file_events(tmp_path: Path) -> None:
    pytest.importorskip("watchfiles")
    reg, proj = _project(tmp_path)
    skill = proj / ".ananke/skills/w"
    _skill(skill)
    seen: list[WatchReport] = []
    stop = threading.Event()
    result: list[str] = []

    def run() -> None:
        result.append(
            watch(
                reg,
                should_stop=stop.is_set,
                on_change=seen.append,
                max_rounds=3,
                backend="native",
            )
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while time.time() < deadline and not seen:  # initial scan
        time.sleep(0.05)
    assert seen, "initial scan should report the new skill"
    time.sleep(1.0)  # let the OS watcher arm
    _skill(skill, "1.1.0")
    while time.time() < deadline and len(seen) < 2:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=10)
    assert len(seen) >= 2, "a file event should trigger a re-scan"
    assert seen[1].changes[0].suggested_version == "1.1.0"
    assert result == ["native"] and not t.is_alive()


def test_events_under_managed_dirs_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    (root / "installed" / "x").mkdir(parents=True)
    (root / "mine").mkdir()
    assert not watcher._relevant([(1, str(root / "installed" / "x" / "f"))], [root])
    assert watcher._relevant([(1, str(root / "mine" / "f"))], [root])
    assert not watcher._relevant([(1, "/elsewhere/file")], [root])

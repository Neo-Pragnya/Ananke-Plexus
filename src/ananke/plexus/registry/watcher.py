"""Watcher for local capability directories (spec §94).

Detects changes under ``.ananke/skills`` / ``.ananke/agents`` (and any extra directories),
inspects and diffs them, and records *pending changes*. Default policy: detect
automatically, publish manually — nothing is registered unless ``policy.auto_register`` is
enabled.

Two interchangeable backends feed the same fingerprint-based scan:

* ``native`` — OS file events through the Rust ``notify`` crate, via the optional ``watchfiles``
  package (``pip install ananke-plexus[registry-watch]``). Near-instant, no busy polling.
* ``poll`` — stdlib only; re-scans on an interval. Used when ``watchfiles`` is missing.

Events are only a *hint that something changed*: every round still fingerprints the trees, so a
missed or coalesced event can never cause a wrong registration.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.importers.common import find_candidate_dirs, tree_fingerprint
from ananke.plexus.registry.learn import LearnCandidate, learn
from ananke.plexus.registry.payload import collect_directory

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


class PendingChange(BaseModel):
    path: str
    fingerprint: str
    uri: str | None = None
    action: str = ""
    suggested_version: str | None = None
    change_classes: list[str] = Field(default_factory=list)
    message: str = ""


class WatchReport(BaseModel):
    changes: list[PendingChange] = Field(default_factory=list)
    registered: list[str] = Field(default_factory=list)


def _state_path(registry: Registry) -> Path:
    return registry.root / "watch-state.json"


def _pending_path(registry: Registry) -> Path:
    return registry.root / "pending.json"


def watch_roots(registry: Registry, extra: list[Path] | None = None) -> list[Path]:
    roots = list(extra or [])
    if registry.project_root is not None:
        for sub in ("skills", "agents"):
            p = registry.project_root / ".ananke" / sub
            if p.is_dir():
                roots.append(p)
    return roots


def scan_once(registry: Registry, extra: list[Path] | None = None) -> WatchReport:
    state: dict[str, str] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        state = json.loads(_state_path(registry).read_text(encoding="utf-8"))
    report = WatchReport()
    new_state: dict[str, str] = {}
    for root in watch_roots(registry, extra):
        for d in find_candidate_dirs(root):
            rel_parts = d.resolve().relative_to(root.resolve()).parts
            if {"installed", "active", "linked"} & set(rel_parts):
                continue
            try:
                fp = tree_fingerprint(collect_directory(d).files)
            except Exception:  # noqa: S112 - unreadable dirs are skipped this round
                continue
            key = str(d)
            new_state[key] = fp
            if state.get(key) == fp:
                continue
            change = PendingChange(path=key, fingerprint=fp)
            try:
                lr = learn(registry, key, dry_run=True, register=False)
                cand: LearnCandidate | None = lr.candidates[0] if lr.candidates else None
            except Exception as exc:
                change.message = str(exc)
                cand = None
            if cand is not None:
                change.uri, change.action = cand.uri, cand.action
                change.suggested_version, change.message = (
                    cand.suggested_version or cand.version,
                    cand.message,
                )
                if cand.diff is not None:
                    change.change_classes = cand.diff.change_classes
                if registry.policy.auto_register and cand.action == "dry-run":
                    res = learn(registry, key)
                    report.registered += [c.uri or key for c in res.registered()]
            report.changes.append(change)
    _state_path(registry).write_text(
        json.dumps(new_state, indent=1, sort_keys=True), encoding="utf-8"
    )
    pending = [c.model_dump() for c in report.changes]
    _pending_path(registry).write_text(json.dumps(pending, indent=1), encoding="utf-8")
    return report


Backend = Literal["auto", "native", "poll"]
_MANAGED = {"installed", "active", "linked"}


def native_available() -> bool:
    try:
        import watchfiles  # noqa: F401
    except ImportError:
        return False
    return True


def _relevant(changes: Any, roots: list[Path]) -> bool:
    """Ignore events under APM-managed directories (our own materialisation)."""
    for _kind, raw in changes:
        path = Path(raw)
        for root in roots:
            try:
                parts = path.resolve().relative_to(root.resolve()).parts
            except ValueError:
                continue
            if not _MANAGED & set(parts):
                return True
    return False


def watch(
    registry: Registry,
    *,
    interval: float = 2.0,
    should_stop: Callable[[], bool] | None = None,
    on_change: Callable[[WatchReport], None] | None = None,
    extra: list[Path] | None = None,
    max_rounds: int | None = None,
    backend: Backend = "auto",
) -> str:
    """Run until stopped; returns the backend actually used (``native`` or ``poll``)."""
    if backend == "native" and not native_available():
        raise RegistryError(
            "native file watching needs watchfiles: pip install ananke-plexus[registry-watch]",
            code="WATCH_UNAVAILABLE",
        )
    roots = [r for r in watch_roots(registry, extra) if r.is_dir()]
    native = backend != "poll" and bool(roots) and native_available()

    rounds = 0

    def run_round() -> bool:
        nonlocal rounds
        rep = scan_once(registry, extra)
        if rep.changes and on_change:
            on_change(rep)
        rounds += 1
        return max_rounds is not None and rounds >= max_rounds

    if not native:
        while not (should_stop and should_stop()):
            if run_round():
                break
            time.sleep(interval)
        return "poll"

    import watchfiles

    if run_round():
        return "native"
    stopped = _StopFlag(should_stop)
    for changes in watchfiles.watch(
        *roots, debounce=400, rust_timeout=500, yield_on_timeout=True, stop_event=stopped
    ):
        if should_stop and should_stop():
            break
        if changes and _relevant(changes, roots) and run_round():
            break
    return "native"


class _StopFlag:
    """Adapts a ``should_stop()`` callable to the ``is_set()`` protocol watchfiles expects."""

    def __init__(self, should_stop: Callable[[], bool] | None) -> None:
        self._should_stop = should_stop

    def is_set(self) -> bool:
        return bool(self._should_stop and self._should_stop())


def read_pending(registry: Registry) -> list[PendingChange]:
    try:
        data = json.loads(_pending_path(registry).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [PendingChange.model_validate(d) for d in data]

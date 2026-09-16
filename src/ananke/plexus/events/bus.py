"""In-process event bus with optional JSONL audit log persistence."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ananke.plexus.events.models import Event

_Handler = Callable[[Event], None]


class EventBus:
    """Synchronous, in-process event bus.

    Subscribers register by event type (or ``"*"`` for all events).
    Events are also appended to a JSONL audit log when an audit path is configured.
    """

    def __init__(self, audit_log: Path | None = None) -> None:
        self._handlers: dict[str, list[_Handler]] = defaultdict(list)
        self._audit_log = audit_log

    def subscribe(self, event_type: str, handler: _Handler) -> None:
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: _Handler) -> None:
        self._handlers["*"].append(handler)

    def publish(self, event: Event) -> None:
        if self._audit_log is not None:
            self._audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.model_dump(), sort_keys=True) + "\n")

        for handler in list(self._handlers.get(event.event_type, [])):
            handler(event)
        for handler in list(self._handlers.get("*", [])):
            handler(event)

    def emit(
        self,
        event_type: str,
        payload: dict[str, object] | None = None,
        *,
        run_id: str | None = None,
        actor: str = "system",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Event:
        from ananke.plexus.events.models import Event as _Event

        ev = _Event(
            event_type=event_type,
            run_id=run_id,
            actor=actor,
            payload=payload or {},
            causation_id=causation_id,
        )
        if correlation_id:
            ev.correlation_id = correlation_id
        self.publish(ev)
        return ev


_default_bus: EventBus | None = None


def get_default_bus(audit_log: Path | None = None) -> EventBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus(audit_log=audit_log)
    return _default_bus


def reset_default_bus() -> None:
    global _default_bus
    _default_bus = None

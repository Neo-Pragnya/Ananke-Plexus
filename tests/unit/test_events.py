"""Tests for the event bus (A5)."""

import json

from ananke.plexus.events.bus import EventBus, get_default_bus, reset_default_bus
from ananke.plexus.events.models import (
    GATE_COMPLETED,
    REQUIREMENT_CAPTURED,
    SPEC_CREATED,
    Event,
)


def test_event_model_defaults():
    ev = Event(event_type=SPEC_CREATED, payload={"spec_id": "x"})
    assert ev.event_type == SPEC_CREATED
    assert ev.event_id
    assert ev.correlation_id
    assert ev.timestamp
    assert ev.actor == "system"


def test_bus_publish_calls_subscriber():
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(SPEC_CREATED, received.append)

    ev = bus.emit(SPEC_CREATED, {"id": "x"})

    assert len(received) == 1
    assert received[0].event_id == ev.event_id


def test_bus_wildcard_subscriber():
    bus = EventBus()
    all_events: list[Event] = []
    bus.subscribe_all(all_events.append)

    bus.emit(SPEC_CREATED)
    bus.emit(GATE_COMPLETED)

    assert len(all_events) == 2


def test_bus_audit_log(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    bus = EventBus(audit_log=log_path)
    bus.emit(REQUIREMENT_CAPTURED, {"req_id": "R1"})
    bus.emit(SPEC_CREATED, {"spec_id": "S1"})

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event_type"] == REQUIREMENT_CAPTURED


def test_bus_no_cross_type_delivery():
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(SPEC_CREATED, received.append)

    bus.emit(GATE_COMPLETED)

    assert len(received) == 0


def test_default_bus_singleton():
    reset_default_bus()
    b1 = get_default_bus()
    b2 = get_default_bus()
    assert b1 is b2
    reset_default_bus()


def test_emit_with_correlation():
    bus = EventBus()
    ev = bus.emit(SPEC_CREATED, correlation_id="corr-123", causation_id="caus-456")
    assert ev.correlation_id == "corr-123"
    assert ev.causation_id == "caus-456"

"""Built-in event subscribers."""

from __future__ import annotations

import sys

from ananke.plexus.events.bus import EventBus
from ananke.plexus.events.models import Event


def console_subscriber(event: Event) -> None:
    print(f"[event] {event.event_type} actor={event.actor}", file=sys.stderr)


def register_console_subscriber(bus: EventBus) -> None:
    bus.subscribe_all(console_subscriber)

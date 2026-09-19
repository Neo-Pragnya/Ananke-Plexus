"""Registry event names (spec §59, §93): audit-log types and their event-bus counterparts."""

from __future__ import annotations

from ananke.plexus.events.bus import EventBus

# audit log (registry_events) type -> Ananke event-bus type
EVENT_MAP: dict[str, str] = {
    "artifact.discovered": "registry.source.discovered",
    "artifact.imported": "registry.artifact.imported",
    "artifact.registered": "registry.version.registered",
    "artifact.yanked": "registry.version.yanked",
    "artifact.unyanked": "registry.version.unyanked",
    "artifact.deprecated": "registry.version.deprecated",
    "artifact.quarantined": "registry.version.quarantined",
    "artifact.archived": "registry.version.archived",
    "artifact.purged": "registry.version.purged",
    "artifact.metadata": "registry.version.metadata",
    "trust.approved": "registry.trust.changed",
    "trust.verified": "registry.trust.changed",
    "trust.restricted": "registry.trust.changed",
    "trust.quarantined": "registry.trust.changed",
    "trust.discovered": "registry.trust.changed",
    "channel.changed": "registry.channel.changed",
    "alias.updated": "registry.alias.updated",
    "activation.changed": "registry.activation.changed",
    "lock.created": "registry.lock.generated",
    "docs.generated": "registry.docs.generated",
    "registry.imported": "registry.archive.imported",
    "registry.exported": "registry.archive.exported",
}


def publish(
    bus: EventBus | None,
    event_type: str,
    payload: dict[str, object],
    *,
    actor: str,
) -> None:
    if bus is None:
        return
    bus.emit(EVENT_MAP.get(event_type, f"registry.{event_type}"), payload, actor=actor)

"""Evaluator registry — discovery, enable/disable, metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluatorMetadata:
    id: str
    version: str = "1"
    description: str = ""
    dimension: str = ""
    deterministic: bool = True
    requires: list[str] = field(default_factory=list)
    network: bool = False
    license: str = "Apache-2.0"
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryEntry:
    evaluator: Any
    metadata: EvaluatorMetadata
    enabled: bool = True


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, *, evaluator: Any, metadata: EvaluatorMetadata) -> None:
        self._entries[metadata.id] = RegistryEntry(evaluator=evaluator, metadata=metadata)

    def get(self, evaluator_id: str) -> Any | None:
        entry = self._entries.get(evaluator_id)
        if entry and entry.enabled:
            return entry.evaluator
        return None

    def enable(self, evaluator_id: str) -> None:
        if evaluator_id in self._entries:
            self._entries[evaluator_id].enabled = True

    def disable(self, evaluator_id: str) -> None:
        if evaluator_id in self._entries:
            self._entries[evaluator_id].enabled = False

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "id": e.metadata.id,
                "version": e.metadata.version,
                "dimension": e.metadata.dimension,
                "deterministic": e.metadata.deterministic,
                "enabled": e.enabled,
                "network": e.metadata.network,
                "license": e.metadata.license,
            }
            for e in self._entries.values()
        ]

    def list_enabled(self) -> list[str]:
        return [eid for eid, e in self._entries.items() if e.enabled]


_default_registry = EvaluatorRegistry()


def get_default_registry() -> EvaluatorRegistry:
    return _default_registry

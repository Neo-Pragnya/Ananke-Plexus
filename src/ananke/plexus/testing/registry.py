"""AdapterRegistry — registry of all known test adapters."""

from __future__ import annotations

from typing import Any


class AdapterRegistry:
    """Simple registry that holds adapter instances and provides lookup."""

    def __init__(self, adapters: list[Any] | None = None) -> None:
        self._adapters: list[Any] = []
        if adapters:
            for adapter in adapters:
                self.register(adapter)

    def register(self, adapter: Any) -> None:
        """Register an adapter instance."""
        self._adapters.append(adapter)

    def all(self) -> list[Any]:
        """Return all registered adapters."""
        return list(self._adapters)

    def available(self) -> list[Any]:
        """Return adapters that report themselves as available."""
        return [a for a in self._adapters if a.available()]

    def get(self, adapter_id: str) -> Any | None:
        """Retrieve an adapter by its adapter_id."""
        for a in self._adapters:
            if getattr(a, "adapter_id", None) == adapter_id:
                return a
        return None

    @classmethod
    def default(cls) -> AdapterRegistry:
        """Build a registry with the default adapter set."""
        from ananke.plexus.testing.adapters import default_adapters

        return cls(default_adapters())

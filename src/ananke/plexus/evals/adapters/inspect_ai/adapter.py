"""Inspect AI adapter — lazy wrapper for controlled benchmark / sandbox evaluation."""

from __future__ import annotations

from typing import Any


def _inspect_available() -> bool:
    try:
        import inspect_ai  # noqa: F401

        return True
    except ImportError:
        return False


class InspectAIAdapter:
    adapter_id = "ananke.adapters.inspect_ai"
    adapter_version = "1"
    requires_package = "inspect-ai"
    license = "MIT"

    def available(self) -> bool:
        return _inspect_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "sandbox_execution": True,
            "dataset_integration": True,
            "scorer_integration": True,
            "adversarial_cases": True,
            "network": False,
        }

    def doctor(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "available": self.available(),
            "license": self.license,
            "status": "ok"
            if self.available()
            else "unavailable — pip install ananke-plexus[eval-inspect]",
        }

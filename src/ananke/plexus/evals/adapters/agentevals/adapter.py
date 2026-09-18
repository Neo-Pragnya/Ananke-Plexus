"""AgentEvals adapter — trajectory evaluation from recorded OTel traces."""

from __future__ import annotations

from typing import Any


def _agentevals_available() -> bool:
    try:
        import agentevals  # noqa: F401

        return True
    except ImportError:
        return False


class AgentEvalsAdapter:
    adapter_id = "ananke.adapters.agentevals"
    adapter_version = "1"
    requires_package = "agentevals"
    license = "Apache-2.0"

    def available(self) -> bool:
        return _agentevals_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "trajectory_matching": True,
            "otel_trace_evaluation": True,
            "strict_trajectory": True,
            "subset_trajectory": True,
            "network": False,
        }

    def doctor(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "available": self.available(),
            "license": self.license,
            "status": "ok"
            if self.available()
            else "unavailable — pip install ananke-plexus[eval-agentevals]",
        }

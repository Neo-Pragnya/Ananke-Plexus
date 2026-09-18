"""OpenEvals adapter."""

from __future__ import annotations

from typing import Any


def _openevals_available() -> bool:
    try:
        import openevals  # noqa: F401

        return True
    except ImportError:
        return False


class OpenEvalsAdapter:
    adapter_id = "ananke.adapters.openevals"
    adapter_version = "1"
    requires_package = "openevals"
    license = "MIT"

    def available(self) -> bool:
        return _openevals_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "criteria_evaluators": True,
            "rubric": True,
            "network": False,
        }

    def doctor(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "available": self.available(),
            "license": self.license,
            "status": "ok"
            if self.available()
            else "unavailable — pip install ananke-plexus[eval-openevals]",
        }

"""Ragas adapter — RAG evaluation metrics (faithfulness, precision, recall)."""

from __future__ import annotations

from typing import Any


def _ragas_available() -> bool:
    try:
        import ragas  # noqa: F401

        return True
    except ImportError:
        return False


class RagasAdapter:
    adapter_id = "ananke.adapters.ragas"
    adapter_version = "1"
    requires_package = "ragas"

    def available(self) -> bool:
        return _ragas_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "faithfulness": True,
            "context_precision": True,
            "context_recall": True,
            "answer_relevance": True,
            "network": True,
        }

    def doctor(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "available": self.available(),
            "note": "Verify approved license version at release time.",
            "status": "ok"
            if self.available()
            else "unavailable — pip install ananke-plexus[eval-ragas]",
        }

"""DeepEval adapter — lazy wrapper; never imported at module level in core package."""

from __future__ import annotations

from typing import Any


def _deepeval_available() -> bool:
    try:
        import deepeval  # noqa: F401

        return True
    except ImportError:
        return False


class DeepEvalAdapter:
    adapter_id = "ananke.adapters.deepeval"
    adapter_version = "1"
    requires_package = "deepeval"
    license = "Apache-2.0"

    def available(self) -> bool:
        return _deepeval_available()

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "task_completion": True,
            "tool_correctness": True,
            "plan_adherence": True,
            "answer_relevance": True,
            "hallucination": True,
            "contextual_precision": True,
            "network": True,
        }

    def evaluate(self, *, case: Any, trace: Any, context: Any) -> list[Any]:
        if not self.available():
            from ananke.plexus.evals.evaluators.base import skipped_score

            return [
                skipped_score(
                    "ananke.adapters.deepeval", "outcome", "deepeval", "deepeval not installed"
                )
            ]
        raise NotImplementedError(
            "DeepEval adapter: call deepeval metrics directly with this wrapper's context"
        )

    def doctor(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "available": self.available(),
            "license": self.license,
            "status": "ok"
            if self.available()
            else "unavailable — pip install ananke-plexus[eval-deepeval]",
            "note": "Confident AI platform integration is disabled by default (external SaaS).",
        }

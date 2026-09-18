"""HypothesisAdapter — stub adapter for Hypothesis property-based testing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class HypothesisAdapter:
    adapter_id = "hypothesis"

    def available(self) -> bool:
        try:
            import hypothesis  # noqa: F401

            return True
        except ImportError:
            return False

    def version(self) -> str | None:
        try:
            import hypothesis

            return hypothesis.__version__
        except ImportError:
            return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            kinds={"property", "stateful"},
            supports_seed=True,
            supports_timeout=True,
            languages=["python"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        return [
            TestDefinition(
                id="hypothesis-suite",
                kind=TestKind.PROPERTY,
                engine="hypothesis",
                description="Hypothesis property-based tests (run via pytest)",
                source=str(project_root),
            )
        ]

    def run(
        self,
        tests: list[TestDefinition],
        project_root: Path,
        timeout: int | None,
    ) -> list[TestResult]:
        # Hypothesis runs within pytest; return UNAVAILABLE as a standalone runner
        return [
            TestResult(
                test_id="hypothesis-suite",
                kind=TestKind.PROPERTY,
                engine="hypothesis",
                status=TestStatus.SKIPPED,
                started_at=datetime.now(tz=UTC),
                message="Hypothesis runs via pytest adapter",
            )
        ]

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "version": self.version(),
            "note": "Hypothesis runs as a pytest plugin; use PytestAdapter to invoke tests.",
        }

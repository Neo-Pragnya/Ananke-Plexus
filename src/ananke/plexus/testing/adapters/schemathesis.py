"""SchemathesisAdapter — stub adapter for schemathesis API schema testing."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class SchemathesisAdapter:
    adapter_id = "schemathesis"

    def available(self) -> bool:
        try:
            import schemathesis  # noqa: F401

            return True
        except ImportError:
            return False

    def version(self) -> str | None:
        try:
            import schemathesis

            return schemathesis.__version__
        except ImportError:
            return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            kinds={"api_schema"},
            supports_selection=True,
            network_required=True,
            languages=["python"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        if not self.available():
            return []
        openapi_candidates = list(project_root.glob("**/openapi*.yaml")) + list(
            project_root.glob("**/openapi*.json")
        )
        if not openapi_candidates:
            return []
        return [
            TestDefinition(
                id=f"schemathesis-{spec.stem}",
                kind=TestKind.API_SCHEMA,
                engine="schemathesis",
                description=f"schemathesis API schema test: {spec.name}",
                source=str(spec),
            )
            for spec in openapi_candidates[:5]
        ]

    def run(
        self,
        tests: list[TestDefinition],
        project_root: Path,
        timeout: int | None,
    ) -> list[TestResult]:
        if not self.available():
            return [
                TestResult(
                    test_id="schemathesis-suite",
                    kind=TestKind.API_SCHEMA,
                    engine="schemathesis",
                    status=TestStatus.UNAVAILABLE,
                    started_at=datetime.now(tz=UTC),
                    message="schemathesis is not installed",
                )
            ]
        results: list[TestResult] = []
        for test in tests:
            if test.source is None:
                continue
            started = datetime.now(tz=UTC)
            try:
                proc = subprocess.run(
                    ["python", "-m", "schemathesis", "run", test.source],
                    shell=False,
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=timeout,
                    text=True,
                )
            except Exception as exc:
                ended = datetime.now(tz=UTC)
                results.append(
                    TestResult(
                        test_id=test.id,
                        kind=TestKind.API_SCHEMA,
                        engine="schemathesis",
                        status=TestStatus.ERROR,
                        started_at=started,
                        ended_at=ended,
                        message=str(exc),
                    )
                )
                continue
            ended = datetime.now(tz=UTC)
            duration_ms = (ended - started).total_seconds() * 1000
            status = TestStatus.PASS if proc.returncode == 0 else TestStatus.FAIL
            results.append(
                TestResult(
                    test_id=test.id,
                    kind=TestKind.API_SCHEMA,
                    engine="schemathesis",
                    status=status,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    tool_version=self.version(),
                )
            )
        return results

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "version": self.version(),
        }

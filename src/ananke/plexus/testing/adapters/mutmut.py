"""MutmutAdapter — stub adapter for mutmut mutation testing."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class MutmutAdapter:
    adapter_id = "mutmut"

    def available(self) -> bool:
        return shutil.which("mutmut") is not None

    def version(self) -> str | None:
        try:
            proc = subprocess.run(
                ["mutmut", "--version"],
                shell=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
            return None
        except Exception:
            return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            kinds={"mutation"},
            supports_timeout=True,
            languages=["python"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        if not self.available():
            return []
        return [
            TestDefinition(
                id="mutmut-suite",
                kind=TestKind.MUTATION,
                engine="mutmut",
                description="mutmut mutation test suite",
                source=str(project_root),
            )
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
                    test_id="mutmut-suite",
                    kind=TestKind.MUTATION,
                    engine="mutmut",
                    status=TestStatus.UNAVAILABLE,
                    started_at=datetime.now(tz=UTC),
                    message="mutmut is not installed",
                )
            ]
        started = datetime.now(tz=UTC)
        try:
            proc = subprocess.run(
                ["mutmut", "run"],
                shell=False,
                cwd=str(project_root),
                capture_output=True,
                timeout=timeout,
                text=True,
            )
        except Exception as exc:
            ended = datetime.now(tz=UTC)
            return [
                TestResult(
                    test_id="mutmut-suite",
                    kind=TestKind.MUTATION,
                    engine="mutmut",
                    status=TestStatus.ERROR,
                    started_at=started,
                    ended_at=ended,
                    message=str(exc),
                )
            ]
        ended = datetime.now(tz=UTC)
        duration_ms = (ended - started).total_seconds() * 1000
        status = TestStatus.PASS if proc.returncode == 0 else TestStatus.WARN
        return [
            TestResult(
                test_id="mutmut-suite",
                kind=TestKind.MUTATION,
                engine="mutmut",
                status=status,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                tool_version=self.version(),
            )
        ]

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "version": self.version(),
        }

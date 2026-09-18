"""KaniAdapter — stub adapter for Kani formal verifier."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class KaniAdapter:
    adapter_id = "kani"

    def available(self) -> bool:
        return shutil.which("kani") is not None or shutil.which("cargo-kani") is not None

    def version(self) -> str | None:
        for cmd in (["kani", "--version"], ["cargo", "kani", "--version"]):
            try:
                proc = subprocess.run(
                    cmd,
                    shell=False,
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if proc.returncode == 0:
                    return proc.stdout.strip()
            except Exception:
                continue
        return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            kinds={"formal"},
            supports_timeout=True,
            languages=["rust"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        if not self.available():
            return []
        return [
            TestDefinition(
                id="kani-suite",
                kind=TestKind.FORMAL,
                engine="kani",
                description="Kani formal verification harness",
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
                    test_id="kani-suite",
                    kind=TestKind.FORMAL,
                    engine="kani",
                    status=TestStatus.UNAVAILABLE,
                    started_at=datetime.now(tz=UTC),
                    message="Kani is not available",
                )
            ]
        started = datetime.now(tz=UTC)
        try:
            proc = subprocess.run(
                ["cargo", "kani"],
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
                    test_id="kani-suite",
                    kind=TestKind.FORMAL,
                    engine="kani",
                    status=TestStatus.ERROR,
                    started_at=started,
                    ended_at=ended,
                    message=str(exc),
                )
            ]
        ended = datetime.now(tz=UTC)
        duration_ms = (ended - started).total_seconds() * 1000
        status = TestStatus.PASS if proc.returncode == 0 else TestStatus.FAIL
        return [
            TestResult(
                test_id="kani-suite",
                kind=TestKind.FORMAL,
                engine="kani",
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
            "kani_binary": shutil.which("kani"),
            "cargo_kani_available": shutil.which("cargo") is not None,
        }

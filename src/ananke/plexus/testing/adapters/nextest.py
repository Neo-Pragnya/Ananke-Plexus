"""NextestAdapter — cargo nextest adapter for Rust projects."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class NextestAdapter:
    adapter_id = "nextest"

    def available(self) -> bool:
        if shutil.which("cargo") is None:
            return False
        try:
            proc = subprocess.run(
                ["cargo", "nextest", "--version"],
                shell=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def version(self) -> str | None:
        try:
            proc = subprocess.run(
                ["cargo", "nextest", "--version"],
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
            kinds={"unit", "integration"},
            supports_selection=True,
            supports_parallelism=True,
            supports_timeout=True,
            supports_junit=True,
            languages=["rust"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        cargo_toml = project_root / "Cargo.toml"
        if not cargo_toml.exists():
            return []
        return [
            TestDefinition(
                id="nextest-suite",
                kind=TestKind.UNIT,
                engine="nextest",
                description="cargo nextest test suite",
                source=str(project_root),
            )
        ]

    def run(
        self,
        tests: list[TestDefinition],
        project_root: Path,
        timeout: int | None,
    ) -> list[TestResult]:
        started = datetime.now(tz=UTC)
        args = ["cargo", "nextest", "run"]
        try:
            proc = subprocess.run(
                args,
                shell=False,
                cwd=str(project_root),
                capture_output=True,
                timeout=timeout,
                text=True,
            )
        except subprocess.TimeoutExpired:
            ended = datetime.now(tz=UTC)
            return [
                TestResult(
                    test_id="nextest-suite",
                    kind=TestKind.UNIT,
                    engine="nextest",
                    status=TestStatus.ERROR,
                    started_at=started,
                    ended_at=ended,
                    message="cargo nextest timed out",
                )
            ]
        except Exception as exc:
            ended = datetime.now(tz=UTC)
            return [
                TestResult(
                    test_id="nextest-suite",
                    kind=TestKind.UNIT,
                    engine="nextest",
                    status=TestStatus.UNAVAILABLE,
                    started_at=started,
                    ended_at=ended,
                    message=str(exc),
                )
            ]

        ended = datetime.now(tz=UTC)
        duration_ms = (ended - started).total_seconds() * 1000
        status = TestStatus.PASS if proc.returncode == 0 else TestStatus.FAIL

        # Parse stderr for test counts (nextest writes stats there)
        metrics: dict[str, Any] = {}
        for line in (proc.stderr or "").splitlines():
            if "tests run" in line.lower() or "passed" in line.lower():
                metrics["summary_line"] = line.strip()
                break

        return [
            TestResult(
                test_id="nextest-suite",
                kind=TestKind.UNIT,
                engine="nextest",
                status=status,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                metrics=metrics,
                tool_version=self.version(),
            )
        ]

    def doctor(self) -> dict[str, Any]:
        cargo_available = shutil.which("cargo") is not None
        nextest_available = False
        if cargo_available:
            try:
                proc = subprocess.run(
                    ["cargo", "nextest", "--version"],
                    shell=False,
                    capture_output=True,
                    timeout=10,
                )
                nextest_available = proc.returncode == 0
            except Exception:
                nextest_available = False
        return {
            "available": self.available(),
            "cargo_available": cargo_available,
            "nextest_subcommand_available": nextest_available,
            "version": self.version(),
        }

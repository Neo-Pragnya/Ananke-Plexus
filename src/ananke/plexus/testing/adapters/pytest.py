"""PytestAdapter — lazy-import adapter for pytest."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


def read_coverage_percent(path: Path) -> float | None:
    """Total line coverage (0-100) from a ``coverage json`` / ``pytest-cov`` report, if present."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class PytestAdapter:
    adapter_id = "pytest"

    def __init__(self, collect_coverage: bool = False, coverage_source: str | None = None) -> None:
        # Coverage is opt-in: it slows the run and needs pytest-cov. The quality gate turns it on
        # when a ``coverage_threshold`` is configured.
        self.collect_coverage = collect_coverage
        self.coverage_source = coverage_source

    @staticmethod
    def _cov_available() -> bool:
        try:
            import pytest_cov  # type: ignore[import-untyped]  # noqa: F401

            return True
        except ImportError:
            return False

    def available(self) -> bool:
        try:
            import pytest  # noqa: F401

            return True
        except ImportError:
            return False

    def version(self) -> str | None:
        try:
            import pytest

            return pytest.__version__
        except ImportError:
            return None

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            kinds={"unit", "integration", "acceptance", "bdd", "snapshot", "coverage"},
            supports_selection=True,
            supports_parallelism=True,
            supports_timeout=True,
            supports_junit=True,
            supports_json=self._json_report_available(),
            supports_coverage=True,
            languages=["python"],
        )

    def _json_report_available(self) -> bool:
        try:
            import pytest_jsonreport  # noqa: F401

            return True
        except ImportError:
            return False

    def _xdist_available(self) -> bool:
        try:
            import xdist  # noqa: F401

            return True
        except ImportError:
            return False

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        return [
            TestDefinition(
                id="pytest-suite",
                kind=TestKind.UNIT,
                engine="pytest",
                description="pytest test suite",
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
        args = ["python", "-m", "pytest", "--tb=short", "-q"]
        use_json = self._json_report_available()
        json_report_path = project_root / ".ananke" / "tmp" / "pytest-report.json"
        if use_json:
            json_report_path.parent.mkdir(parents=True, exist_ok=True)
            args += ["--json-report", f"--json-report-file={json_report_path}"]
        cov_path = project_root / ".ananke" / "tmp" / "coverage.json"
        cov_requested = self.collect_coverage and self._cov_available()
        if cov_requested:
            cov_path.parent.mkdir(parents=True, exist_ok=True)
            cov_path.unlink(missing_ok=True)
            args += [f"--cov={self.coverage_source or '.'}", f"--cov-report=json:{cov_path}"]
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
                    test_id="pytest-suite",
                    kind=TestKind.UNIT,
                    engine="pytest",
                    status=TestStatus.ERROR,
                    started_at=started,
                    ended_at=ended,
                    message="pytest timed out",
                )
            ]
        except Exception as exc:
            ended = datetime.now(tz=UTC)
            return [
                TestResult(
                    test_id="pytest-suite",
                    kind=TestKind.UNIT,
                    engine="pytest",
                    status=TestStatus.UNAVAILABLE,
                    started_at=started,
                    ended_at=ended,
                    message=str(exc),
                )
            ]
        ended = datetime.now(tz=UTC)
        duration = (ended - started).total_seconds() * 1000

        if use_json and json_report_path.exists():
            results = self._parse_json_report(json_report_path, started, ended, duration)
        else:
            results = self._parse_stdout(proc.stdout, proc.returncode, started, ended, duration)
        if cov_requested:
            percent = read_coverage_percent(cov_path)
            if percent is not None:
                for result in results:
                    result.metrics["coverage_percent"] = percent
        return results

    def _parse_json_report(
        self,
        path: Path,
        started: datetime,
        ended: datetime,
        duration_ms: float,
    ) -> list[TestResult]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            summary = data.get("summary", {})
            passed = summary.get("passed", 0)
            failed = summary.get("failed", 0)
            errors = summary.get("error", 0)
            total_status = TestStatus.PASS if (failed + errors) == 0 else TestStatus.FAIL
            return [
                TestResult(
                    test_id="pytest-suite",
                    kind=TestKind.UNIT,
                    engine="pytest",
                    status=total_status,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    metrics={
                        "passed": passed,
                        "failed": failed,
                        "errors": errors,
                        "total": summary.get("total", passed + failed + errors),
                    },
                    tool_version=self.version(),
                )
            ]
        except Exception:
            return [
                TestResult(
                    test_id="pytest-suite",
                    kind=TestKind.UNIT,
                    engine="pytest",
                    status=TestStatus.ERROR,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    message="failed to parse JSON report",
                )
            ]

    def _parse_stdout(
        self,
        stdout: str,
        returncode: int,
        started: datetime,
        ended: datetime,
        duration_ms: float,
    ) -> list[TestResult]:
        status = TestStatus.PASS if returncode == 0 else TestStatus.FAIL
        metrics: dict[str, Any] = {}
        for line in stdout.splitlines():
            if "passed" in line or "failed" in line or "error" in line:
                metrics["summary_line"] = line.strip()
                break
        return [
            TestResult(
                test_id="pytest-suite",
                kind=TestKind.UNIT,
                engine="pytest",
                status=status,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                metrics=metrics,
                tool_version=self.version(),
            )
        ]

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "version": self.version(),
            "json_report_available": self._json_report_available(),
            "xdist_available": self._xdist_available(),
        }

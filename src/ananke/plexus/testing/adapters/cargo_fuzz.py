"""CargoFuzzAdapter — stub adapter for cargo-fuzz."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus


class CargoFuzzAdapter:
    adapter_id = "cargo-fuzz"

    def available(self) -> bool:
        if shutil.which("cargo") is None:
            return False
        try:
            proc = subprocess.run(
                ["cargo", "fuzz", "--version"],
                shell=False,
                capture_output=True,
                timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def version(self) -> str | None:
        try:
            proc = subprocess.run(
                ["cargo", "fuzz", "--version"],
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
            kinds={"fuzz"},
            supports_seed=True,
            supports_timeout=True,
            languages=["rust"],
        )

    def discover(self, project_root: Path, profile: str) -> list[TestDefinition]:
        fuzz_dir = project_root / "fuzz"
        if not self.available() or not fuzz_dir.exists():
            return []
        targets = [
            TestDefinition(
                id=f"cargo-fuzz-{t.stem}",
                kind=TestKind.FUZZ,
                engine="cargo-fuzz",
                description=f"cargo-fuzz target: {t.stem}",
                source=str(t),
            )
            for t in sorted((fuzz_dir / "fuzz_targets").glob("*.rs"))
        ]
        return targets or [
            TestDefinition(
                id="cargo-fuzz-suite",
                kind=TestKind.FUZZ,
                engine="cargo-fuzz",
                description="cargo-fuzz suite",
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
                    test_id="cargo-fuzz-suite",
                    kind=TestKind.FUZZ,
                    engine="cargo-fuzz",
                    status=TestStatus.UNAVAILABLE,
                    started_at=datetime.now(tz=UTC),
                    message="cargo-fuzz is not available",
                )
            ]
        results: list[TestResult] = []
        for test in tests:
            target = test.id.replace("cargo-fuzz-", "")
            started = datetime.now(tz=UTC)
            run_time = str(timeout or 30)
            try:
                proc = subprocess.run(
                    ["cargo", "fuzz", "run", target, "--", f"-max_total_time={run_time}"],
                    shell=False,
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=(timeout or 30) + 30,
                    text=True,
                )
            except Exception as exc:
                ended = datetime.now(tz=UTC)
                results.append(
                    TestResult(
                        test_id=test.id,
                        kind=TestKind.FUZZ,
                        engine="cargo-fuzz",
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
                    kind=TestKind.FUZZ,
                    engine="cargo-fuzz",
                    status=status,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    tool_version=self.version(),
                )
            )
        return results

    def doctor(self) -> dict[str, Any]:
        cargo_available = shutil.which("cargo") is not None
        fuzz_available = False
        if cargo_available:
            try:
                proc = subprocess.run(
                    ["cargo", "fuzz", "--version"],
                    shell=False,
                    capture_output=True,
                    timeout=10,
                )
                fuzz_available = proc.returncode == 0
            except Exception:
                fuzz_available = False
        return {
            "available": self.available(),
            "cargo_available": cargo_available,
            "cargo_fuzz_available": fuzz_available,
            "version": self.version(),
        }

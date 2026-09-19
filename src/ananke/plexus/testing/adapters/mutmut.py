"""MutmutAdapter — stub adapter for mutmut mutation testing."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ananke.plexus.testing.adapters.base import AdapterCapabilities
from ananke.plexus.testing.models.result import TestResult
from ananke.plexus.testing.models.test import TestDefinition, TestKind, TestStatus

_V2_LINE = re.compile(r"^(Killed|Survived|Timeout|Suspicious|Skipped)\b[^()]*\((\d+)\)", re.I)


def parse_mutation_score(text: str) -> float | None:
    """Mutation score (% of mutants killed) from mutmut output.

    Understands mutmut 3's ``export-cicd-stats`` JSON (``killed``/``survived``/``total``) and
    mutmut 2's ``results`` summary lines (``Survived 🙁 (3)``). Returns ``None`` when the output
    carries no counts, so callers never invent a score.
    """
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            killed = int(data.get("killed", 0))
            survived = int(data.get("survived", 0))
            timeout = int(data.get("timeout", 0))
            suspicious = int(data.get("suspicious", 0))
            considered = killed + survived + timeout + suspicious
            return round(100.0 * (killed + timeout) / considered, 2) if considered else None
        except (ValueError, TypeError, AttributeError):
            return None
    counts: dict[str, int] = {}
    for line in text.splitlines():
        m = _V2_LINE.match(line.strip())
        if m:
            counts[m.group(1).lower()] = int(m.group(2))
    if not counts:
        return None
    good = counts.get("killed", 0) + counts.get("timeout", 0)
    considered = good + counts.get("survived", 0) + counts.get("suspicious", 0)
    return round(100.0 * good / considered, 2) if considered else None


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
        metrics: dict[str, float | int | str | bool] = {}
        score = self._score(project_root)
        if score is not None:
            metrics["mutation_score"] = score
        return [
            TestResult(
                test_id="mutmut-suite",
                kind=TestKind.MUTATION,
                engine="mutmut",
                status=status,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                metrics=metrics,
                tool_version=self.version(),
            )
        ]

    def _score(self, project_root: Path) -> float | None:
        for cmd in (["mutmut", "export-cicd-stats"], ["mutmut", "results"]):
            try:
                proc = subprocess.run(
                    cmd,
                    shell=False,
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=120,
                    text=True,
                )
            except (OSError, subprocess.SubprocessError):
                continue  # next command; a missing score is reported as None, not guessed
            output = proc.stdout
            if cmd[1] == "export-cicd-stats":
                stats = project_root / "mutants" / "mutmut-cicd-stats.json"
                if stats.exists():
                    output = stats.read_text(encoding="utf-8")
            score = parse_mutation_score(output)
            if score is not None:
                return score
        return None

    def doctor(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "version": self.version(),
        }

"""A run in which no test executed is a warning, never a pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.testing.adapters.pytest import PytestAdapter
from ananke.plexus.testing.models import TestStatus

NOW = datetime.now(tz=UTC)


def test_json_report_with_zero_tests_warns(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"summary": {"total": 0, "collected": 0}}))
    [result] = PytestAdapter()._parse_json_report(report, NOW, NOW, 1.0)
    assert result.status is TestStatus.WARN and result.message == "no tests ran"


def test_json_report_with_tests_still_passes(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"summary": {"passed": 2, "total": 2}}))
    [result] = PytestAdapter()._parse_json_report(report, NOW, NOW, 1.0)
    assert result.status is TestStatus.PASS


def test_stdout_exit_code_5_warns() -> None:
    [result] = PytestAdapter()._parse_stdout("no tests ran in 0.01s", 5, NOW, NOW, 1.0)
    assert result.status is TestStatus.WARN

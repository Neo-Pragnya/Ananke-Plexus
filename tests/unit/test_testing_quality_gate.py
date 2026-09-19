"""Quality-gate threshold enforcement (coverage, mutation score, duration)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ananke.plexus.testing.adapters.mutmut import parse_mutation_score
from ananke.plexus.testing.adapters.pytest import read_coverage_percent
from ananke.plexus.testing.models.result import TestResult as Result
from ananke.plexus.testing.models.result import TestRun as Run
from ananke.plexus.testing.models.test import TestKind as Kind
from ananke.plexus.testing.models.test import TestStatus as Status
from ananke.plexus.testing.policy.gates import QualityGateVerdict, apply_quality_gate
from ananke.plexus.testing.policy.thresholds import (
    QualityConfigError,
    QualityGateConfig,
    load_quality_config,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _run(*, metrics: dict[str, float] | None = None, seconds: float = 5, status=Status.PASS):
    result = Result(
        test_id="t",
        kind=Kind.UNIT,
        engine="pytest",
        status=status,
        started_at=T0,
        metrics=dict(metrics or {}),
    )
    return Run(
        run_id="r",
        suite_id="s",
        profile="standard",
        started_at=T0,
        ended_at=T0 + timedelta(seconds=seconds),
        results=[result],
    )


def test_defaults_pass_without_thresholds() -> None:
    d = apply_quality_gate(_run(), QualityGateConfig())
    assert d.verdict == QualityGateVerdict.PASS and not d.blocks


def test_coverage_below_threshold_blocks() -> None:
    d = apply_quality_gate(
        _run(metrics={"coverage_percent": 71.5}), QualityGateConfig(coverage_threshold=80)
    )
    assert d.blocks and d.verdict == QualityGateVerdict.BLOCK
    assert "coverage 71.5% is below the 80% threshold" in d.reasons


def test_coverage_at_threshold_passes() -> None:
    d = apply_quality_gate(
        _run(metrics={"coverage_percent": 80.0}), QualityGateConfig(coverage_threshold=80)
    )
    assert d.verdict == QualityGateVerdict.PASS


def test_unmeasured_threshold_warns_instead_of_passing_silently() -> None:
    d = apply_quality_gate(
        _run(), QualityGateConfig(coverage_threshold=80, mutation_score_threshold=50)
    )
    assert d.verdict == QualityGateVerdict.WARN and not d.blocks
    assert len(d.reasons) == 2 and all("nothing measured it" in r for r in d.reasons)


def test_mutation_score_threshold() -> None:
    cfg = QualityGateConfig(mutation_score_threshold=60)
    assert apply_quality_gate(_run(metrics={"mutation_score": 59.9}), cfg).blocks
    assert not apply_quality_gate(_run(metrics={"mutation_score": 60}), cfg).blocks


def test_duration_budget() -> None:
    cfg = QualityGateConfig(max_duration_seconds=10)
    assert not apply_quality_gate(_run(seconds=10), cfg).blocks
    d = apply_quality_gate(_run(seconds=11), cfg)
    assert d.blocks and "over the 10s budget" in d.reasons[0]


def test_threshold_and_failure_reasons_accumulate() -> None:
    d = apply_quality_gate(
        _run(metrics={"coverage_percent": 10}, status=Status.FAIL),
        QualityGateConfig(coverage_threshold=50),
    )
    assert d.blocks and len(d.reasons) == 2


@pytest.mark.parametrize(
    "bad", ["coverage_threshold: 150", "coverage_threshold: -1", "max_duration_seconds: 0"]
)
def test_out_of_range_config_is_rejected_not_ignored(tmp_path: Path, bad: str) -> None:
    (tmp_path / ".ananke").mkdir()
    (tmp_path / ".ananke" / "quality.yaml").write_text(f"quality_gate:\n  {bad}\n")
    with pytest.raises(QualityConfigError):
        load_quality_config(tmp_path)


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".ananke").mkdir()
    (tmp_path / ".ananke" / "quality.yaml").write_text("quality_gate: [unterminated")
    with pytest.raises(QualityConfigError):
        load_quality_config(tmp_path)


def test_valid_config_loads(tmp_path: Path) -> None:
    (tmp_path / ".ananke").mkdir()
    (tmp_path / ".ananke" / "quality.yaml").write_text(
        "quality_gate:\n  coverage_threshold: 85\n  max_duration_seconds: 900\n"
    )
    cfg = load_quality_config(tmp_path)
    assert cfg.coverage_threshold == 85 and cfg.max_duration_seconds == 900
    assert load_quality_config(tmp_path / "missing").coverage_threshold is None


def test_read_coverage_percent(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"totals": {"percent_covered": 91.25}}))
    assert read_coverage_percent(p) == 91.25
    p.write_text("not json")
    assert read_coverage_percent(p) is None
    assert read_coverage_percent(tmp_path / "absent.json") is None


def test_parse_mutation_score_v3_json() -> None:
    assert (
        parse_mutation_score(json.dumps({"killed": 6, "survived": 2, "timeout": 2, "total": 10}))
        == 80.0
    )
    assert parse_mutation_score(json.dumps({"killed": 0, "survived": 0})) is None
    assert parse_mutation_score("{broken") is None


def test_parse_mutation_score_v2_text() -> None:
    out = "To apply a mutant on disk:\nTimeout ⏰ (1)\n\nSuspicious 🤔 (0)\n\nSurvived 🙁 (2)\n\nKilled 🎉 (7)\n"
    assert parse_mutation_score(out) == 80.0
    assert parse_mutation_score("nothing useful") is None


def test_cli_blocks_on_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from ananke.plexus.cli.app import app

    (tmp_path / ".ananke").mkdir()
    (tmp_path / ".ananke" / "quality.yaml").write_text("quality_gate:\n  coverage_threshold: 90\n")
    monkeypatch.setattr(
        "ananke.plexus.testing.api.run_quality_suite",
        lambda **_: _run(metrics={"coverage_percent": 50}),
    )
    res = CliRunner().invoke(app, ["test", "run", "--project", str(tmp_path)])
    assert res.exit_code == 1
    assert "coverage 50% is below the 90% threshold" in res.output

    (tmp_path / ".ananke" / "quality.yaml").write_text("quality_gate:\n  coverage_threshold: 500\n")
    bad = CliRunner().invoke(app, ["test", "run", "--project", str(tmp_path)])
    assert bad.exit_code == 2

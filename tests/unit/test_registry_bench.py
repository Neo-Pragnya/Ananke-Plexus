"""Benchmark harness: structure, regression comparison and CLI (never asserts wall-clock speed)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.registry.bench import (
    BenchReport,
    BenchResult,
    compare,
    load_report,
    run_benchmarks,
    save_report,
)
from ananke.plexus.registry.cli import registry_app


def _r(name: str, median: float) -> BenchResult:
    return BenchResult(name=name, iterations=1, median_ms=median, p95_ms=median, min_ms=median)


def test_runs_every_spec_operation_on_a_tiny_registry() -> None:
    report = run_benchmarks(size=12, iterations=2)
    names = [r.name for r in report.results]
    assert names == [
        "cas_insert",
        "register",
        "exact_lookup",
        "version_list",
        "resolve_small",
        "resolve_100_deps",
        "fts_search",
        "docs_full_build",
        "docs_incremental",
        "integrity_quick",
        "export_import",
    ]
    assert all(r.median_ms >= 0 and r.p95_ms >= r.min_ms for r in report.results)
    goals = {r.name: r.target_ms for r in report.results if r.target_ms}
    assert goals == {
        "exact_lookup": 5.0,
        "version_list": 10.0,
        "resolve_small": 20.0,
        "fts_search": 50.0,
        "docs_incremental": 100.0,
    }


def test_only_filter() -> None:
    report = run_benchmarks(size=5, iterations=2, only={"exact_lookup"})
    assert [r.name for r in report.results] == ["exact_lookup"]


def test_regression_needs_both_ratio_and_absolute_floor() -> None:
    base = BenchReport(
        size=1, results=[_r("a", 10.0), _r("b", 0.1), _r("c", 10.0), _r("gone", 5.0)]
    )
    cur = BenchReport(size=1, results=[_r("a", 30.0), _r("b", 1.0), _r("c", 12.0), _r("new", 99.0)])
    found = {r.name: r for r in compare(cur, base, tolerance=1.5)}
    assert set(found) == {"a"}  # b: 10x slower but inside the noise floor; c: within tolerance
    assert found["a"].ratio == 3.0


def test_report_roundtrip(tmp_path: Path) -> None:
    report = BenchReport(size=3, results=[_r("a", 1.5)])
    save_report(report, tmp_path / "sub" / "base.json")
    assert load_report(tmp_path / "sub" / "base.json") == report


def test_cli_saves_baseline_and_flags_regressions(tmp_path: Path) -> None:
    runner = CliRunner()
    base = tmp_path / "base.json"
    args = ["benchmark", "--size", "5", "--iterations", "2", "--only", "exact_lookup"]
    ok = runner.invoke(registry_app, [*args, "--save-baseline", str(base)])
    assert ok.exit_code == 0 and "exact_lookup" in ok.output and base.exists()

    data = json.loads(base.read_text())
    data["results"][0]["median_ms"] = 0.0001  # pretend the baseline was impossibly fast
    data["results"][0]["p95_ms"] = data["results"][0]["min_ms"] = 0.0001
    base.write_text(json.dumps(data))
    # the run is fast, so the absolute floor keeps this from being flagged
    assert runner.invoke(registry_app, [*args, "--baseline", str(base)]).exit_code == 0

    report = runner.invoke(registry_app, [*args, "--baseline", str(base), "--json"])
    assert json.loads(report.output)["report"]["size"] == 5

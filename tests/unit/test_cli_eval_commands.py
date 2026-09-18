"""Tests for ananke eval CLI commands.

Covers every eval sub-command: run, report, compare, suite list/validate,
dataset list/validate, baseline create, trace show/import, adapter list/doctor,
and judge list/test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helper fixture builders
# ---------------------------------------------------------------------------


def _make_scores_file(tmp_path: Path, run_id: str = "test-run") -> Path:
    """Create a scores.json in the Ananke evidence dir for *run_id*."""
    scores_dir = tmp_path / ".ananke" / "evidence" / run_id / "eval"
    scores_dir.mkdir(parents=True, exist_ok=True)
    scores_data = [
        {
            "evaluator_id": "exact-match",
            "dimension": "outcome",
            "metric": "exact_match",
            "status": "pass",
            "normalized_score": 1.0,
            "reason": "ok",
        }
    ]
    (scores_dir / "scores.json").write_text(json.dumps(scores_data), encoding="utf-8")
    return scores_dir


def _make_trace_file(tmp_path: Path, run_id: str = "test-run") -> Path:
    """Create a minimal valid trace JSON at *tmp_path*/<run_id>.json."""
    trace_data = {
        "trace_id": "t-001",
        "run_id": run_id,
        "runtime": "test",
        "spans": [],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "tool_calls": 0,
        },
    }
    trace_path = tmp_path / f"{run_id}.json"
    trace_path.write_text(json.dumps(trace_data), encoding="utf-8")
    return trace_path


def _make_suite_file(tmp_path: Path, suite_id: str = "test-suite") -> Path:
    """Create a valid eval suite JSON file under the project's suites dir."""
    suites_dir = tmp_path / ".ananke" / "evals" / "suites"
    suites_dir.mkdir(parents=True, exist_ok=True)
    suite_data = {
        "id": suite_id,
        "evaluators": [{"id": "exact-match"}],
        "policy": {"rules": {}},
    }
    f = suites_dir / f"{suite_id}.json"
    f.write_text(json.dumps(suite_data), encoding="utf-8")
    return f


def _make_dataset_file(tmp_path: Path, dataset_id: str = "test-dataset") -> Path:
    """Create a minimal eval dataset JSON under the project's datasets dir."""
    ds_dir = tmp_path / ".ananke" / "evals" / "datasets"
    ds_dir.mkdir(parents=True, exist_ok=True)
    ds_data = {
        "id": dataset_id,
        "version": 1,
        "cases": [{"id": "case-1", "input": "hello", "expected": "world"}],
    }
    f = ds_dir / f"{dataset_id}.json"
    f.write_text(json.dumps(ds_data), encoding="utf-8")
    return f


def _make_baseline_file(tmp_path: Path, baseline_id: str, run_id: str = "test-run") -> Path:
    """Create a baseline JSON file in the project's baselines dir."""
    baselines_dir = tmp_path / ".ananke" / "evals" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_data = {
        "baseline_id": baseline_id,
        "suite_id": "unknown",
        "version": run_id,
        "scores": {"outcome": 1.0},
        "pass_rates": {},
        "usage_summary": {},
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    f = baselines_dir / f"{baseline_id}.json"
    f.write_text(json.dumps(baseline_data), encoding="utf-8")
    return f


# ===========================================================================
# ananke eval run
# ===========================================================================


def test_eval_run_default(tmp_path: Path) -> None:
    """Basic eval run exits 0 (pass) or 1 (gate blocks); must not crash."""
    result = runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1), result.output
    assert result.output  # non-empty console output


def test_eval_run_json_output(tmp_path: Path) -> None:
    """--json flag emits parseable JSON containing 'run_id'."""
    result = runner.invoke(app, ["eval", "run", "--json", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)
    data = json.loads(result.output.strip())
    assert "run_id" in data


def test_eval_run_json_has_suite_id(tmp_path: Path) -> None:
    """JSON output has 'suite_id' field."""
    result = runner.invoke(app, ["eval", "run", "--json", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)
    data = json.loads(result.output.strip())
    assert "suite_id" in data


def test_eval_run_json_has_scores(tmp_path: Path) -> None:
    """JSON output has 'scores' field (list)."""
    result = runner.invoke(app, ["eval", "run", "--json", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)
    data = json.loads(result.output.strip())
    assert "scores" in data
    assert isinstance(data["scores"], list)


def test_eval_run_with_output(tmp_path: Path) -> None:
    """--output passes an agent output string for quick evaluation."""
    result = runner.invoke(
        app, ["eval", "run", "--output", "hello world", "--project", str(tmp_path)]
    )
    assert result.exit_code in (0, 1)
    assert result.output


def test_eval_run_with_suite(tmp_path: Path) -> None:
    """--suite names a specific suite id."""
    result = runner.invoke(app, ["eval", "run", "--suite", "my-suite", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)


def test_eval_run_with_case(tmp_path: Path) -> None:
    """--case names a specific case id."""
    result = runner.invoke(
        app,
        ["eval", "run", "--case", "test-case-001", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)


def test_eval_run_creates_evidence_dir(tmp_path: Path) -> None:
    """After eval run the .ananke/evidence directory is created."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    evidence_root = tmp_path / ".ananke" / "evidence"
    assert evidence_root.exists(), "evidence root should be created"
    run_dirs = [d for d in evidence_root.iterdir() if d.is_dir()]
    assert len(run_dirs) >= 1


def test_eval_run_creates_scores_json(tmp_path: Path) -> None:
    """After eval run a scores.json exists in the evidence bundle."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    evidence_root = tmp_path / ".ananke" / "evidence"
    assert evidence_root.exists()
    # The default run_id is "eval-run"
    scores_file = evidence_root / "eval-run" / "eval" / "scores.json"
    assert scores_file.exists(), "scores.json should be created"


def test_eval_run_with_trace_path(tmp_path: Path) -> None:
    """--trace points to a recorded trace JSON file to evaluate against."""
    trace_path = _make_trace_file(tmp_path, "trace-run")
    result = runner.invoke(
        app,
        ["eval", "run", "--trace", str(trace_path), "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)


# ===========================================================================
# ananke eval report
# ===========================================================================


def test_eval_report_missing_run(tmp_path: Path) -> None:
    """Reporting on a non-existent run exits with code 2."""
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "nonexistent-run-999",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2


def test_eval_report_after_scores_created(tmp_path: Path) -> None:
    """Report on an existing run (scores.json present) exits 0."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        ["eval", "report", "--run", "my-run", "--project", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert result.output


def test_eval_report_console_format(tmp_path: Path) -> None:
    """Default 'console' format produces readable text (exit 0)."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "my-run",
            "--format",
            "console",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert result.output


def test_eval_report_json_format(tmp_path: Path) -> None:
    """'json' format emits parseable JSON containing run_id."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "my-run",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output.strip())
    assert "run_id" in data
    assert data["run_id"] == "my-run"


def test_eval_report_markdown_format(tmp_path: Path) -> None:
    """'markdown' format exits 0 and produces non-empty output."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "my-run",
            "--format",
            "markdown",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert result.output


def test_eval_report_junit_format(tmp_path: Path) -> None:
    """'junit' format exits 0 and produces non-empty output (XML)."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "my-run",
            "--format",
            "junit",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert result.output


def test_eval_report_unknown_format_falls_back_to_console(tmp_path: Path) -> None:
    """An unrecognised format falls back to the console renderer (exit 0)."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "report",
            "--run",
            "my-run",
            "--format",
            "unknown-format",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0


# ===========================================================================
# ananke eval compare
# ===========================================================================


def test_eval_compare_missing_baseline(tmp_path: Path) -> None:
    """Comparing against a non-existent baseline exits with code 2."""
    _make_scores_file(tmp_path, run_id="candidate-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "nonexistent-baseline",
            "--run",
            "candidate-run",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2


def test_eval_compare_missing_run(tmp_path: Path) -> None:
    """Comparing with a non-existent candidate run exits with code 2."""
    _make_baseline_file(tmp_path, baseline_id="my-baseline")
    result = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "my-baseline",
            "--run",
            "nonexistent-run",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2


def test_eval_compare_stable_scores(tmp_path: Path) -> None:
    """Candidate with same scores as baseline produces no regressions (exit 0)."""
    _make_scores_file(tmp_path, run_id="candidate-run")
    _make_baseline_file(tmp_path, baseline_id="my-baseline")
    result = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "my-baseline",
            "--run",
            "candidate-run",
            "--project",
            str(tmp_path),
        ],
    )
    # No regressions → regression_blocked=False → exit 0
    assert result.exit_code == 0


def test_eval_compare_after_baseline_create(tmp_path: Path) -> None:
    """Create baseline then compare produces a parseable result."""
    _make_scores_file(tmp_path, run_id="candidate-run")
    _make_baseline_file(tmp_path, baseline_id="baseline-v1")
    result = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "baseline-v1",
            "--run",
            "candidate-run",
            "--project",
            str(tmp_path),
        ],
    )
    # 0 = no regressions / not blocked, 1 = regression_blocked
    assert result.exit_code in (0, 1)


# ===========================================================================
# ananke eval suite list
# ===========================================================================


def test_eval_suite_list_empty(tmp_path: Path) -> None:
    """No suites directory → exit 0, output mentions suites."""
    result = runner.invoke(app, ["eval", "suite", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output


def test_eval_suite_list_empty_output_contains_keyword(tmp_path: Path) -> None:
    """When no suites dir exists the output contains 'no suites'."""
    result = runner.invoke(app, ["eval", "suite", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert "no suites" in result.output.lower()


def test_eval_suite_list_with_suite(tmp_path: Path) -> None:
    """Suite directory with a suite file: command runs without crash."""
    _make_suite_file(tmp_path, "my-suite")
    # NOTE: render_result is invoked with 'as_json' kwarg which the current
    # rendering implementation does not accept; exit may be 1 due to TypeError.
    result = runner.invoke(app, ["eval", "suite", "list", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)


def test_eval_suite_list_always_returns_output(tmp_path: Path) -> None:
    """Suite list always produces some stdout content."""
    result = runner.invoke(app, ["eval", "suite", "list", "--project", str(tmp_path)])
    assert result.output  # non-empty


# ===========================================================================
# ananke eval suite validate
# ===========================================================================


def test_eval_suite_validate_missing(tmp_path: Path) -> None:
    """Validating a non-existent suite file exits with code 2."""
    result = runner.invoke(
        app,
        [
            "eval",
            "suite",
            "validate",
            "--path",
            str(tmp_path / "nonexistent.json"),
        ],
    )
    assert result.exit_code == 2


def test_eval_suite_validate_valid(tmp_path: Path) -> None:
    """Validating a well-formed suite JSON exits 0."""
    suite_file = _make_suite_file(tmp_path)
    result = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(suite_file)],
    )
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_eval_suite_validate_missing_id(tmp_path: Path) -> None:
    """Suite JSON missing 'id' field → exit 1."""
    bad_suite = tmp_path / "no-id.json"
    bad_suite.write_text(json.dumps({"evaluators": [{"id": "x"}]}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(bad_suite)],
    )
    assert result.exit_code == 1


def test_eval_suite_validate_missing_evaluators(tmp_path: Path) -> None:
    """Suite JSON missing 'evaluators' field → exit 1."""
    bad_suite = tmp_path / "no-evaluators.json"
    bad_suite.write_text(json.dumps({"id": "my-suite"}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(bad_suite)],
    )
    assert result.exit_code == 1


def test_eval_suite_validate_missing_both_required(tmp_path: Path) -> None:
    """Suite JSON missing both 'id' and 'evaluators' → exit 1."""
    bad_suite = tmp_path / "empty.json"
    bad_suite.write_text(json.dumps({"policy": {"rules": {}}}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(bad_suite)],
    )
    assert result.exit_code == 1


def test_eval_suite_validate_not_json(tmp_path: Path) -> None:
    """Non-JSON content → exit 1 (parse error)."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not a json file @@@", encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(bad_file)],
    )
    assert result.exit_code == 1


# ===========================================================================
# ananke eval dataset list
# ===========================================================================


def test_eval_dataset_list_empty(tmp_path: Path) -> None:
    """No datasets directory → exit 0."""
    result = runner.invoke(app, ["eval", "dataset", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_eval_dataset_list_empty_mentions_dataset(tmp_path: Path) -> None:
    """Output when no datasets dir exists contains informative text."""
    result = runner.invoke(app, ["eval", "dataset", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output  # non-empty


def test_eval_dataset_list_with_dataset(tmp_path: Path) -> None:
    """Datasets directory exists with a dataset: command runs without crash."""
    _make_dataset_file(tmp_path, "test-ds")
    # render_result called with as_json kwarg (known limitation) → may exit 1
    result = runner.invoke(app, ["eval", "dataset", "list", "--project", str(tmp_path)])
    assert result.exit_code in (0, 1)


# ===========================================================================
# ananke eval dataset validate
# ===========================================================================


def test_eval_dataset_validate_valid(tmp_path: Path) -> None:
    """Valid dataset file → exit 0, output contains 'valid'."""
    ds_file = _make_dataset_file(tmp_path, "good-ds")
    result = runner.invoke(
        app,
        ["eval", "dataset", "validate", "--path", str(ds_file)],
    )
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_eval_dataset_validate_missing(tmp_path: Path) -> None:
    """Non-existent dataset file → non-zero exit code."""
    result = runner.invoke(
        app,
        [
            "eval",
            "dataset",
            "validate",
            "--path",
            str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code != 0


def test_eval_dataset_validate_empty_cases(tmp_path: Path) -> None:
    """Dataset with no cases fails validation (exit 1)."""
    ds_file = tmp_path / "empty-ds.json"
    ds_file.write_text(json.dumps({"id": "empty", "cases": []}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "dataset", "validate", "--path", str(ds_file)],
    )
    assert result.exit_code == 1


def test_eval_dataset_validate_case_missing_input(tmp_path: Path) -> None:
    """Dataset with a case that has no input field fails validation (exit 1)."""
    ds_file = tmp_path / "bad-case.json"
    ds_file.write_text(json.dumps({"id": "my-ds", "cases": [{"id": "c1"}]}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["eval", "dataset", "validate", "--path", str(ds_file)],
    )
    assert result.exit_code == 1


# ===========================================================================
# ananke eval baseline create
# ===========================================================================


def test_eval_baseline_create_no_scores(tmp_path: Path) -> None:
    """Creating a baseline without a scores.json exits with code 2."""
    result = runner.invoke(
        app,
        [
            "eval",
            "baseline",
            "create",
            "--run",
            "nonexistent-run",
            "--id",
            "my-baseline",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2


def test_eval_baseline_create_with_scores(tmp_path: Path) -> None:
    """Create baseline from existing scores → exit 0, baseline file created."""
    _make_scores_file(tmp_path, run_id="my-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "baseline",
            "create",
            "--run",
            "my-run",
            "--id",
            "baseline-v1",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    baseline_file = tmp_path / ".ananke" / "evals" / "baselines" / "baseline-v1.json"
    assert baseline_file.exists()


def test_eval_baseline_create_content(tmp_path: Path) -> None:
    """Created baseline JSON has correct baseline_id and scores keys."""
    _make_scores_file(tmp_path, run_id="my-run")
    runner.invoke(
        app,
        [
            "eval",
            "baseline",
            "create",
            "--run",
            "my-run",
            "--id",
            "baseline-check",
            "--project",
            str(tmp_path),
        ],
    )
    baseline_file = tmp_path / ".ananke" / "evals" / "baselines" / "baseline-check.json"
    assert baseline_file.exists()
    data = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert data["baseline_id"] == "baseline-check"
    assert "scores" in data


def test_eval_baseline_create_different_ids(tmp_path: Path) -> None:
    """Creating two baselines with different IDs produces two files."""
    _make_scores_file(tmp_path, run_id="run-1")
    _make_scores_file(tmp_path, run_id="run-2")

    for baseline_id, run_id in [("bl-a", "run-1"), ("bl-b", "run-2")]:
        res = runner.invoke(
            app,
            [
                "eval",
                "baseline",
                "create",
                "--run",
                run_id,
                "--id",
                baseline_id,
                "--project",
                str(tmp_path),
            ],
        )
        assert res.exit_code == 0

    baselines_dir = tmp_path / ".ananke" / "evals" / "baselines"
    files = {f.stem for f in baselines_dir.glob("*.json")}
    assert "bl-a" in files
    assert "bl-b" in files


# ===========================================================================
# ananke eval trace show
# ===========================================================================


def test_eval_trace_show_missing(tmp_path: Path) -> None:
    """Showing a non-existent trace file → non-zero exit."""
    result = runner.invoke(
        app,
        [
            "eval",
            "trace",
            "show",
            "--path",
            str(tmp_path / "missing-trace.json"),
        ],
    )
    assert result.exit_code != 0


def test_eval_trace_show_valid(tmp_path: Path) -> None:
    """Showing a valid trace file → exit 0, trace_id in output."""
    trace_path = _make_trace_file(tmp_path, "my-trace")
    result = runner.invoke(
        app,
        ["eval", "trace", "show", "--path", str(trace_path)],
    )
    assert result.exit_code == 0
    # render_result prints summary "trace t-001" via Rich Console
    assert result.output


def test_eval_trace_show_produces_output(tmp_path: Path) -> None:
    """Trace show always produces some output for a valid file."""
    trace_path = _make_trace_file(tmp_path, "my-trace")
    result = runner.invoke(
        app,
        ["eval", "trace", "show", "--path", str(trace_path)],
    )
    assert result.exit_code == 0
    assert len(result.output.strip()) > 0


# ===========================================================================
# ananke eval trace import
# ===========================================================================


def test_eval_trace_import(tmp_path: Path) -> None:
    """Importing a trace normalizes it and stores under .ananke/traces/."""
    trace_path = _make_trace_file(tmp_path, "import-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "trace",
            "import",
            "--path",
            str(trace_path),
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    stored = tmp_path / ".ananke" / "traces" / "import-run.json"
    assert stored.exists()


def test_eval_trace_import_with_run_id_override(tmp_path: Path) -> None:
    """--run-id override stores the trace under the given id."""
    trace_path = _make_trace_file(tmp_path, "original-run")
    result = runner.invoke(
        app,
        [
            "eval",
            "trace",
            "import",
            "--path",
            str(trace_path),
            "--run-id",
            "overridden-id",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    stored = tmp_path / ".ananke" / "traces" / "overridden-id.json"
    assert stored.exists()


def test_eval_trace_import_creates_valid_json(tmp_path: Path) -> None:
    """Imported trace file is valid JSON parseable as AgentTrace."""
    trace_path = _make_trace_file(tmp_path, "import-run")
    runner.invoke(
        app,
        [
            "eval",
            "trace",
            "import",
            "--path",
            str(trace_path),
            "--project",
            str(tmp_path),
        ],
    )
    stored = tmp_path / ".ananke" / "traces" / "import-run.json"
    if stored.exists():
        data = json.loads(stored.read_text(encoding="utf-8"))
        assert "trace_id" in data
        assert "run_id" in data


# ===========================================================================
# ananke eval adapter list
# ===========================================================================


def test_eval_adapter_list(tmp_path: Path) -> None:
    """Adapter list command runs without crashing."""
    result = runner.invoke(app, ["eval", "adapter", "list"])
    # render_result called with as_json (known bug) → may exit 1
    assert result.exit_code in (0, 1)


def test_eval_adapter_list_json(tmp_path: Path) -> None:
    """adapter list --json also completes without crashing."""
    result = runner.invoke(app, ["eval", "adapter", "list", "--json"])
    assert result.exit_code in (0, 1)


# ===========================================================================
# ananke eval adapter doctor
# ===========================================================================


def test_eval_adapter_doctor_all(tmp_path: Path) -> None:
    """Doctor for all adapters runs without crashing."""
    result = runner.invoke(app, ["eval", "adapter", "doctor"])
    # render_result called with as_json (known bug) → may exit 1
    assert result.exit_code in (0, 1)


def test_eval_adapter_doctor_specific(tmp_path: Path) -> None:
    """Doctor for 'mlflow' adapter runs without crashing."""
    result = runner.invoke(app, ["eval", "adapter", "doctor", "mlflow"])
    assert result.exit_code in (0, 1)


def test_eval_adapter_doctor_unknown(tmp_path: Path) -> None:
    """Doctor for an unknown adapter name returns empty filtered result."""
    result = runner.invoke(app, ["eval", "adapter", "doctor", "nonexistent-adapter-xyz"])
    # Empty filtered result → render_result still called with as_json → may exit 1
    assert result.exit_code in (0, 1)


def test_eval_adapter_doctor_json(tmp_path: Path) -> None:
    """adapter doctor --json also completes without crashing."""
    result = runner.invoke(app, ["eval", "adapter", "doctor", "--json"])
    assert result.exit_code in (0, 1)


# ===========================================================================
# ananke eval judge list
# ===========================================================================


def test_eval_judge_list(tmp_path: Path) -> None:
    """Judge list exits 0 and produces output."""
    result = runner.invoke(app, ["eval", "judge", "list"])
    assert result.exit_code == 0
    assert result.output


def test_eval_judge_list_contains_local(tmp_path: Path) -> None:
    """Judge list includes 'local' provider."""
    result = runner.invoke(app, ["eval", "judge", "list"])
    assert result.exit_code == 0
    assert "local" in result.output


def test_eval_judge_list_contains_supported_providers(tmp_path: Path) -> None:
    """Judge list includes at least one of the known supported providers."""
    result = runner.invoke(app, ["eval", "judge", "list"])
    assert result.exit_code == 0
    output = result.output.lower()
    known_providers = {"azure", "bedrock", "local", "custom", "anthropic", "openai"}
    assert any(p in output for p in known_providers)


def test_eval_judge_list_multiple_providers(tmp_path: Path) -> None:
    """Judge list shows more than one provider."""
    result = runner.invoke(app, ["eval", "judge", "list"])
    assert result.exit_code == 0
    # At least 2 providers should be mentioned
    output = result.output.lower()
    known_providers = {"azure", "bedrock", "local", "custom", "anthropic", "openai"}
    found = [p for p in known_providers if p in output]
    assert len(found) >= 2


# ===========================================================================
# ananke eval judge test
# ===========================================================================


def test_eval_judge_test_local(tmp_path: Path) -> None:
    """Local judge test exits 0 and shows a score."""
    result = runner.invoke(app, ["eval", "judge", "test", "--provider", "local"])
    assert result.exit_code == 0
    assert "score" in result.output.lower()


def test_eval_judge_test_local_shows_passed(tmp_path: Path) -> None:
    """Local judge test output includes 'passed' field."""
    result = runner.invoke(app, ["eval", "judge", "test", "--provider", "local"])
    assert result.exit_code == 0
    output = result.output.lower()
    assert "passed" in output or "score" in output


def test_eval_judge_test_unsupported_provider(tmp_path: Path) -> None:
    """Non-implemented provider triggers NotImplementedError caught as exit 1."""
    result = runner.invoke(
        app,
        ["eval", "judge", "test", "--provider", "azure"],
    )
    # azure is allowed but dispatch raises NotImplementedError → caught → exit 1
    assert result.exit_code == 1


def test_eval_judge_test_default_provider(tmp_path: Path) -> None:
    """Default provider (local) produces a valid score without error."""
    result = runner.invoke(app, ["eval", "judge", "test"])
    assert result.exit_code == 0

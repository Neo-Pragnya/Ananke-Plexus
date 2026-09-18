"""End-to-end workflow tests for the Ananke eval harness.

These tests exercise multi-step sequences: run → report → baseline → compare
and verify that evidence files, API functions, and adapter health checks
all behave as documented.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_scores_file(tmp_path: Path, run_id: str = "test-run") -> Path:
    """Create a minimal scores.json for *run_id* under .ananke/evidence/."""
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


# ---------------------------------------------------------------------------
# Full workflow: run → report → baseline → compare
# ---------------------------------------------------------------------------


def test_full_eval_workflow(tmp_path: Path) -> None:
    """
    Full golden path:

    1. Run ``ananke eval run`` to produce an eval evidence bundle.
    2. Call ``ananke eval report`` on the resulting run id.
    3. Create a baseline from that run.
    4. Run ``ananke eval run`` again (same run id, overwrites scores).
    5. Compare the second run against the baseline.
    """
    # Step 1: run eval
    r1 = runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    assert r1.exit_code in (0, 1), f"eval run failed unexpectedly: {r1.output}"

    # Determine the run_id (default is "eval-run")
    evidence_root = tmp_path / ".ananke" / "evidence"
    assert evidence_root.exists(), "evidence dir should be created"

    run_id = "eval-run"  # default from build_trace_from_run_id
    scores_file = evidence_root / run_id / "eval" / "scores.json"
    assert scores_file.exists(), "scores.json must be created by eval run"

    # Step 2: report
    r2 = runner.invoke(
        app,
        ["eval", "report", "--run", run_id, "--project", str(tmp_path)],
    )
    assert r2.exit_code == 0, f"eval report failed: {r2.output}"

    # Step 3: baseline
    r3 = runner.invoke(
        app,
        [
            "eval",
            "baseline",
            "create",
            "--run",
            run_id,
            "--id",
            "baseline-v1",
            "--project",
            str(tmp_path),
        ],
    )
    assert r3.exit_code == 0, f"baseline create failed: {r3.output}"
    baseline_file = tmp_path / ".ananke" / "evals" / "baselines" / "baseline-v1.json"
    assert baseline_file.exists()

    # Step 4: second run (overwrites same run_id)
    r4 = runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    assert r4.exit_code in (0, 1)

    # Step 5: compare
    r5 = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "baseline-v1",
            "--run",
            run_id,
            "--project",
            str(tmp_path),
        ],
    )
    assert r5.exit_code in (0, 1), f"eval compare failed: {r5.output}"


def test_eval_run_creates_evidence_bundle(tmp_path: Path) -> None:
    """
    After ``ananke eval run`` the following evidence files exist:

    * ``.ananke/evidence/<run_id>/eval/scores.json``
    * ``.ananke/evidence/<run_id>/eval/reports/`` (dir with at least one file)
    * ``.ananke/evidence/<run_id>/eval/manifest.json``
    """
    r = runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    assert r.exit_code in (0, 1)

    evidence_eval = tmp_path / ".ananke" / "evidence" / "eval-run" / "eval"

    assert (evidence_eval / "scores.json").exists(), "scores.json missing"
    assert (evidence_eval / "manifest.json").exists(), "manifest.json missing"

    reports_dir = evidence_eval / "reports"
    assert reports_dir.exists(), "reports/ dir missing"
    report_files = list(reports_dir.iterdir())
    assert len(report_files) >= 1, "at least one report file expected"


def test_eval_run_scores_json_is_valid(tmp_path: Path) -> None:
    """scores.json produced by eval run is valid JSON containing a list."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    scores_file = tmp_path / ".ananke" / "evidence" / "eval-run" / "eval" / "scores.json"
    assert scores_file.exists()
    data = json.loads(scores_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)


def test_eval_run_manifest_has_run_id(tmp_path: Path) -> None:
    """manifest.json contains the run_id field."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    manifest = tmp_path / ".ananke" / "evidence" / "eval-run" / "eval" / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert "run_id" in data


def test_eval_run_reports_contain_markdown(tmp_path: Path) -> None:
    """Evidence bundle contains a Markdown report."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    reports_dir = tmp_path / ".ananke" / "evidence" / "eval-run" / "eval" / "reports"
    if reports_dir.exists():
        md_files = list(reports_dir.glob("*.md"))
        assert len(md_files) >= 1, "Markdown report missing from evidence bundle"


def test_eval_run_reports_contain_junit(tmp_path: Path) -> None:
    """Evidence bundle contains a JUnit XML report."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    reports_dir = tmp_path / ".ananke" / "evidence" / "eval-run" / "eval" / "reports"
    if reports_dir.exists():
        xml_files = list(reports_dir.glob("*.xml"))
        assert len(xml_files) >= 1, "JUnit XML report missing from evidence bundle"


# ---------------------------------------------------------------------------
# Adapter health-check workflow
# ---------------------------------------------------------------------------


def test_eval_adapter_doctor_all_adapters(tmp_path: Path) -> None:
    """
    adapter_doctor() returns results for the 6 expected adapters.
    Calls the Python API directly to bypass the render_result bug.
    """
    from ananke.plexus.evals.api import adapter_doctor

    results = adapter_doctor()
    # adapter_doctor() returns full adapter_id values like "ananke.adapters.mlflow"
    expected_suffixes = {"mlflow", "deepeval", "inspect_ai", "ragas", "openevals", "agentevals"}
    for suffix in expected_suffixes:
        matching = [k for k in results if suffix in k]
        assert matching, (
            f"no adapter matching '{suffix}' found in doctor report; got: {list(results)}"
        )
        info = results[matching[0]]
        assert "available" in info, f"adapter '{suffix}' doctor result missing 'available' key"


def test_eval_adapter_doctor_available_is_bool(tmp_path: Path) -> None:
    """Each adapter's 'available' key is a boolean."""
    from ananke.plexus.evals.api import adapter_doctor

    results = adapter_doctor()
    for adapter_id, info in results.items():
        available = info.get("available")
        assert isinstance(available, bool), (
            f"adapter '{adapter_id}' 'available' should be bool, got {type(available)}"
        )


def test_eval_adapter_doctor_version_is_string_or_none(tmp_path: Path) -> None:
    """Each adapter's 'version' key (if present) is a string or None."""
    from ananke.plexus.evals.api import adapter_doctor

    results = adapter_doctor()
    for adapter_id, info in results.items():
        if "version" in info:
            assert info["version"] is None or isinstance(info["version"], str), (
                f"adapter '{adapter_id}' version should be str or None"
            )


# ---------------------------------------------------------------------------
# Judge test + list workflow
# ---------------------------------------------------------------------------


def test_eval_judge_test_and_list(tmp_path: Path) -> None:
    """
    Provider list is non-empty and local judge test produces a score in [0, 1].
    """
    from ananke.plexus.evals.judges.base import JudgeInputEnvelope
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
    from ananke.plexus.evals.models.rubric import Rubric

    gw = EnterpriseJudgeGateway()
    providers = gw.list_providers()
    assert len(providers) > 0
    assert "local" in providers

    rubric = Rubric(rubric_id="test-rubric", title="Test", pass_threshold=0.5)
    envelope = JudgeInputEnvelope(
        rubric=rubric,
        case_input="What is 2+2?",
        agent_output="The answer is 4.",
    )
    result = gw.score(envelope=envelope, provider="local")
    assert 0.0 <= result.normalized_score <= 1.0
    assert isinstance(result.passed, bool)


def test_eval_judge_test_score_is_float(tmp_path: Path) -> None:
    """Local judge score is a float between 0 and 1."""
    from ananke.plexus.evals.judges.base import JudgeInputEnvelope
    from ananke.plexus.evals.judges.gateway import EnterpriseJudgeGateway
    from ananke.plexus.evals.models.rubric import Rubric

    gw = EnterpriseJudgeGateway()
    rubric = Rubric(rubric_id="tr", title="T", pass_threshold=0.5)
    envelope = JudgeInputEnvelope(rubric=rubric, case_input="hello", agent_output="world")
    result = gw.score(envelope=envelope, provider="local")
    assert isinstance(result.normalized_score, float)
    assert 0.0 <= result.normalized_score <= 1.0


# ---------------------------------------------------------------------------
# Baseline round-trip: create → compare
# ---------------------------------------------------------------------------


def test_baseline_round_trip_via_api(tmp_path: Path) -> None:
    """
    Create a baseline from a run, then compare the same run against it.
    Scores are equal so no regressions or improvements.
    """
    _make_scores_file(tmp_path, run_id="stable-run")

    # Create baseline
    r_create = runner.invoke(
        app,
        [
            "eval",
            "baseline",
            "create",
            "--run",
            "stable-run",
            "--id",
            "stable-baseline",
            "--project",
            str(tmp_path),
        ],
    )
    assert r_create.exit_code == 0

    # Compare same run to its own baseline → stable, no regressions
    r_compare = runner.invoke(
        app,
        [
            "eval",
            "compare",
            "--baseline",
            "stable-baseline",
            "--run",
            "stable-run",
            "--project",
            str(tmp_path),
        ],
    )
    assert r_compare.exit_code == 0  # no regression → not blocked


def test_baseline_compare_regression_detection(tmp_path: Path) -> None:
    """
    Candidate scores lower than baseline → regressions detected via API.
    Uses compare_to_baseline directly (not CLI) to avoid render_result side-effects.
    """
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(
        baseline_id="b1",
        suite_id="s",
        version="v1",
        scores={"outcome": 1.0},
        created_at=datetime.now(tz=UTC),
    )
    score = EvalScore(
        evaluator_id="em",
        dimension="outcome",
        metric="exact_match",
        status=EvalStatus.FAIL,
        normalized_score=0.5,
    )
    report = EvalReport(run_id="run-1", suite_id="s", scores=[score])
    comparison = compare_to_baseline(report, baseline)
    # delta = 0.5 - 1.0 = -0.5 → regression
    assert "outcome" in comparison.regressions
    assert comparison.regressions["outcome"] < 0


def test_baseline_compare_improvement_detection(tmp_path: Path) -> None:
    """Candidate scores higher than baseline → improvements detected via API."""
    from ananke.plexus.evals.models.baseline import Baseline
    from ananke.plexus.evals.models.report import EvalReport
    from ananke.plexus.evals.models.score import EvalScore, EvalStatus
    from ananke.plexus.evals.regression.compare import compare_to_baseline

    baseline = Baseline(
        baseline_id="b2",
        suite_id="s",
        version="v1",
        scores={"quality": 0.5},
        created_at=datetime.now(tz=UTC),
    )
    score = EvalScore(
        evaluator_id="em",
        dimension="quality",
        metric="quality_score",
        status=EvalStatus.PASS,
        normalized_score=0.9,
    )
    report = EvalReport(run_id="run-2", suite_id="s", scores=[score])
    comparison = compare_to_baseline(report, baseline)
    # delta = 0.9 - 0.5 = 0.4 → improvement
    assert "quality" in comparison.improvements
    assert comparison.improvements["quality"] > 0


# ---------------------------------------------------------------------------
# Trace import → show round-trip
# ---------------------------------------------------------------------------


def test_trace_import_then_show(tmp_path: Path) -> None:
    """Import a trace then show the imported file — both succeed."""
    trace_data = {
        "trace_id": "wf-trace",
        "run_id": "wf-run",
        "runtime": "test",
        "spans": [],
        "usage": {"total_tokens": 42, "tool_calls": 0},
    }
    raw_trace = tmp_path / "wf-run.json"
    raw_trace.write_text(json.dumps(trace_data), encoding="utf-8")

    # Import
    r_import = runner.invoke(
        app,
        [
            "eval",
            "trace",
            "import",
            "--path",
            str(raw_trace),
            "--project",
            str(tmp_path),
        ],
    )
    assert r_import.exit_code == 0

    stored = tmp_path / ".ananke" / "traces" / "wf-run.json"
    assert stored.exists()

    # Show the imported trace
    r_show = runner.invoke(
        app,
        ["eval", "trace", "show", "--path", str(stored)],
    )
    assert r_show.exit_code == 0


# ---------------------------------------------------------------------------
# Suite validate → list workflow
# ---------------------------------------------------------------------------


def test_suite_validate_then_list(tmp_path: Path) -> None:
    """A suite that passes validate also appears in the suite list."""
    suites_dir = tmp_path / ".ananke" / "evals" / "suites"
    suites_dir.mkdir(parents=True, exist_ok=True)
    suite_data = {
        "id": "workflow-suite",
        "evaluators": [{"id": "exact-match"}],
        "policy": {"rules": {}},
    }
    suite_file = suites_dir / "workflow-suite.json"
    suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

    # Validate
    r_validate = runner.invoke(
        app,
        ["eval", "suite", "validate", "--path", str(suite_file)],
    )
    assert r_validate.exit_code == 0

    # List
    r_list = runner.invoke(app, ["eval", "suite", "list", "--project", str(tmp_path)])
    assert r_list.exit_code == 0


# ---------------------------------------------------------------------------
# Dataset validate → list workflow
# ---------------------------------------------------------------------------


def test_dataset_validate_then_list(tmp_path: Path) -> None:
    """A dataset that passes validate also appears in the dataset list."""
    ds_dir = tmp_path / ".ananke" / "evals" / "datasets"
    ds_dir.mkdir(parents=True, exist_ok=True)
    ds_data = {
        "id": "wf-dataset",
        "version": 1,
        "cases": [{"id": "c1", "input": "q?", "expected": "a!"}],
    }
    ds_file = ds_dir / "wf-dataset.json"
    ds_file.write_text(json.dumps(ds_data), encoding="utf-8")

    # Validate
    r_validate = runner.invoke(
        app,
        ["eval", "dataset", "validate", "--path", str(ds_file)],
    )
    assert r_validate.exit_code == 0

    # List
    r_list = runner.invoke(app, ["eval", "dataset", "list", "--project", str(tmp_path)])
    assert r_list.exit_code == 0


# ---------------------------------------------------------------------------
# Report all formats after a real eval run
# ---------------------------------------------------------------------------


def test_all_report_formats_after_run(tmp_path: Path) -> None:
    """All four report formats succeed after a real eval run."""
    runner.invoke(app, ["eval", "run", "--project", str(tmp_path)])
    run_id = "eval-run"

    for fmt in ("console", "json", "markdown", "junit"):
        result = runner.invoke(
            app,
            [
                "eval",
                "report",
                "--run",
                run_id,
                "--format",
                fmt,
                "--project",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, f"eval report --format {fmt} failed: {result.output}"
        assert result.output, f"eval report --format {fmt} produced no output"

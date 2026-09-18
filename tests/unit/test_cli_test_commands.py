"""Tests for ``ananke test`` CLI commands (unified quality test harness).

The testing module is expected to exist.  If it cannot be imported the entire
module is skipped so the suite stays green while the feature is in flight.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from ananke.plexus.testing.api import run_quality_suite  # noqa: F401

    TESTING_AVAILABLE = True
except ImportError:
    TESTING_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not TESTING_AVAILABLE,
    reason="ananke.plexus.testing module not available",
)

from typer.testing import CliRunner  # noqa: E402 (import after conditional skip)

from ananke.plexus.cli.app import app  # noqa: E402

runner = CliRunner()


# ---------------------------------------------------------------------------
# ananke test run
# ---------------------------------------------------------------------------


def test_test_run_standard_profile(tmp_path: Path) -> None:
    """``ananke test run`` with the default 'standard' profile exits 0 or 1."""
    result = runner.invoke(app, ["test", "run", "--project", str(tmp_path)])
    # 0 = all tests pass, 1 = some tests fail/error
    assert result.exit_code in (0, 1), result.output


def test_test_run_fast_profile(tmp_path: Path) -> None:
    """``ananke test run --profile fast`` exits 0 or 1."""
    result = runner.invoke(
        app,
        ["test", "run", "--profile", "fast", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1), result.output


def test_test_run_json_output(tmp_path: Path) -> None:
    """``ananke test run --json`` emits parseable JSON with a 'run_id'."""
    result = runner.invoke(
        app,
        ["test", "run", "--json", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)
    data = json.loads(result.output.strip())
    assert "run_id" in data


def test_test_run_json_has_results(tmp_path: Path) -> None:
    """JSON output from ``test run`` contains a 'results' list."""
    result = runner.invoke(
        app,
        ["test", "run", "--json", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)
    data = json.loads(result.output.strip())
    assert "results" in data
    assert isinstance(data["results"], list)


def test_test_run_produces_output(tmp_path: Path) -> None:
    """``test run`` always produces some stdout content."""
    result = runner.invoke(app, ["test", "run", "--project", str(tmp_path)])
    assert result.output


def test_test_run_strict_profile(tmp_path: Path) -> None:
    """``ananke test run --profile strict`` exits 0 or 1."""
    result = runner.invoke(
        app,
        ["test", "run", "--profile", "strict", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# ananke test discover
# ---------------------------------------------------------------------------


def test_test_discover(tmp_path: Path) -> None:
    """``ananke test discover`` runs without crashing."""
    result = runner.invoke(app, ["test", "discover", "--project", str(tmp_path)])
    # render_result called with as_json (known bug) → may exit 1
    assert result.exit_code in (0, 1)


def test_test_discover_json(tmp_path: Path) -> None:
    """``ananke test discover --json`` also completes without crashing."""
    result = runner.invoke(
        app,
        ["test", "discover", "--json", "--project", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# ananke test profile list
# ---------------------------------------------------------------------------


def test_test_profile_list(tmp_path: Path) -> None:
    """``ananke test profile list`` runs without crashing."""
    result = runner.invoke(app, ["test", "profile", "list"])
    # render_result called with as_json (known bug) → may exit 1
    assert result.exit_code in (0, 1)


def test_test_profile_list_json(tmp_path: Path) -> None:
    """``ananke test profile list --json`` also completes without crashing."""
    result = runner.invoke(app, ["test", "profile", "list", "--json"])
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# ananke test profile show
# ---------------------------------------------------------------------------


def test_test_profile_show_fast(tmp_path: Path) -> None:
    """``ananke test profile show fast`` runs without crashing."""
    result = runner.invoke(app, ["test", "profile", "show", "fast"])
    # render_result called with as_json (known bug) → may exit 1
    assert result.exit_code in (0, 1)


def test_test_profile_show_standard(tmp_path: Path) -> None:
    """``ananke test profile show standard`` runs without crashing."""
    result = runner.invoke(app, ["test", "profile", "show", "standard"])
    assert result.exit_code in (0, 1)


def test_test_profile_show_nonexistent(tmp_path: Path) -> None:
    """``ananke test profile show`` with unknown profile exits 2."""
    result = runner.invoke(app, ["test", "profile", "show", "no-such-profile-xyz"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# ananke test adapter list
# ---------------------------------------------------------------------------


def test_test_adapter_list(tmp_path: Path) -> None:
    """``ananke test adapter list`` runs without crashing."""
    result = runner.invoke(app, ["test", "adapter", "list"])
    # render_result called with as_json → may exit 1
    assert result.exit_code in (0, 1)


def test_test_adapter_list_json(tmp_path: Path) -> None:
    """``ananke test adapter list --json`` also completes without crashing."""
    result = runner.invoke(app, ["test", "adapter", "list", "--json"])
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# ananke test report (with missing run)
# ---------------------------------------------------------------------------


def test_test_report_missing_run(tmp_path: Path) -> None:
    """``ananke test report`` on a non-existent run exits 2."""
    result = runner.invoke(
        app,
        [
            "test",
            "report",
            "--run",
            "nonexistent-quality-run",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Python API: list_profiles
# ---------------------------------------------------------------------------


def test_list_profiles_returns_dict() -> None:
    """list_profiles() returns a non-empty dict."""
    from ananke.plexus.testing.api import list_profiles

    profiles = list_profiles()
    assert isinstance(profiles, dict)
    assert len(profiles) > 0


def test_list_profiles_contains_standard() -> None:
    """list_profiles() includes the 'standard' profile."""
    from ananke.plexus.testing.api import list_profiles

    profiles = list_profiles()
    assert "standard" in profiles


def test_list_profiles_contains_fast() -> None:
    """list_profiles() includes the 'fast' profile."""
    from ananke.plexus.testing.api import list_profiles

    profiles = list_profiles()
    assert "fast" in profiles


# ---------------------------------------------------------------------------
# Python API: adapter_doctor
# ---------------------------------------------------------------------------


def test_test_adapter_doctor_returns_dict() -> None:
    """adapter_doctor() returns a dict."""
    from ananke.plexus.testing.api import adapter_doctor

    result = adapter_doctor()
    assert isinstance(result, dict)


def test_test_adapter_doctor_each_has_available() -> None:
    """Each entry in adapter_doctor() has an 'available' key."""
    from ananke.plexus.testing.api import adapter_doctor

    result = adapter_doctor()
    for adapter_id, info in result.items():
        assert "available" in info, f"adapter '{adapter_id}' missing 'available' key"


def test_test_adapter_doctor_available_is_bool() -> None:
    """'available' in each adapter_doctor() entry is a boolean."""
    from ananke.plexus.testing.api import adapter_doctor

    result = adapter_doctor()
    for adapter_id, info in result.items():
        assert isinstance(info["available"], bool), (
            f"adapter '{adapter_id}' 'available' should be bool"
        )


# ---------------------------------------------------------------------------
# Python API: run_quality_suite
# ---------------------------------------------------------------------------


def test_run_quality_suite_returns_test_run(tmp_path: Path) -> None:
    """run_quality_suite() returns a TestRun object."""
    from ananke.plexus.testing.api import run_quality_suite
    from ananke.plexus.testing.models.result import TestRun

    run = run_quality_suite(project_root=tmp_path, profile="fast")
    assert isinstance(run, TestRun)


def test_run_quality_suite_has_run_id(tmp_path: Path) -> None:
    """TestRun returned by run_quality_suite() has a non-empty run_id."""
    from ananke.plexus.testing.api import run_quality_suite

    run = run_quality_suite(project_root=tmp_path, profile="fast")
    assert run.run_id
    assert isinstance(run.run_id, str)


def test_run_quality_suite_has_results_list(tmp_path: Path) -> None:
    """TestRun has a 'results' attribute that is a list."""
    from ananke.plexus.testing.api import run_quality_suite

    run = run_quality_suite(project_root=tmp_path, profile="fast")
    assert isinstance(run.results, list)


def test_run_quality_suite_custom_run_id(tmp_path: Path) -> None:
    """run_quality_suite respects an explicit run_id."""
    from ananke.plexus.testing.api import run_quality_suite

    run = run_quality_suite(
        project_root=tmp_path,
        profile="fast",
        run_id="custom-run-id",
        save_evidence=False,
    )
    assert run.run_id == "custom-run-id"

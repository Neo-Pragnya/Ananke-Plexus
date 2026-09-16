"""Tests for new CLI commands: bmad, hooks, backend, policy packs."""

from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app

runner = CliRunner()


def _init(tmp: Path) -> None:
    result = runner.invoke(app, ["init", "--project", str(tmp)])
    assert result.exit_code == 0


# ---------- policy commands ----------


def test_policy_list(tmp_path):
    _init(tmp_path)
    result = runner.invoke(app, ["policy", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_policy_list_json(tmp_path):
    _init(tmp_path)
    result = runner.invoke(app, ["policy", "list", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_policy_install_pack_baseline(tmp_path):
    _init(tmp_path)
    result = runner.invoke(app, ["policy", "install-pack", "baseline", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_policy_install_pack_python_library(tmp_path):
    _init(tmp_path)
    result = runner.invoke(
        app, ["policy", "install-pack", "python-library", "--project", str(tmp_path)]
    )
    assert result.exit_code == 0


def test_policy_install_pack_unknown(tmp_path):
    _init(tmp_path)
    result = runner.invoke(
        app, ["policy", "install-pack", "does-not-exist", "--project", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_policy_check(tmp_path):
    _init(tmp_path)
    result = runner.invoke(
        app, ["policy", "check", "--stage", "verify", "--project", str(tmp_path)]
    )
    # May exit 0 or 3 depending on policy; should not crash
    assert result.exit_code in (0, 3)


# ---------- hooks commands ----------


def _fake_git_hooks(tmp: Path) -> None:
    (tmp / ".git" / "hooks").mkdir(parents=True, exist_ok=True)


def test_hooks_status(tmp_path):
    _fake_git_hooks(tmp_path)
    result = runner.invoke(app, ["hooks", "status", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_hooks_status_json(tmp_path):
    _fake_git_hooks(tmp_path)
    result = runner.invoke(app, ["hooks", "status", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_hooks_install(tmp_path):
    _fake_git_hooks(tmp_path)
    result = runner.invoke(app, ["hooks", "install", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_hooks_install_framework_mode(tmp_path):
    result = runner.invoke(
        app,
        ["hooks", "install", "--mode", "pre-commit-framework", "--project", str(tmp_path)],
    )
    assert result.exit_code == 0


def test_hooks_uninstall(tmp_path):
    _fake_git_hooks(tmp_path)
    runner.invoke(app, ["hooks", "install", "--project", str(tmp_path)])
    result = runner.invoke(app, ["hooks", "uninstall", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_hooks_run_post_commit(tmp_path):
    result = runner.invoke(app, ["hooks", "run", "post-commit", "--project", str(tmp_path)])
    assert result.exit_code == 0


# ---------- backend commands ----------


def test_backend_list(tmp_path):
    result = runner.invoke(app, ["backend", "list", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_backend_list_json(tmp_path):
    result = runner.invoke(app, ["backend", "list", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_backend_doctor_fake(tmp_path):
    result = runner.invoke(app, ["backend", "doctor", "fake", "--project", str(tmp_path)])
    assert result.exit_code == 0


def test_backend_test_fake(tmp_path):
    result = runner.invoke(app, ["backend", "test", "fake", "--project", str(tmp_path)])
    assert result.exit_code == 0


# ---------- bmad commands ----------


def _make_feature_dir(tmp: Path, req_id: str = "DEMO-101") -> Path:
    feature_dir = tmp / ".ananke" / "specs" / req_id
    feature_dir.mkdir(parents=True)
    (feature_dir / "requirement.md").write_text(
        "# Test Feature\n\n## Acceptance Criteria\n- Criterion one\n- Criterion two\n",
        encoding="utf-8",
    )
    return feature_dir


def test_bmad_compile(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    result = runner.invoke(
        app,
        ["bmad", "compile", "--feature-dir", str(feature_dir), "--project", str(tmp_path)],
    )
    assert result.exit_code == 0


def test_bmad_show(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    runner.invoke(
        app, ["bmad", "compile", "--feature-dir", str(feature_dir), "--project", str(tmp_path)]
    )
    result = runner.invoke(app, ["bmad", "show", "--feature-dir", str(feature_dir)])
    assert result.exit_code == 0
    assert "behavior:" in result.output


def test_bmad_trace(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    result = runner.invoke(app, ["bmad", "trace", "--feature-dir", str(feature_dir)])
    assert result.exit_code == 0


def test_bmad_trace_json(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    result = runner.invoke(app, ["bmad", "trace", "--feature-dir", str(feature_dir), "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert "requirement_id" in data


def test_bmad_verify_missing(tmp_path):
    feature_dir = tmp_path / "empty-feature"
    feature_dir.mkdir()
    (feature_dir / "requirement.md").write_text("# Empty\n", encoding="utf-8")
    result = runner.invoke(app, ["bmad", "verify", "--feature-dir", str(feature_dir)])
    assert result.exit_code == 4


def test_bmad_verify_complete(tmp_path):
    feature_dir = _make_feature_dir(tmp_path)
    runner.invoke(
        app, ["bmad", "compile", "--feature-dir", str(feature_dir), "--project", str(tmp_path)]
    )
    result = runner.invoke(app, ["bmad", "verify", "--feature-dir", str(feature_dir)])
    assert result.exit_code == 0


# ---------- run approvals ----------


def test_run_approvals(tmp_path):
    _init(tmp_path)
    result_start = runner.invoke(
        app, ["run", "start", "--spec-id", "SPEC-1", "--project", str(tmp_path)]
    )
    assert result_start.exit_code == 0
    import json

    details = (
        json.loads(result_start.output.strip().split("\n")[-1])
        if "{" in result_start.output
        else {}
    )
    run_id = details.get("run_id") or ""
    if run_id:
        result = runner.invoke(
            app, ["run", "approvals", "--run-id", run_id, "--project", str(tmp_path)]
        )
        assert result.exit_code == 0

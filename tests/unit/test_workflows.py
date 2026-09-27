"""Workflows: named, replayable sequences of ananke/apm commands."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.workflows import Workflow, WorkflowStep, load_workflow, save_workflow
from ananke.plexus.workflows.presets import BUILTIN_PRESETS
from ananke.plexus.workflows.runner import parse_variables, run_workflow
from ananke.plexus.workflows.storage import WorkflowNotFoundError, delete_workflow, list_workflows


def _echo_step(*args: str) -> WorkflowStep:
    """A step that runs this same interpreter (portable across CI, no PATH assumptions)."""
    return WorkflowStep(command=[sys.executable, "-c", *args])


class TestModels:
    def test_rejects_bad_names(self) -> None:
        with pytest.raises(ValueError, match="invalid workflow name"):
            Workflow(name="has space", steps=[])
        with pytest.raises(ValueError, match="invalid workflow name"):
            Workflow(name="", steps=[])

    def test_rejects_blank_command_args(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            WorkflowStep(command=["ananke", ""])

    def test_rejects_empty_command(self) -> None:
        with pytest.raises(ValueError):
            WorkflowStep(command=[])

    def test_label_falls_back_to_command(self) -> None:
        assert WorkflowStep(command=["ananke", "doctor"]).label == "ananke doctor"
        assert WorkflowStep(command=["ananke"], description="Diagnose").label == "Diagnose"


class TestStorage:
    def test_round_trip(self, tmp_path: Path) -> None:
        wf = Workflow(
            name="demo",
            description="A demo workflow",
            steps=[
                WorkflowStep(description="Step one", command=["ananke", "doctor"]),
                WorkflowStep(command=["ananke", "verify"], continue_on_error=True),
            ],
        )
        path = save_workflow(tmp_path, wf)
        assert path == tmp_path / ".ananke" / "workflows" / "demo.toml"
        loaded = load_workflow(tmp_path, "demo")
        assert loaded == wf

    def test_file_is_deterministic_and_git_diffable(self, tmp_path: Path) -> None:
        wf = Workflow(name="demo", steps=[WorkflowStep(command=["ananke", "doctor"])])
        save_workflow(tmp_path, wf)
        first = (tmp_path / ".ananke" / "workflows" / "demo.toml").read_text()
        delete_workflow(tmp_path, "demo")
        save_workflow(tmp_path, wf)
        second = (tmp_path / ".ananke" / "workflows" / "demo.toml").read_text()
        assert first == second

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        wf = Workflow(name="demo", steps=[WorkflowStep(command=["ananke", "doctor"])])
        save_workflow(tmp_path, wf)
        with pytest.raises(FileExistsError):
            save_workflow(tmp_path, wf)
        save_workflow(tmp_path, wf, force=True)  # does not raise

    def test_missing_workflow_raises_named_error(self, tmp_path: Path) -> None:
        with pytest.raises(WorkflowNotFoundError):
            load_workflow(tmp_path, "nope")

    def test_list_and_delete(self, tmp_path: Path) -> None:
        assert list_workflows(tmp_path) == []
        save_workflow(tmp_path, Workflow(name="b", steps=[WorkflowStep(command=["x"])]))
        save_workflow(tmp_path, Workflow(name="a", steps=[WorkflowStep(command=["x"])]))
        assert list_workflows(tmp_path) == ["a", "b"]  # sorted
        assert delete_workflow(tmp_path, "a") is True
        assert delete_workflow(tmp_path, "a") is False
        assert list_workflows(tmp_path) == ["b"]


class TestVariableSubstitution:
    def test_parse_variables(self) -> None:
        assert parse_variables(["a=1", "b=two words"]) == {"a": "1", "b": "two words"}

    def test_parse_variables_rejects_bad_pairs(self) -> None:
        with pytest.raises(ValueError, match="KEY=VALUE"):
            parse_variables(["no-equals-sign"])

    def test_value_may_itself_contain_equals(self) -> None:
        assert parse_variables(["url=http://x?a=b"]) == {"url": "http://x?a=b"}


class TestRunner:
    def test_runs_steps_in_order(self, tmp_path: Path) -> None:
        marker = tmp_path / "order.txt"
        wf = Workflow(
            name="w",
            steps=[
                _echo_step(f"open('{marker}','a').write('1')"),
                _echo_step(f"open('{marker}','a').write('2')"),
            ],
        )
        result = run_workflow(wf, project=tmp_path)
        assert result.ok and all(s.ok for s in result.steps)
        assert marker.read_text() == "12"

    def test_stops_on_first_failure(self, tmp_path: Path) -> None:
        marker = tmp_path / "reached.txt"
        wf = Workflow(
            name="w",
            steps=[
                _echo_step("import sys; sys.exit(1)"),
                _echo_step(f"open('{marker}','w').write('x')"),
            ],
        )
        result = run_workflow(wf, project=tmp_path)
        assert not result.ok
        assert result.steps[0].ok is False and result.steps[0].exit_code == 1
        assert result.steps[1].skipped is True
        assert not marker.exists()

    def test_continue_on_error_keeps_going(self, tmp_path: Path) -> None:
        marker = tmp_path / "reached.txt"
        wf = Workflow(
            name="w",
            steps=[
                WorkflowStep(
                    command=[sys.executable, "-c", "import sys; sys.exit(3)"],
                    continue_on_error=True,
                ),
                _echo_step(f"open('{marker}','w').write('x')"),
            ],
        )
        result = run_workflow(wf, project=tmp_path)
        assert not result.ok  # the overall run still reports the failure...
        assert result.steps[0].exit_code == 3
        assert result.steps[1].skipped is False and result.steps[1].ok  # ...but didn't stop
        assert marker.exists()

    def test_substitutes_variables(self, tmp_path: Path) -> None:
        out = tmp_path / "seen.txt"
        wf = Workflow(
            name="w",
            steps=[_echo_step(f"open(r'{out}','w').write('${{greeting}}-${{project}}')")],
        )
        result = run_workflow(wf, project=tmp_path, variables={"greeting": "hi"})
        assert result.ok
        assert out.read_text() == f"hi-{tmp_path}"

    def test_unresolved_placeholder_fails_that_step_cleanly(self, tmp_path: Path) -> None:
        wf = Workflow(name="w", steps=[WorkflowStep(command=["echo", "${missing}"])])
        result = run_workflow(wf, project=tmp_path)
        assert not result.ok
        assert "missing" in result.steps[0].error
        assert result.steps[0].exit_code is None  # never actually executed

    def test_dry_run_executes_nothing(self, tmp_path: Path) -> None:
        marker = tmp_path / "should-not-exist.txt"
        wf = Workflow(name="w", steps=[_echo_step(f"open(r'{marker}','w').write('x')")])
        result = run_workflow(wf, project=tmp_path, dry_run=True)
        assert result.ok and result.dry_run
        assert not marker.exists()

    def test_missing_command_is_a_clean_error_not_a_crash(self, tmp_path: Path) -> None:
        wf = Workflow(name="w", steps=[WorkflowStep(command=["definitely-not-a-real-binary-xyz"])])
        result = run_workflow(wf, project=tmp_path)
        assert not result.ok
        assert "not found" in result.steps[0].error

    def test_runs_in_the_given_project_directory(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("here")
        out = tmp_path / "cwd_listing.txt"
        wf = Workflow(
            name="w",
            steps=[
                WorkflowStep(
                    command=[
                        sys.executable,
                        "-c",
                        f"import os; open(r'{out}','w').write(str('marker.txt' in os.listdir('.')))",
                    ]
                )
            ],
        )
        run_workflow(wf, project=tmp_path)
        assert out.read_text() == "True"


class TestPresets:
    def test_all_presets_are_valid_workflows_with_placeholders(self) -> None:
        assert set(BUILTIN_PRESETS) == {"setup", "verify-all", "registry-bootstrap"}
        for name, wf in BUILTIN_PRESETS.items():
            assert wf.name == name
            assert wf.steps, f"{name} has no steps"
            assert all("${project}" in " ".join(s.command) for s in wf.steps)

    def test_setup_preset_is_installable_and_idempotent_to_reinstall_with_force(
        self, tmp_path: Path
    ) -> None:
        path = save_workflow(tmp_path, BUILTIN_PRESETS["setup"])
        assert path.is_file()
        with pytest.raises(FileExistsError):
            save_workflow(tmp_path, BUILTIN_PRESETS["setup"])
        save_workflow(tmp_path, BUILTIN_PRESETS["setup"], force=True)


class TestWorkflowCli:
    def test_full_lifecycle(self, tmp_path: Path) -> None:
        runner = CliRunner()
        proj = ["--project", str(tmp_path)]

        empty = runner.invoke(app, ["workflow", "list", *proj])
        assert empty.exit_code == 0 and "0 workflow" in empty.output

        created = runner.invoke(
            app,
            [
                "workflow",
                "create",
                "smoke",
                "--step",
                f"{shlex.quote(sys.executable)} -c \"print('hi')\"",
                *proj,
            ],
        )
        assert created.exit_code == 0, created.output

        listed = runner.invoke(app, ["workflow", "list", *proj])
        assert listed.exit_code == 0 and "smoke" in listed.output

        shown = runner.invoke(app, ["workflow", "show", "smoke", *proj, "--json"])
        assert shown.exit_code == 0 and '"name": "smoke"' in shown.output

        ran = runner.invoke(app, ["workflow", "run", "smoke", *proj])
        assert ran.exit_code == 0, ran.output

        deleted = runner.invoke(app, ["workflow", "delete", "smoke", *proj])
        assert deleted.exit_code == 0
        assert runner.invoke(app, ["workflow", "delete", "smoke", *proj]).exit_code == 2

    def test_create_requires_at_least_one_step(self, tmp_path: Path) -> None:
        res = CliRunner().invoke(app, ["workflow", "create", "x", "--project", str(tmp_path)])
        assert res.exit_code == 2

    def test_install_pack_unknown_preset(self, tmp_path: Path) -> None:
        res = CliRunner().invoke(
            app, ["workflow", "install-pack", "nope", "--project", str(tmp_path)]
        )
        assert res.exit_code == 2 and "setup" in res.output  # lists what IS available

    def test_run_reports_failure_exit_code(self, tmp_path: Path) -> None:
        runner = CliRunner()
        proj = ["--project", str(tmp_path)]
        runner.invoke(
            app,
            [
                "workflow",
                "create",
                "bad",
                "--step",
                f"{shlex.quote(sys.executable)} -c 'import sys;sys.exit(1)'",
                *proj,
            ],
        )
        res = runner.invoke(app, ["workflow", "run", "bad", *proj])
        assert res.exit_code == 1

    def test_run_missing_workflow(self, tmp_path: Path) -> None:
        res = CliRunner().invoke(app, ["workflow", "run", "nope", "--project", str(tmp_path)])
        assert res.exit_code == 2

    def test_set_variable_flows_through_to_the_step(self, tmp_path: Path) -> None:
        runner = CliRunner()
        proj = ["--project", str(tmp_path)]
        out = tmp_path / "out.txt"
        runner.invoke(
            app,
            [
                "workflow",
                "create",
                "greet",
                "--step",
                f"{shlex.quote(sys.executable)} -c \"open(r'{out}','w').write('${{who}}')\"",
                *proj,
            ],
        )
        res = runner.invoke(app, ["workflow", "run", "greet", "--set", "who=world", *proj])
        assert res.exit_code == 0, res.output
        assert out.read_text() == "world"

    def test_install_setup_preset_and_run_it_for_real(self, tmp_path: Path) -> None:
        runner = CliRunner()
        proj = ["--project", str(tmp_path)]
        assert runner.invoke(app, ["workflow", "install-pack", "setup", *proj]).exit_code == 0
        res = runner.invoke(app, ["workflow", "run", "setup", *proj])
        assert res.exit_code == 0, res.output
        assert (tmp_path / ".ananke" / "config.toml").is_file()
        assert (tmp_path / ".ananke" / "policy").is_dir()

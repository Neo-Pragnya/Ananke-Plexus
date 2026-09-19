import os
import subprocess
import sys

import ananke


def test_package_exposes_version() -> None:
    assert hasattr(ananke, "__version__")
    assert isinstance(ananke.__version__, str)
    assert ananke.__version__


def test_module_entrypoint_runs_help() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(os.getcwd(), "src"), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    completed = subprocess.run(
        [sys.executable, "-m", "ananke", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.getcwd(),
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Ananke Plexus" in completed.stdout or "Usage:" in completed.stdout

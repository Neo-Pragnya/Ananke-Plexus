from pathlib import Path

from ananke.plexus.api import Ananke
from ananke.plexus.specs.models import Requirement


def test_graph_build_query_impact(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "module.py").write_text(
        "import json\n\nclass Demo:\n    pass\n\ndef run():\n    return 1\n",
        encoding="utf-8",
    )

    build = app.graph_build()
    assert build.ok

    query = app.graph_query("Demo")
    assert query.ok
    assert int(query.details["matches"]) >= 1

    impact = app.graph_impact(["src/module.py"])
    assert impact.ok
    assert int(impact.details["impacted_symbols"]) >= 1


def test_run_start_and_status(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    req = Requirement(
        requirement_id="RUN-100",
        title="Run flow",
        body="Run engine baseline",
        acceptance_criteria=["state file created"],
    )
    app.converge(req)

    started = app.run_start("RUN-100")
    assert started.ok
    run_id = str(started.details["run_id"])

    status = app.run_status(run_id)
    assert status.ok
    assert str(status.details["state"]) == "READY"

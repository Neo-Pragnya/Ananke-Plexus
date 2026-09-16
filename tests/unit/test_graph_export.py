from pathlib import Path

from ananke.plexus.api import Ananke


def test_graph_export_json_and_markdown(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    source = tmp_path / "src"
    source.mkdir(parents=True, exist_ok=True)
    (source / "demo.py").write_text("def x():\n    return 1\n", encoding="utf-8")

    app.graph_build()
    json_result = app.graph_export("json")
    md_result = app.graph_export("markdown")

    assert json_result.ok
    assert md_result.ok
    assert Path(str(json_result.details["path"])).exists()
    assert Path(str(md_result.details["path"])).exists()

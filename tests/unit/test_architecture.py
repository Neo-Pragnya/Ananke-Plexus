import json
from pathlib import Path

from ananke.plexus.api import Ananke


def test_arch_validate_diff_reconcile_render(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "module.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    validate = app.arch_validate()
    assert validate.ok
    assert int(validate.details["issue_count"]) == 0

    diff = app.arch_diff()
    assert not diff.ok
    assert int(diff.details["missing_from_calm"]) >= 1

    reconcile_preview = app.arch_reconcile(apply=False)
    assert reconcile_preview.ok
    assert reconcile_preview.details["applied"] is False

    reconcile_apply = app.arch_reconcile(apply=True)
    assert reconcile_apply.ok
    assert reconcile_apply.details["applied"] is True

    render = app.arch_render()
    assert render.ok
    render_path = Path(str(render.details["path"]))
    assert render_path.exists()
    assert "mermaid" in render_path.read_text(encoding="utf-8")


def test_arch_validate_detects_invalid_relationship(tmp_path: Path) -> None:
    app = Ananke.open(tmp_path)
    app.init_project()

    calm_path = tmp_path / ".ananke" / "architecture" / "system.calm.json"
    calm_payload = {
        "schema_version": "0.1",
        "system": {"name": "demo"},
        "components": [{"name": "api"}],
        "relationships": [{"source": "api", "target": "db", "kind": "uses"}],
    }
    calm_path.write_text(json.dumps(calm_payload, indent=2) + "\n", encoding="utf-8")

    validate = app.arch_validate()
    assert not validate.ok
    assert int(validate.details["issue_count"]) >= 1

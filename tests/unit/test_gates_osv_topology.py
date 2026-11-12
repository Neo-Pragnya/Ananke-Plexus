"""CAP-quality-cve-04 (rerun gates), CAP-repo-autodocs-01 (test topology), FUT-05 (offline OSV), retrieval promotion."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ananke.plexus import intel


def test_gates_rerun_only_what_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ananke.plexus.intel import gates

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert False\n"
    )
    result = {
        "findings": [
            {"kind": "test", "test": "tests/test_a.py::test_bad", "source": "junit"},
            {"kind": "code", "source": "ruff"},
        ],
        "rerun": [{"gate": "tests"}, {"gate": "ruff"}],
        "changed": [],
    }
    monkeypatch.setattr(
        gates.shutil, "which", lambda tool: None if tool == "semgrep" else f"/bin/{tool}"
    )
    records = {
        r["gate"]: r for r in gates.rerun(tmp_path, ["tests", "semgrep", "nonsense"], result)
    }
    assert records["tests"]["status"] == "executed: failed"
    assert records["tests"]["command"][-1] == "tests/test_a.py::test_bad"  # only the failing test
    assert records["tests"]["findings_before"] == 1
    assert records["semgrep"]["status"] == "not installed"
    assert records["nonsense"]["status"] == "not runnable here"
    (tmp_path / "tests" / "test_a.py").write_text("def test_bad():\n    assert True\n")
    again = gates.rerun(tmp_path, ["tests"], result)[0]
    assert again["status"] == "executed: passed"


def test_gate_counts_sarif_results(tmp_path: Path) -> None:
    from ananke.plexus.intel.gates import _count

    (tmp_path / "ruff.sarif").write_text(
        json.dumps({"runs": [{"results": [{}, {}]}, {"results": [{}]}]})
    )
    assert _count("ruff", tmp_path) == 3
    assert _count("semgrep", tmp_path) is None


def test_test_topology_section(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "core.py").write_text("def f():\n    return 1\n")
    (tmp_path / "app" / "lonely.py").write_text("def g():\n    return 2\n")
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_core.py").write_text(
        "import pytest\nfrom app.core import f\n\n\ndef test_f():\n    assert f() == 1\n\n\ndef test_g():\n    pass\n"
    )
    (tmp_path / "tests" / "e2e" / "home.spec.ts").write_text(
        "import { test } from '@playwright/test'\ntest('home', async () => {})\n"
    )
    (tmp_path / "tests" / "conftest.py").write_text("")
    section = intel.analyze(tmp_path)["sections"]["tests"]
    assert section["status"] == "found"
    suites = {(s["kind"], s["framework"]): s for s in section["suites"]}
    assert suites[("unit", "pytest")]["tests"] == 2
    assert suites[("unit", "pytest")]["exercises"] == ["app/core.py"]
    assert suites[("end-to-end", "playwright")]["tests"] == 1
    assert section["untested"] == ["app/lonely.py"]
    assert "tests/conftest.py" in section["setup"]


def _advisory(
    vid: str, name: str, events: list[dict[str, str]], versions: list[str] | None = None
) -> dict:
    return {
        "id": vid,
        "modified": "2026-01-01T00:00:00Z",
        "summary": f"{vid} summary",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": name},
                "ranges": [{"type": "ECOSYSTEM", "events": events}],
                "versions": versions or [],
            }
        ],
    }


def test_offline_osv_import_and_match(tmp_path: Path) -> None:
    from ananke.plexus.intel.osv_db import OsvDbError, import_export, query, status

    export = tmp_path / "all.zip"
    with zipfile.ZipFile(export, "w") as z:
        z.writestr(
            "PYSEC-1.json",
            json.dumps(_advisory("PYSEC-1", "Jinja2", [{"introduced": "0"}, {"fixed": "3.1.5"}])),
        )
        z.writestr(
            "PYSEC-2.json",
            json.dumps(
                _advisory("PYSEC-2", "jinja2", [{"introduced": "2.0"}, {"last_affected": "2.11.3"}])
            ),
        )
        z.writestr(
            "PYSEC-3.json",
            json.dumps({**_advisory("PYSEC-3", "jinja2", [{"introduced": "0"}]), "withdrawn": "x"}),
        )
        z.writestr("PYSEC-4.json", json.dumps(_advisory("PYSEC-4", "requests", [], ["2.31.0"])))
        z.writestr("junk.json", "not json")
    db = tmp_path / "osv.sqlite"
    info = import_export(export, db)
    assert info["imported"] == 4 and info["skipped"] == 1
    assert status(db)["ecosystems"] == "PyPI"
    ids, details = query(
        db,
        [
            ("PyPI", "Jinja2", "2.11.3"),
            ("PyPI", "jinja2", "3.1.5"),
            ("PyPI", "requests", "2.31.0"),
            ("PyPI", "requests", "2.32.0"),
        ],
    )
    assert ids == [["PYSEC-1", "PYSEC-2"], [], ["PYSEC-4"], []]
    assert set(details) == {"PYSEC-1", "PYSEC-2", "PYSEC-4"}
    with pytest.raises(OsvDbError, match="doesn't exist"):
        import_export(tmp_path / "missing.zip", db)
    (tmp_path / "notzip.zip").write_text("x")
    with pytest.raises(OsvDbError, match="isn't a zip"):
        import_export(tmp_path / "notzip.zip", db)


def test_dependencies_use_the_offline_db_and_say_what_they_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ananke.plexus.intel import dependencies
    from ananke.plexus.intel.context import RepoContext
    from ananke.plexus.intel.osv_db import import_export

    folder = tmp_path / "osv"
    folder.mkdir()
    (folder / "a.json").write_text(
        json.dumps(_advisory("PYSEC-9", "jinja2", [{"introduced": "0"}, {"fixed": "3.1.5"}]))
    )
    db = tmp_path / "osv.sqlite"
    import_export(folder, db)
    project = tmp_path / "p"
    project.mkdir()
    (project / "requirements.txt").write_text("jinja2==3.1.4\n")
    (project / "package.json").write_text(json.dumps({"dependencies": {"left-pad": "1.0.0"}}))
    monkeypatch.setenv("ANANKE_OSV_DB", str(db))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("the offline check must not use the network")

    monkeypatch.setattr(dependencies, "_request", no_network)
    result = dependencies.analyze(RepoContext.open(project, check_vulnerabilities=True))
    assert result["scanner"] == "osv-offline"
    assert [(v["package"], v["id"]) for v in result["vulnerabilities"]] == [("jinja2", "PYSEC-9")]
    assert any("npm packages weren't checked" in x for x in result["limits"])


def test_retrieval_promotes_the_code_a_matching_test_calls(tmp_path: Path) -> None:
    from ananke.plexus.graph.service import build_graph
    from ananke.plexus.retrieval import plan

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "contracts.py").write_text("def gaps(routes):\n    return routes\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_contracts.py").write_text(
        "from pkg.contracts import gaps\n\n\ndef test_routes_are_matched_to_test_calls():\n    assert gaps([]) == []\n"
    )
    names = [c["name"] for c in plan("how are routes matched to test calls", build_graph(tmp_path))]
    assert "gaps" in names[:3]

"""CAP-auto-sdd-01/03: an approved PRD mapped to features, acceptance ids and tests; BMAD contracts compiled from it,
linked to the PRD revision; an inconsistent PRD blocks compiling."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.specs.prd import PrdError, compile_prd, map_prd

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "api-contract"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def _prd(root: Path, body: str) -> Path:
    path = root / "prd.md"
    path.write_text(body)
    return path


def test_every_criterion_gets_an_id_a_planned_test_and_its_tests(repo: Path) -> None:
    m = map_prd(repo / "docs" / "orders-prd.md", repo)
    assert m["consistent"] is True
    assert m["prd"]["status"] == "approved" and m["prd"]["approved_by"] == "product-owner"
    assert [f["title"] for f in m["features"]] == ["Placing orders", "Looking up orders"]
    r1 = next(r for r in m["requirements"] if r["id"] == "R1")
    assert r1["feature_id"] == "F1"
    assert [a["id"] for a in r1["acceptance"]] == ["AC-R1-1", "AC-R1-2"]
    assert (
        r1["acceptance"][0]["text"] == "An order with no items is rejected with 422."
    )  # the inline bullet
    assert r1["acceptance"][0]["planned_test"]["name"].startswith(
        "test_ac_1_an_order_with_no_items"
    )
    r2 = next(r for r in m["requirements"] if r["id"] == "R2")
    assert r2["acceptance"][0]["tested"] == "by acceptance id"
    assert r2["acceptance"][0]["observed_tests"] == ["tests/test_prd.py:2"]
    assert m["summary"] == {
        "features": 2, "requirements": 4, "acceptance": 5, "acceptance_with_tests": 1, "errors": 0, "warnings": 4,
    }  # fmt: skip


def test_same_prd_same_digest(repo: Path) -> None:
    prd = repo / "docs" / "orders-prd.md"
    assert map_prd(prd, repo)["digest"] == map_prd(prd, repo)["digest"]


def test_draft_prd_is_inconsistent_unless_allowed(repo: Path) -> None:
    prd = _prd(
        repo,
        "---\nstatus: draft\n---\n# X\n## Requirements\n- **R1** Do it.\n## Acceptance\n- R1: it is done.\n",
    )
    blocked = map_prd(prd, repo)
    assert blocked["consistent"] is False
    assert blocked["findings"][0]["code"] == "PRD_NOT_APPROVED"
    assert map_prd(prd, repo, allow_draft=True)["consistent"] is True


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("## Requirements\n- **R1** Do it.\n", "REQUIREMENT_WITHOUT_ACCEPTANCE"),
        (
            "## Requirements\n- **R1** A.\n- **R1** B.\n## Acceptance\n- R1: ok.\n",
            "DUPLICATE_REQUIREMENT",
        ),
        (
            "## Requirements\n- **R1** A.\n## Acceptance\n- R1: ok.\n- R9: who?\n",
            "ACCEPTANCE_UNKNOWN_REQUIREMENT",
        ),
        ("## Problem\nNothing listed.\n", "NO_REQUIREMENTS"),
    ],
)
def test_inconsistencies_are_errors(repo: Path, body: str, code: str) -> None:
    m = map_prd(_prd(repo, "---\nstatus: approved\n---\n# X\n" + body), repo)
    assert m["consistent"] is False
    assert code in {f["code"] for f in m["findings"] if f["severity"] == "error"}


def test_require_tests_turns_untested_into_errors(repo: Path) -> None:
    m = map_prd(repo / "docs" / "orders-prd.md", repo, require_tests=True)
    assert m["consistent"] is False
    assert {f["code"] for f in m["findings"]} == {"ACCEPTANCE_UNTESTED"}


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [("prd.docx", "x", "unsupported format"), ("prd.md", b"\xff\xfe", "isn't UTF-8"),
     ("prd.md", "---\nstatus: approved\n# never closed\n", "never ends")],
)  # fmt: skip
def test_bad_prds_give_actionable_errors(
    tmp_path: Path, name: str, content: str | bytes, message: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    with pytest.raises(PrdError, match=message):
        map_prd(path, tmp_path)
    with pytest.raises(PrdError, match="doesn't exist"):
        map_prd(tmp_path / "missing.md", tmp_path)


def test_compile_links_every_file_to_the_prd_and_fills_contracts(
    repo: Path, tmp_path: Path
) -> None:
    from ananke.plexus import intel
    from ananke.plexus.intel.api_contract import build

    api = intel.analyze(repo)["sections"]["api"]
    files = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file()]
    contract = build(repo, api, files)["model_contract"]
    calm = {"nodes": [{"unique-id": "orders-api", "name": "Orders API", "node-type": "service"},
                      {"unique-id": "billing", "name": "Billing", "node-type": "service"}]}  # fmt: skip
    m = map_prd(repo / "docs" / "orders-prd.md", repo)
    m["requirements"][0]["text"] += " Served by orders-api."
    out = tmp_path / "bmad"
    index = compile_prd(m, out, api_contract=contract, calm=calm)
    assert index["prd"]["sha256"] == m["prd"]["sha256"]
    assert index["prd_map_digest"] == m["digest"]
    by_req = {e["requirement"]: e for e in index["requirements"]}
    assert by_req["R1"]["acceptance"] == ["AC-R1-1", "AC-R1-2"]
    assert by_req["R1"]["api_operations_mapped"] and by_req["R3"]["api_operations_mapped"]
    assert not by_req["R2"]["api_operations_mapped"]
    assert all(len(sha) == 64 for e in index["requirements"] for sha in e["files"].values())
    model = yaml.safe_load((out / "r1" / "model-contract.yaml").read_text())
    assert [o["operation_id"] for o in model["operations"]] == ["post_orders"]
    assert {s["name"] for s in model["schemas"]} == {"Item", "OrderIn", "OrderOut"}
    assert "unmapped" in yaml.safe_load((out / "r2" / "model-contract.yaml").read_text())
    arch = yaml.safe_load((out / "r1" / "architecture-contract.yaml").read_text())
    assert [c["unique_id"] for c in arch["components"]] == ["orders-api"]
    bmad = yaml.safe_load(
        (out / "r4" / "bmad.yaml").read_text()
    )  # a quote in the criterion stays valid YAML
    assert bmad["behavior"]["scenarios"][0]["acceptance_id"] == "AC-R4-1"
    tests = (out / "r1" / "tests" / "test_r1_behavior.py").read_text()
    assert m["requirements"][0]["acceptance"][0]["planned_test"]["name"] in tests
    assert json.loads((out / "bmad-index.json").read_text())["digest"] == index["digest"]


def test_compile_refuses_an_inconsistent_prd(repo: Path, tmp_path: Path) -> None:
    m = map_prd(_prd(repo, "---\nstatus: draft\n---\n## Requirements\n- **R1** A.\n"), repo)
    with pytest.raises(PrdError, match="nothing was compiled"):
        compile_prd(m, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_cli_map_and_compile(repo: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    prd = repo / "docs" / "orders-prd.md"
    done = runner.invoke(app, ["spec", "map", str(prd), "--project", str(repo), "--json"])
    assert done.exit_code == 0, done.output
    assert json.loads(done.output)["consistent"] is True
    contract_dir = tmp_path / "ac"
    assert (
        runner.invoke(
            app, ["triage", "api-contract", "--project", str(repo), "--out", str(contract_dir)]
        ).exit_code
        == 0
    )
    out = tmp_path / "bmad"
    done = runner.invoke(
        app,
        ["spec", "compile", str(prd), "--project", str(repo), "--out", str(out),
         "--api-contract", str(contract_dir / "model-contract.yaml")],
    )  # fmt: skip
    assert done.exit_code == 0, done.output
    assert "4 BMAD bundles, 2 with API operations" in done.output
    draft = _prd(
        repo, "---\nstatus: draft\n---\n## Requirements\n- **R1** A.\n## Acceptance\n- R1: ok.\n"
    )
    assert runner.invoke(app, ["spec", "map", str(draft), "--project", str(repo)]).exit_code == 1
    assert (
        runner.invoke(
            app, ["spec", "compile", str(draft), "--project", str(repo), "--out", str(out)]
        ).exit_code
        == 1
    )
    bad = runner.invoke(
        app, ["spec", "compile", str(prd), "--api-contract", str(prd), "--out", str(out)]
    )
    assert bad.exit_code == 2

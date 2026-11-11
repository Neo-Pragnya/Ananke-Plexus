"""Workflow assembly on real registry records: typed ports, policy, stable hash, no effects, explained suggestions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from registry_support import add, open_registry

from ananke.plexus.assembly import (
    STEP_TYPE,
    CompileError,
    compile_workflow,
    preview,
    suggest,
    validate,
)
from ananke.plexus.execution.scheduler import run_plan
from ananke.plexus.registry.models import TrustStatus
from ananke.plexus.registry.registry import Registry

WWI = json.loads((Path(__file__).parents[1] / "fixtures" / "wwi-assembly.json").read_text())


def contract(ports: list[tuple[str, str]]) -> dict:
    return {"native": {"ports": [{"name": n, "schema_ref": s} for n, s in ports]}}


@pytest.fixture
def reg(tmp_path: Path) -> Registry:
    r = open_registry(tmp_path)
    for n in WWI["nodes"]:
        ns, _, rest = n["capability_ref"].partition("/")
        name, _, version = rest.partition("@")
        perms = (
            {"filesystem": {"read": ["**"]}}
            if "filesystem-read" in n.get("permission_profile", [])
            else {}
        )
        add(
            r,
            name,
            version,
            namespace=ns,
            kind="skill" if n["kind"] == "skill" else "tool",
            approve=True,
            permissions=perms,
            inputs=contract([(p["name"], p["schema_ref"]) for p in n["inputs"]]),
            outputs=contract([(p["name"], p["schema_ref"]) for p in n["outputs"]]),
        )
    return r


def codes(draft: dict, reg: Registry) -> set[str]:
    return {i.code for i in validate(draft, reg).issues}


def test_the_spec_example_validates_and_compiles_to_the_existing_plan(reg: Registry) -> None:
    v = validate(WWI, reg)
    assert v.ok, v.issues
    done = compile_workflow(WWI, reg)
    assert done.plan.spec_id == WWI["workflow_id"] and {s.step_type for s in done.plan.steps} == {
        STEP_TYPE
    }
    ids = [s.step_id for s in done.plan.steps]
    run = next(s for s in done.plan.steps if s.step_id == "run")
    assert run.depends_on == ["plan"] and run.params["bindings"] == {"tests": ["plan.tests"]}
    assert ids.index("plan") < ids.index("run") < ids.index("gate")
    assert {a.step_id for a in done.plan.approvals} == {"run", "gate"}


def test_plan_hash_is_stable_and_covers_what_matters(reg: Registry) -> None:
    first = compile_workflow(WWI, reg)
    shuffled = copy.deepcopy(WWI)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    shuffled["title"] = "A new title"
    shuffled["ui"] = {"x": 1}
    second = compile_workflow(shuffled, reg)
    assert (
        second.plan_hash == first.plan_hash and second.plan.model_dump() == first.plan.model_dump()
    )
    changed = copy.deepcopy(WWI)
    changed["nodes"][0]["config"] = {"strict": True}
    assert compile_workflow(changed, reg).plan_hash != first.plan_hash


def test_preview_explains_and_has_no_effects(
    reg: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def fingerprint() -> str:
        h = hashlib.sha256()
        for p in sorted(tmp_path.rglob("*")):
            if p.is_file() and p.suffix not in (".db", ".db-wal", ".db-shm"):
                h.update(str(p).encode() + p.read_bytes())
        return h.hexdigest()

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("a preview must not start a process")

    before = fingerprint()
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    out = preview(WWI, reg)
    assert out["ok"] and out["plan_hash"] and out["steps"][0]["node"] == "extract"
    assert out["approvals_needed"] == ["PLAN"] and out["workflow_inputs"] == ["extract.source"]
    assert out["steps"][0]["permissions"] == ["filesystem.read"]
    assert fingerprint() == before


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])), "DUPLICATE_ID"),
        (
            lambda d: d["edges"].append(
                {
                    "id": "back",
                    "source": "gate",
                    "source_port": "evidence",
                    "target": "extract",
                    "target_port": "source",
                }
            ),
            "CYCLE",
        ),
        (lambda d: d["nodes"][1]["inputs"][0].update(schema_ref="markdown/v1"), "PORT_MISMATCH"),
        (
            lambda d: (d["edges"].pop(0), d["nodes"][1]["inputs"][0].update(required=True)),
            "UNBOUND_INPUT",
        ),
        (
            lambda d: d["nodes"][0].update(capability_ref="fixture/api-contract-extract"),
            "UNPINNED_REF",
        ),
        (lambda d: d["nodes"][0].update(capability_ref="fixture/nothing@1.0.0"), "UNRESOLVED_REF"),
        (lambda d: d["edges"][0].update(target="ghost"), "UNKNOWN_NODE"),
        (lambda d: d["edges"][0].update(source_port="ghost"), "UNKNOWN_PORT"),
        (lambda d: d["nodes"][0].update(approval_class="WHATEVER"), "UNKNOWN_APPROVAL"),
        (lambda d: d["nodes"][0].update(retries=99), "INVALID_RETRIES"),
        (lambda d: d.update(schema_version="9"), "UNSUPPORTED_SCHEMA"),
        (
            lambda d: d["nodes"][0].update(
                capability_ref=d["nodes"][0]["capability_ref"] + "#sha256:" + "0" * 64
            ),
            "DIGEST_MISMATCH",
        ),
    ],
)
def test_bad_drafts_are_rejected_with_a_stable_code(
    reg: Registry, mutate: object, code: str
) -> None:
    draft = copy.deepcopy(WWI)
    mutate(draft)  # type: ignore[operator]
    assert code in codes(draft, reg), validate(draft, reg).issues
    with pytest.raises(CompileError):
        compile_workflow(draft, reg)


def test_no_privilege_escalation(reg: Registry) -> None:
    draft = copy.deepcopy(WWI)
    draft["nodes"][0]["permission_profile"] = []  # the capability needs filesystem.read
    assert "PERMISSION_NOT_GRANTED" in codes(draft, reg)
    wide = copy.deepcopy(WWI)
    wide["nodes"][1]["permission_profile"] = ["network", "shell"]  # asking for more grants nothing
    step = next(s for s in compile_workflow(wide, reg).plan.steps if s.step_id == "plan")
    assert step.params["permissions"] == []


def test_draft_ports_must_match_what_the_capability_declares(reg: Registry) -> None:
    draft = copy.deepcopy(WWI)
    draft["nodes"][4]["outputs"][0]["schema_ref"] = (
        "sarif/v2"  # a draft can't claim an output the manifest doesn't
    )
    assert "PORT_CONTRACT_MISMATCH" in codes(draft, reg)


def test_trust_and_lifecycle_come_from_the_registry(reg: Registry) -> None:
    add(reg, "fresh", "1.0.0", namespace="fixture", kind="tool", trust=TrustStatus.UNKNOWN)
    draft = copy.deepcopy(WWI)
    draft["nodes"][4]["capability_ref"] = "fixture/fresh@1.0.0"
    v = validate(draft, reg)
    assert "UNTRUSTED" in {i.code for i in v.issues}
    reg.quarantine("fixture/docs-render@1.0.0", reason="bad")
    assert codes(WWI, reg) & {"LIFECYCLE_BLOCKED", "UNTRUSTED"}


def test_suggestions_explain_and_never_activate(reg: Registry) -> None:
    add(
        reg,
        "unsafe-extract",
        "1.0.0",
        namespace="fixture",
        kind="tool",
        trust=TrustStatus.UNKNOWN,
        outputs=contract([("openapi", "openapi/v3")]),
    )
    add(
        reg,
        "other",
        "1.0.0",
        namespace="fixture",
        kind="tool",
        approve=True,
        outputs=contract([("graph", "code-graph/v1")]),
    )
    before = reg.exact_version("fixture/unsafe-extract@1.0.0").trust
    out = suggest(
        WWI,
        "plan",
        "openapi",
        [
            "fixture/api-contract-extract@1.0.0",
            "fixture/unsafe-extract@1.0.0",
            "fixture/other@1.0.0",
            "fixture/gone@1.0.0",
        ],
        reg,
    )
    rows = {r["ref"]: r for r in out["candidates"]}
    assert out["requires"] == "openapi/v3"
    good = rows["fixture/api-contract-extract@1.0.0"]
    assert good["compatible"] and good["authorized"] and good["output_port"] == "openapi"
    unsafe = rows["fixture/unsafe-extract@1.0.0"]
    assert unsafe["compatible"] and not unsafe["authorized"]  # compatible isn't authorized
    assert (
        not rows["fixture/other@1.0.0"]["compatible"]
        and "code-graph/v1, not openapi/v3" in rows["fixture/other@1.0.0"]["reasons"][0]
    )
    assert "can't be resolved" in rows["fixture/gone@1.0.0"]["reasons"][0]
    assert all(r["auto_activate"] is False for r in rows.values())
    assert reg.exact_version("fixture/unsafe-extract@1.0.0").trust == before
    assert "error" in suggest(WWI, "plan", "nope", [], reg)


def test_compiled_plan_runs_on_the_existing_scheduler(reg: Registry, tmp_path: Path) -> None:
    ran: list[str] = []

    def handler(step: object, root: Path) -> dict[str, object]:
        ran.append(step.step_id)  # type: ignore[attr-defined]
        return {"status": "SUCCEEDED"}

    plan = compile_workflow(WWI, reg).plan
    plan.run_id = "run-test"
    done = run_plan(tmp_path, plan, handlers={STEP_TYPE: handler}, max_workers=1)
    assert done.state == "SUCCEEDED" and ran.index("extract") < ran.index("run") < ran.index("docs")


def test_a_capability_step_nothing_executed_fails_instead_of_succeeding(
    reg: Registry, tmp_path: Path
) -> None:
    plan = compile_workflow(WWI, reg).plan
    plan.run_id = "run-no-executor"
    done = run_plan(tmp_path, plan, max_workers=1)
    assert done.state == "FAILED"
    first = next(s for s in done.steps if s.state == "FAILED")
    assert "did not run" in first.error


@pytest.mark.parametrize(
    "change",
    [{"on_failure": "continue"}, {"kind": "banana"}],
    ids=["unknown-field", "unsupported-kind"],
)
def test_what_the_compiler_cant_honour_is_a_diagnostic_not_ignored(
    reg: Registry, change: dict[str, str]
) -> None:
    draft = copy.deepcopy(WWI)
    draft["nodes"][0].update(change)
    result = validate(draft, reg)
    assert not result.ok
    assert all(i.code and i.message for i in result.issues)


def test_gaps_name_each_unfed_input_and_what_could_feed_it(reg: Registry) -> None:
    from ananke.plexus.assembly import gaps

    draft = copy.deepcopy(WWI)
    draft["edges"] = [
        e for e in draft["edges"] if e["target"] != "plan"
    ]  # nothing feeds plan.openapi now
    for node in draft["nodes"]:
        if node["id"] == "plan":
            for port in node["inputs"]:
                port["required"] = True  # so the run can't supply it
    add(
        reg,
        "unsafe-extract",
        "1.0.0",
        namespace="fixture",
        kind="tool",
        trust=TrustStatus.UNKNOWN,
        outputs=contract([("openapi", "openapi/v3")]),
    )
    before = json.dumps(draft, sort_keys=True)
    out = gaps(draft, reg)
    (gap,) = [g for g in out["gaps"] if g["node"] == "plan"]
    assert gap["requires"] == "openapi/v3"
    assert [r["ref"] for r in gap["authorized"]] == ["fixture/api-contract-extract@1.0.0"]
    assert [r["ref"] for r in gap["not_authorized"]] == ["fixture/unsafe-extract@1.0.0"]
    assert gap["incompatible"] > 0 and gap["fix"].startswith(
        "connect fixture/api-contract-extract@1.0.0"
    )
    assert out["auto_activate"] is False and json.dumps(draft, sort_keys=True) == before
    assert all(
        g["node"] != "extract" for g in out["gaps"]
    )  # an entry input the run supplies isn't a gap
    assert gaps(WWI, reg)["gaps"] == []  # the complete example has none


def test_a_proposal_gets_the_same_checks_and_is_never_compiled(reg: Registry) -> None:
    from ananke.plexus.assembly import review_proposal

    good = review_proposal(WWI, reg, proposer="a model the user ran")
    assert good["status"] == "proposal" and good["valid"] and good["compiled"] is False
    assert good["preview"]["plan_hash"] == preview(WWI, reg)["plan_hash"]
    bad = copy.deepcopy(WWI)
    bad["nodes"][0]["permission_profile"] = [
        "network-unrestricted"
    ]  # asking for more than it declares
    bad["nodes"][0]["capability_ref"] = "fixture/not-registered@9.9.9"
    out = review_proposal(bad, reg, proposer="template")
    assert not out["valid"] and out["preview"] is None and out["issues"]
    with pytest.raises(ValueError, match="what proposed"):
        review_proposal(WWI, reg, proposer=" ")

"""ADV02, 03, 04, 07, 09, 10, 11, 12, 14, 17, 01: the advanced views, each on a small real git repository."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.insight import InsightError

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@x.invalid", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "pricing.py").write_text("def total(items):\n    return sum(items)\n")
    (root / "app" / "orders.py").write_text(
        "from app.pricing import total\n\n\ndef place(items):\n    return total(items)\n"
    )
    (root / "tests" / "test_orders.py").write_text(
        "from app.orders import place\n\n\ndef test_place():\n    assert place([1, 2]) == 3\n"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


# ---------------------------------------------------------------- ADV02 time machine


def test_graph_diff_finds_the_new_relationship(repo: Path) -> None:
    from ananke.plexus.insight.timemachine import diff

    (repo / "app" / "audit.py").write_text(
        "from app.orders import place\n\n\ndef log(x):\n    return place(x)\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "audit")
    report = diff(repo, "HEAD~1", "HEAD")
    assert report["summary"]["nodes_added"] >= 2
    assert any(
        e["source"] == "function:app/audit.py:log" and e["kind"] == "calls"
        for e in report["edges_added"]
    )
    assert diff(repo, "HEAD~1", "HEAD")["digest"] == report["digest"]
    with pytest.raises(InsightError, match="isn't a commit"):
        diff(repo, "no-such-ref")


def test_likely_moves_are_hypotheses(repo: Path) -> None:
    from ananke.plexus.insight.timemachine import diff

    (repo / "app" / "money.py").write_text((repo / "app" / "pricing.py").read_text())
    (repo / "app" / "pricing.py").unlink()
    (repo / "app" / "orders.py").write_text(
        "from app.money import total\n\n\ndef place(items):\n    return total(items)\n"
    )
    report = diff(repo, "HEAD")  # against the working tree
    moves = report["likely_moves"]
    assert moves and moves[0]["name"] == "total" and moves[0]["status"].startswith("hypothesis")


# ---------------------------------------------------------------- ADV03 what-if


def test_whatif_reaches_callers_and_tests(repo: Path) -> None:
    from ananke.plexus.insight.whatif import run

    report = run(repo, ["app/pricing.py"], depth=3)
    assert "app/orders.py" in report["files"]
    assert report["tests_to_run"] == ["tests/test_orders.py"]
    assert all(r["status"] == "simulated" for r in report["impacted"])
    assert all(0 < r["confidence"] <= 1 for r in report["impacted"])
    with pytest.raises(InsightError, match="is in the code graph"):
        run(repo, ["nowhere.py"])


def test_whatif_backtest_measures_against_history(repo: Path) -> None:
    from ananke.plexus.insight.whatif import backtest

    for i in range(3):
        (repo / "app" / "pricing.py").write_text(
            f"def total(items):\n    return sum(items) + {i}\n"
        )
        (repo / "app" / "orders.py").write_text(
            f"from app.pricing import total\n\n\ndef place(items):\n    return total(items) * {i + 1}\n"
        )
        _git(repo, "commit", "-qam", f"change {i}")
    result = backtest(repo, commits=5, depth=1)
    changes = [c for c in result["cases"] if c["seed"] == "app/orders.py"]
    assert len(changes) >= 3
    assert all(
        c["both_hits"] == c["co_changed"] == 1 for c in changes
    )  # orders calls pricing: always predicted
    assert all(c["hits"] == 0 for c in changes)  # dependants alone can't see a callee
    assert 0 <= result["baseline_recall"] <= 1


# ---------------------------------------------------------------- ADV04 test gaps


def test_test_gaps_from_branches_failures_and_contracts(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.testgaps import pytest_file, synthesize

    (repo / "app" / "pricing.py").write_text(
        "def total(items):\n    if not items:\n        return 0\n    return sum(items)\n"
    )
    cov = tmp_path / "coverage.xml"
    cov.write_text(
        '<?xml version="1.0" ?><coverage><sources><source>'
        + str(repo)
        + "</source></sources><packages><package>"
        '<classes><class filename="app/pricing.py"><lines>'
        '<line number="2" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="3"/>'
        "</lines></class></classes></package></packages></coverage>"
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite><testcase classname="tests.test_orders" name="test_place" file="tests/test_orders.py" '
        'line="4"><failure message="assert 4 == 3">boom</failure></testcase></testsuite>'
    )
    report = synthesize(repo, coverage=cov, junit=junit)
    kinds = {c["kind"]: c for c in report["tests"]}
    assert kinds["missing-branch"]["evidence"] == "app/pricing.py:2"
    assert (
        "if not items:" in kinds["missing-branch"]["why"]
        and "line 3" in kinds["missing-branch"]["why"]
    )
    assert kinds["missing-branch"]["target"] == "app.pricing.total"
    assert kinds["observed-failure"]["why"].startswith("failure: assert 4 == 3")
    text = pytest_file(report)
    compile(text, "gaps.py", "exec")
    assert text.count("pytest.fail(") == len(report["tests"])


def test_test_gaps_refuse_unsafe_or_wrong_input(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.testgaps import synthesize

    bad = tmp_path / "coverage.xml"
    bad.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><coverage/>')
    with pytest.raises(InsightError, match="DOCTYPE"):
        synthesize(repo, coverage=bad)
    with pytest.raises(InsightError, match="at least one source"):
        synthesize(repo)
    with pytest.raises(InsightError, match="unsupported format"):
        synthesize(repo, coverage=tmp_path / "x.lcov") if (tmp_path / "x.lcov").write_text(
            "x"
        ) else None


# ---------------------------------------------------------------- ADV17 notebooks


def _nb(cells: list[tuple[str, int | None, list[dict[str, object]]]]) -> str:
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"version": "3.12.1"}},
            "cells": [
                {
                    "cell_type": "code",
                    "source": src,
                    "execution_count": n,
                    "outputs": outs,
                    "metadata": {},
                }
                for src, n, outs in cells
            ],
        }
    )


def test_notebook_order_lineage_and_environment(tmp_path: Path) -> None:
    from ananke.plexus.insight.notebooks import inspect_all

    (tmp_path / "requirements.txt").write_text("pandas\n")
    (tmp_path / "good.ipynb").write_text(
        _nb([("import pandas as pd\nx = 1", 1, []), ("print(x)", 2, [])])
    )
    (tmp_path / "bad.ipynb").write_text(
        _nb(
            [
                ("import numpy as np\ny = x + 1", 3, []),
                ("x = 5", 2, []),
                ("df = pd.read_csv('missing.csv')", None, []),
                (
                    "1/0",
                    7,
                    [
                        {
                            "output_type": "error",
                            "ename": "ZeroDivisionError",
                            "evalue": "division by zero",
                        }
                    ],
                ),
            ]
        )
    )
    report = inspect_all(tmp_path)
    by = {n["notebook"]: n for n in report["notebooks"]}
    assert by["good.ipynb"]["verdict"] == "likely reproducible"
    rules = {f["rule"] for f in by["bad.ipynb"]["findings"]}
    assert {
        "out-of-order",
        "used-before-defined",
        "undefined-name",
        "never-run",
        "error-output",
        "undeclared-import",
        "missing-input",
        "hidden-runs",
    } <= rules
    assert by["bad.ipynb"]["verdict"] == "not reproducible as saved"
    assert inspect_all(tmp_path)["digest"] == report["digest"]


# ---------------------------------------------------------------- ADV07 budget observatory


def test_observatory_records_and_flags_regressions(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.budget import compare, load_cases, observe, record

    cases = tmp_path / "q.json"
    cases.write_text(
        json.dumps([{"question": "how are order totals computed", "expect": ["total"]}])
    )
    first = observe(repo, load_cases(cases), (200, 1000))
    assert first["best_recall"] == 1.0 and first["knee_budget"] == 200
    history = tmp_path / "h.jsonl"
    record(repo, first, history)
    worse = json.loads(json.dumps(first))
    worse["per_question"]["how are order totals computed"]["200"]["recall"] = 0.0
    assert compare(first, worse)[0]["change"] == "recall dropped"
    _, previous = record(repo, first, history)
    assert previous is not None and previous["digest"] == first["digest"]
    bad = tmp_path / "bad.json"
    bad.write_text('[{"question": "x"}]')
    with pytest.raises(InsightError, match="expect"):
        load_cases(bad)


# ---------------------------------------------------------------- ADV09 drift


def test_drift_between_manifests_and_snapshots(tmp_path: Path) -> None:
    from ananke.plexus.insight.drift import compare

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: shop/api:1.4\n    ports: ['8000:8000']\n    environment:\n      DB_URL: x\n"
        "      SECRET_KEY: y\n  worker:\n    image: shop/worker:1.4\n"
    )
    (tmp_path / "k8s.yaml").write_text(
        "kind: Deployment\nmetadata: {name: api}\nspec:\n  replicas: 3\n  template:\n    spec:\n      containers:\n"
        "        - name: api\n          image: shop/api:1.4\n          ports: [{containerPort: 8000}]\n"
    )
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "data" {}\nresource "aws_sqs_queue" "jobs" {}\n'
    )
    inspect = tmp_path / "inspect.json"
    inspect.write_text(
        json.dumps(
            [
                {
                    "Name": "/shop-api-1",
                    "Config": {
                        "Image": "shop/api:1.3",
                        "Env": ["DB_URL=secret-value"],
                        "ExposedPorts": {"8000/tcp": {}},
                        "Labels": {"com.docker.compose.service": "api"},
                    },
                    "State": {"Status": "running"},
                },
                {
                    "Name": "/redis",
                    "Config": {"Image": "redis:7", "Env": [], "Labels": {}},
                    "State": {"Status": "running"},
                },
            ]
        )
    )
    kubectl = tmp_path / "kubectl.json"
    kubectl.write_text(
        json.dumps(
            {
                "kind": "List",
                "items": [
                    {
                        "kind": "Deployment",
                        "metadata": {"name": "api"},
                        "spec": {
                            "replicas": 2,
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "api",
                                            "image": "shop/api:1.4",
                                            "ports": [{"containerPort": 8000}],
                                        }
                                    ]
                                }
                            },
                        },
                        "status": {"readyReplicas": 1},
                    }
                ],
            }
        )
    )
    state = tmp_path / "terraform.tfstate"
    state.write_text(
        json.dumps(
            {
                "version": 4,
                "resources": [
                    {"mode": "managed", "type": "aws_s3_bucket", "name": "data"},
                    {"mode": "managed", "type": "aws_iam_role", "name": "manual"},
                ],
            }
        )
    )
    report = compare(tmp_path, [inspect, kubectl, state])
    kinds = {(d["kind"], d["name"]) for d in report["drift"]}
    assert ("image differs", "api") in kinds
    assert ("environment variables missing", "api") in kinds
    assert ("declared, not running", "worker") in kinds
    assert ("running, not declared", "redis") in kinds
    assert ("replicas differ", "deployment/api") in kinds
    assert ("not all replicas ready", "deployment/api") in kinds
    assert ("in code, not in state", "aws_sqs_queue.jobs") in kinds
    assert ("in state, not in code", "aws_iam_role.manual") in kinds
    assert "secret-value" not in json.dumps(report)  # values are never read
    with pytest.raises(InsightError, match="isn't this machine"):
        compare(tmp_path, [], "http://example.com/openapi.json")


# ---------------------------------------------------------------- ADV10 patch loop


def test_patch_loop_in_a_worktree_with_approval(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.patchloop import commit_branch, evaluate

    (repo / "app" / "pricing.py").write_text(
        "def total(items):\n    return sum(items) if items else 0\n"
    )
    patch = tmp_path / "fix.patch"
    patch.write_text(_git(repo, "diff") + "\n")
    _git(repo, "checkout", "--", ".")
    report = evaluate(repo, patch, run_tests=True)
    assert report["applies"] and report["tests"]["status"] == "passed", report
    assert report["tests"]["command"][-1] == "tests/test_orders.py"
    assert report["ready"]
    assert (
        evaluate(repo, patch, run_tests=True)["approval"] == report["approval"]
    )  # timings don't change it
    assert _git(repo, "status", "--short") == ""  # the working tree wasn't touched
    with pytest.raises(InsightError, match="doesn't match"):
        commit_branch(repo, patch, report, "sha256:wrong", "fix/total", "Fix total")
    sha = commit_branch(repo, patch, report, report["approval"], "fix/total", "Fix total")
    assert _git(repo, "log", "-1", "--format=%H", "fix/total") == sha
    assert "if items else 0" in _git(repo, "show", "fix/total:app/pricing.py")
    assert _git(repo, "worktree", "list").count("\n") == 0  # the throwaway worktree is gone
    broken = tmp_path / "broken.patch"
    broken.write_text("--- a/app/nope.py\n+++ b/app/nope.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert evaluate(repo, broken)["applies"] is False


# ---------------------------------------------------------------- ADV11 mesh


def test_mesh_links_topics_and_datasets_across_repositories(tmp_path: Path) -> None:
    from ananke.plexus.insight.mesh import build

    a, b = tmp_path / "orders", tmp_path / "billing"
    for r in (a, b):
        r.mkdir()
    (a / "events.py").write_text(
        "def emit(producer, df):\n    producer.send('order-placed', b'x')\n"
        "    path = 'exports/orders.parquet'\n    df.to_parquet(path)\n"
        "    df.to_csv('s3://lake/raw/orders.csv')\n"
    )
    (b / "consume.py").write_text(
        "from kafka import KafkaConsumer\n\n\ndef run(pd):\n    KafkaConsumer('order-placed')\n"
        "    pd.read_parquet('exports/orders.parquet')\n    KafkaConsumer('refunds')\n"
        "    pd.read_csv('s3://lake/curated/refunds.csv')\n"
    )
    report = build(
        [
            {"path": str(a), "name": "orders", "pin": None},
            {"path": str(b), "name": "billing", "pin": None},
        ]
    )
    topics = {t["topic"]: t for t in report["topics"]}
    assert topics["order-placed"]["crosses_repositories"]
    datasets = {d["dataset"]: d for d in report["datasets"]}
    assert datasets["orders.parquet"]["crosses_repositories"]
    gaps = {(g["kind"], g["name"]) for g in report["gaps"]}
    assert ("subscribed, no publisher", "refunds") in gaps
    assert ("read, written by no repository", "s3://lake/curated") in gaps


# ---------------------------------------------------------------- ADV12 decisions


def test_decision_ranks_options_with_labelled_evidence(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.decide import assess, load

    spec = tmp_path / "d.yaml"
    spec.write_text(
        "title: Where totals live\ncriteria: {impact: 1, risk: 1, cost: 1}\noptions:\n"
        "  - name: Keep in pricing\n    touches: [app/pricing.py]\n    assumptions: [{metric: effort_days, value: 1, source: estimate}]\n"
        "  - name: Move to orders\n    touches: [app/orders.py, app/pricing.py]\n    adds_dependencies: [moneyed]\n"
        "    assumptions: [{metric: effort_days, value: 4, source: estimate}]\n"
    )
    report = assess(repo, load(spec))
    assert report["status"] == "proposed"
    assert report["ranking"][0] == "Keep in pricing"
    statuses = {e["status"] for o in report["options"] for e in o["evidence"]}
    assert {"simulated", "observed", "assumed (estimate)"} <= statuses
    one = tmp_path / "one.yaml"
    one.write_text("title: x\noptions:\n  - name: only\n")
    with pytest.raises(InsightError, match="at least two options"):
        load(one)


# ---------------------------------------------------------------- ADV14 incident


def test_incident_chain_from_a_jenkins_log(repo: Path, tmp_path: Path) -> None:
    from ananke.plexus.insight.incident import investigate

    good = _git(repo, "rev-parse", "HEAD")
    (repo / "app" / "orders.py").write_text(
        "from app.pricing import total\n\n\ndef place(items):\n    return total(items) + 1\n"
    )
    _git(repo, "commit", "-qam", "Add a fee")
    bad = _git(repo, "rev-parse", "HEAD")
    (repo / "docker-compose.yml").write_text("services:\n  backend:\n    build: .\n")
    log = tmp_path / "console.txt"
    log.write_text(
        f"Started by user admin\nChecking out Revision {bad} (origin/main)\n[Pipeline] {{ (Test)\n"
        "+ docker compose run --rm backend pytest -q\nContainer shop-backend-1  Started\n"
        "FAILED tests/test_orders.py::test_place - assert 4 == 3\nERROR: script returned exit code 1\n"
        "[Pipeline] }\nFinished: FAILURE\n"
    )
    report = investigate(log, repo, last_good=good)
    links = [c["link"] for c in report["chain"]]
    assert links[:3] == ["commit", "suspect commit", "stage"]
    assert "step" in links and "container" in links
    step = next(c for c in report["chain"] if c["link"] == "step")
    assert step["value"] == "docker compose run --rm backend pytest -q"
    assert report["suspects"][0]["subject"] == "Add a fee"
    assert report["suspects"][0]["status"].startswith("hypothesis")
    container = next(c for c in report["containers"] if c["kind"] == "container")
    assert container["service"] == "backend"


# ---------------------------------------------------------------- ADV01 twin


def test_twin_marks_unknown_and_stale_evidence(repo: Path) -> None:
    from ananke.plexus.insight.twin import build

    report = build(repo)
    assert report["components"]
    for t in report["components"]:
        assert t["runtime"]["status"].startswith("unknown")
        for part in ("source", "contract", "infrastructure", "verification"):
            assert t[part]["status"]


# ---------------------------------------------------------------- CLI wiring


def test_cli_commands_are_registered(repo: Path) -> None:
    for argv in (
        ["graph", "diff", "--help"],
        ["graph", "whatif", "--help"],
        ["graph", "twin", "--help"],
        ["triage", "test-gaps", "--help"],
        ["triage", "notebooks", "--help"],
        ["triage", "drift", "--help"],
        ["triage", "patch", "--help"],
        ["triage", "mesh", "--help"],
        ["triage", "incident", "--help"],
        ["arch", "decide", "--help"],
        ["context", "observe", "--help"],
        ["cve", "import", "--help"],
    ):
        assert runner.invoke(app, argv).exit_code == 0, argv
    done = runner.invoke(
        app, ["graph", "whatif", "--change", "app/pricing.py", "--project", str(repo), "--json"]
    )
    assert done.exit_code == 0, done.output
    assert json.loads(done.output)["tests_to_run"] == ["tests/test_orders.py"]
    bad = runner.invoke(app, ["graph", "diff", "--base", "nope", "--project", str(repo)])
    assert bad.exit_code == 2

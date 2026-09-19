"""Spec §146 analytics questions: portable SQL that gives identical answers on SQLite and DuckDB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from registry_support import add, artifact, open_registry
from typer.testing import CliRunner

from ananke.plexus.registry.cli import registry_app
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import Quality
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.reports import QUESTIONS, answer_question


@pytest.fixture
def reg(tmp_path: Path) -> Registry:
    r = open_registry(tmp_path)
    add(
        r, "graph-review", "1.0.0", approve=True, runtime={"supported": ["pydantic", "generic-mcp"]}
    )
    add(r, "old-skill", "1.0.0", approve=True, runtime={"supported": ["pydantic"]})
    add(r, "net-skill", "1.0.0", permissions={"network": ["https://api.example.com/*"]})
    r.register(
        artifact(
            "agent-a",
            "1.0.0",
            kind="agent",
            skills=[{"ref": "core/old-skill", "version": "^1"}],
        )
    )
    r.register(
        artifact(
            "pack",
            "1.0.0",
            kind="bundle",
            members=[{"ref": "core/graph-review", "version": "^1"}],
        )
    )
    r.deprecate("core/old-skill@1.0.0", "superseded")
    r.update_quality(
        "core/graph-review@1.0.0",
        Quality(tests_status="pass", last_verified_at="2099-01-01T00:00:00+00:00"),
    )
    return r


def test_every_question_runs_on_sqlite(reg: Registry) -> None:
    for name in QUESTIONS:
        res = answer_question(reg, name, engine="sqlite")
        assert res.engine == "sqlite" and res.columns and res.name == name


def test_answers(reg: Registry) -> None:
    per_runtime = dict(
        map(tuple, answer_question(reg, "approved-skills-per-runtime", engine="sqlite").rows)
    )
    assert per_runtime == {"pydantic": "1", "generic-mcp": "1"} or per_runtime == {
        "pydantic": 1,
        "generic-mcp": 1,
    }

    dep = answer_question(reg, "agents-on-deprecated", engine="sqlite").rows
    assert [(r[0], r[1], r[3]) for r in dep] == [
        ("ananke://agent/core/agent-a@1.0.0", "ananke://skill/core/old-skill", "deprecated")
    ]

    net = answer_question(reg, "network-skills", engine="sqlite").rows
    assert net == [["ananke://skill/core/net-skill@1.0.0", "https://api.example.com/*"]]

    stale = {r[0] for r in answer_question(reg, "not-verified-recently", engine="sqlite").rows}
    assert "ananke://skill/core/graph-review@1.0.0" not in stale  # verified far in the future
    assert "ananke://skill/core/net-skill@1.0.0" in stale  # never verified

    lic = answer_question(reg, "bundle-licenses", engine="sqlite").rows
    assert lic == [["ananke://bundle/core/pack@1.0.0", "Apache-2.0"]]


def test_locked_versions(reg: Registry) -> None:
    rec = reg.exact_version("core/graph-review@1.0.0")
    reg.store.record_lock(
        "proj/ananke.lock",
        "abc123",
        "snap",
        [rec.digest_sha256],
        "tester",
        "2026-01-01T00:00:00+00:00",
    )
    rows = answer_question(reg, "locked-versions", engine="sqlite").rows
    assert rows == [
        ["ananke://skill/core/graph-review@1.0.0", "proj/ananke.lock", "2026-01-01T00:00:00+00:00"]
    ]


def test_unknown_question_and_bad_arguments(reg: Registry) -> None:
    with pytest.raises(RegistryError) as exc:
        answer_question(reg, "nope")
    assert exc.value.code == "UNKNOWN_QUESTION"
    with pytest.raises(RegistryError):
        answer_question(reg, "network-skills", engine="mysql")
    with pytest.raises(RegistryError):
        answer_question(reg, "not-verified-recently", days=-1)


def test_sql_is_fixed_text_never_built_from_input(reg: Registry) -> None:
    # questions take no free-text parameters other than the computed cutoff
    assert all(q.params in ([], ["cutoff"]) for q in QUESTIONS.values())
    add(reg, "quote-skill", "1.0.0", summary="x'); DROP TABLE registry_versions;--")
    assert answer_question(reg, "network-skills", engine="sqlite").rows  # metadata is data, not SQL
    assert reg.store.count_versions() == 6


def test_duckdb_and_sqlite_agree(reg: Registry) -> None:
    pytest.importorskip("duckdb")
    for name in QUESTIONS:
        a = answer_question(reg, name, engine="sqlite", days=30)
        b = answer_question(reg, name, engine="duckdb", days=30)
        assert b.engine == "duckdb"
        norm = lambda rows: sorted(tuple("" if v is None else str(v) for v in r) for r in rows)  # noqa: E731
        assert a.columns == b.columns and norm(a.rows) == norm(b.rows), name


def test_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    proj = ["--project", str(tmp_path)]
    assert runner.invoke(registry_app, ["init", *proj]).exit_code == 0
    listed = runner.invoke(registry_app, ["analytics", "query"])
    assert listed.exit_code == 0 and "network-skills" in listed.output
    empty = runner.invoke(
        registry_app, ["analytics", "query", "network-skills", "--engine", "sqlite", *proj]
    )
    assert empty.exit_code == 0 and "no rows" in empty.output
    out = runner.invoke(
        registry_app,
        ["analytics", "query", "locked-versions", "--json", "--engine", "sqlite", *proj],
    )
    assert json.loads(out.output)["rows"] == []
    bad = runner.invoke(registry_app, ["analytics", "query", "nope", *proj])
    assert bad.exit_code == 1 and "UNKNOWN_QUESTION" in bad.output + (bad.stderr or "")

"""CAP-quality-cve-02..04: one finding schema, tied to the change, ranked, with the gates to rerun. Nothing invented."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.intel.priorities import normalize, prioritize, to_markdown

SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "Semgrep",
                    "rules": [{"id": "sql-injection", "help": {"text": "use a bound parameter"}}],
                }
            },
            "results": [
                {
                    "ruleId": "sql-injection",
                    "level": "error",
                    "message": {"text": "SQL built from a string"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "pkg/b.py"},
                                "region": {"startLine": 3},
                            }
                        }
                    ],
                },
                {
                    "ruleId": "sql-injection",
                    "level": "note",
                    "message": {"text": "minor"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "pkg/c.py"},
                                "region": {"startLine": 1},
                            }
                        }
                    ],
                },
            ],
        }
    ],
}
JUNIT = '<testsuite tests="2"><testcase classname="tests.test_b" name="test_q"><failure message="boom"/></testcase><testcase classname="tests.test_b" name="test_ok"/></testsuite>'
COVERAGE = """<?xml version="1.0" ?>
<coverage line-rate="0.5" branch-rate="0" version="7"><packages><package name="pkg" line-rate="0.5"><classes>
<class name="b.py" filename="pkg/b.py" line-rate="0.25"><lines><line number="1" hits="1"/><line number="2" hits="0"/><line number="3" hits="0"/><line number="4" hits="0"/></lines></class>
</classes></package></packages></coverage>
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "a.py").write_text("from pkg import b\n\n\ndef run():\n    return b.q()\n")
    (root / "pkg" / "b.py").write_text("import yaml\n\n\ndef q():\n    return 'x'\n")
    (root / "pkg" / "c.py").write_text("X = 1\n")
    (root / "semgrep.sarif").write_text(json.dumps(SARIF))
    (root / "junit.xml").write_text(JUNIT)
    (root / "coverage.xml").write_text(COVERAGE)
    git = ["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    return root


def _cli(repo: Path, *extra: str) -> dict:
    res = CliRunner().invoke(app, ["triage", "findings", "--project", str(repo), "--json", *extra])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_findings_are_ranked_against_the_change(repo: Path) -> None:
    out = _cli(repo, "--changed", "pkg/b.py")
    by_kind = {}
    for f in out["findings"]:
        by_kind.setdefault(f["kind"], []).append(f)
    top = out["findings"][0]
    assert (
        top["kind"] == "code"
        and top["path"] == "pkg/b.py"
        and top["changed"]
        and top["tier"] == "fix now"
    )
    assert "use a bound parameter" in top["suggestion"] and top["line"] == 3
    minor = next(f for f in by_kind["code"] if f["path"] == "pkg/c.py")
    assert not minor["changed"] and not minor["impacted"] and minor["tier"] == "later"
    assert by_kind["test"][0]["tier"] == "fix now" and "boom" in by_kind["test"][0]["message"]
    (cov,) = by_kind["coverage"]
    assert cov["path"] == "pkg/b.py" and "25%" in cov["message"]
    assert out["impacted"] == ["pkg/a.py"]  # imports the changed file
    assert {"gate": "semgrep", "command": "semgrep scan --config auto pkg/b.py"} in out["rerun"]
    assert any(g["gate"] == "tests" for g in out["rerun"])


def test_the_same_reports_give_the_same_output(repo: Path) -> None:
    a, b = _cli(repo, "--changed", "pkg/b.py"), _cli(repo, "--changed", "pkg/b.py")
    assert a["digest"] == b["digest"] and [f["id"] for f in a["findings"]] == [
        f["id"] for f in b["findings"]
    ]


def test_vulnerable_dependencies_name_package_version_advisory_and_who_imports_them() -> None:
    report = {
        "sections": {
            "dependencies": {
                "vulnerabilities": [
                    {
                        "ecosystem": "PyPI",
                        "package": "PyYAML",
                        "version": "5.3",
                        "id": "GHSA-x",
                        "aliases": ["CVE-2020-1"],
                        "severity": "critical",
                        "fixed_in": "5.4",
                        "summary": "arbitrary code",
                    },
                    {
                        "ecosystem": "PyPI",
                        "package": "leftpad",
                        "version": "1.0",
                        "id": "PYSEC-y",
                        "aliases": [],
                        "severity": "high",
                        "fixed_in": None,
                        "summary": "bad",
                    },
                ]
            }
        }
    }
    graph = {
        "edges": [
            {
                "kind": "imports",
                "source": "file:pkg/b.py",
                "target": "module:yaml",
                "resolved_target": None,
            }
        ]
    }
    out = prioritize(report, graph, ["pkg/b.py"])
    yaml_, left = sorted(out["findings"], key=lambda f: f["package"] != "PyYAML")
    assert yaml_["evidence"] == "PyPI:PyYAML@5.3 GHSA-x" and yaml_["importers"] == ["pkg/b.py"]
    assert yaml_["reachable"] and yaml_["changed"] and yaml_["tier"] == "fix now"
    assert yaml_["suggestion"].startswith("upgrade PyYAML from 5.3 to 5.4 or later")
    assert (
        not left["reachable"] and "no fixed version" in left["suggestion"]
    )  # never an invented fix version
    assert left["score"] < yaml_["score"]


def test_no_reports_means_no_findings_and_says_so(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("X = 1\n")
    out = _cli(tmp_path)
    assert out["findings"] == [] and any("unknown, not zero" in x for x in out["limits"])
    assert "What to fix first" in to_markdown(out)
    assert normalize({"sections": {}}) == []

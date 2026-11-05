"""Scanner and test reports already in a repository: read, never run, and unknown isn't zero."""

from __future__ import annotations

import json
from pathlib import Path

from ananke.plexus.intel import analyze


def _sarif(root: Path, name: str = "scan.sarif") -> None:
    (root / name).write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "tool": {
                            "driver": {
                                "name": "CodeQL",
                                "rules": [
                                    {
                                        "id": "py/x",
                                        "properties": {"security-severity": "9.1"},
                                        "help": {"text": "Use a parameter."},
                                    },
                                    {"id": "py/y", "defaultConfiguration": {"level": "note"}},
                                ],
                            }
                        },
                        "results": [
                            {
                                "ruleId": "py/y",
                                "message": {"text": "minor"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "b.py"},
                                            "region": {"startLine": 2},
                                        }
                                    }
                                ],
                            },
                            {
                                "ruleId": "py/x",
                                "message": {"text": "SQL injection"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "a.py"},
                                            "region": {"startLine": 9},
                                        }
                                    }
                                ],
                            },
                        ],
                    }
                ]
            }
        )
    )


JUNIT = """<testsuite tests="3"><testcase classname="t" name="ok"/><testcase classname="t" name="bad"><failure message="boom"/></testcase><testcase classname="t" name="later"><skipped/></testcase></testsuite>"""


def test_no_report_is_unknown_not_clean(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    section = analyze(tmp_path)["sections"]["findings"]
    assert section["status"] == "not_found" and section["findings"] == []
    assert any("unknown" in note for note in section["limits"])


def test_sarif_is_read_worst_first_with_its_source_and_fix(tmp_path: Path) -> None:
    _sarif(tmp_path)
    section = analyze(tmp_path)["sections"]["findings"]
    first = section["findings"][0]
    assert (first["severity"], first["path"], first["line"], first["tool"]) == (
        "critical",
        "a.py",
        9,
        "CodeQL",
    )
    assert first["remediation"] == "Use a parameter."
    assert section["findings"][1]["severity"] == "low"


def test_test_results_name_what_failed(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text(JUNIT)
    result = analyze(tmp_path)["sections"]["findings"]["tests"][0]
    assert (result["total"], result["passed"], result["failed"], result["skipped"]) == (3, 1, 1, 1)
    assert result["failures"][0]["name"] == "t.bad" and result["failures"][0]["message"] == "boom"


def test_broken_and_hostile_reports_are_named_and_dont_stop_it(tmp_path: Path) -> None:
    (tmp_path / "a.sarif").write_text("{not json")
    (tmp_path / "b.sarif").write_text('{"runs": 7}')
    (tmp_path / "junit.xml").write_text('<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>')
    (tmp_path / "test-results.xml").write_text("not xml")
    report = analyze(tmp_path)
    section = report["sections"]["findings"]
    assert section["status"] != "failed"
    text = " ".join(section["limits"])
    assert "a.sarif" in text and "DOCTYPE" in text and "test-results.xml" in text


def test_an_ordinary_xml_file_isnt_taken_for_test_results(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    assert analyze(tmp_path)["sections"]["findings"]["tests"] == []

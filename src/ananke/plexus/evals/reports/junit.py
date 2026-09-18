"""JUnit XML report generator — CI integration."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ananke.plexus.evals.models.report import EvalReport
from ananke.plexus.evals.models.score import EvalStatus


def generate_junit_xml(report: EvalReport) -> str:
    suite = ET.Element("testsuite")
    suite.set("name", f"ananke.eval.{report.suite_id}")
    suite.set("id", report.run_id)
    suite.set("tests", str(len(report.scores)))

    failures = sum(1 for s in report.scores if s.status in (EvalStatus.FAIL, EvalStatus.ERROR))
    skipped = sum(1 for s in report.scores if s.status == EvalStatus.SKIPPED)
    suite.set("failures", str(failures))
    suite.set("skipped", str(skipped))

    for score in report.scores:
        tc = ET.SubElement(suite, "testcase")
        tc.set("name", f"{score.dimension}.{score.metric}")
        tc.set("classname", f"ananke.eval.{report.suite_id}")

        if score.status == EvalStatus.FAIL:
            fail = ET.SubElement(tc, "failure")
            fail.set("type", "EvalFailure")
            fail.text = score.reason or f"score={score.value}"
        elif score.status == EvalStatus.ERROR:
            err = ET.SubElement(tc, "error")
            err.set("type", "EvalError")
            err.text = score.reason or "evaluation error"
        elif score.status == EvalStatus.SKIPPED:
            ET.SubElement(tc, "skipped").text = score.reason or ""

    ET.indent(suite, space="  ")
    return ET.tostring(suite, encoding="unicode", xml_declaration=False)


def write_junit_report(report: EvalReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_junit_xml(report), encoding="utf-8")
    return output_path

"""generate_junit_xml — JUnit XML output for a TestRun."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC

from ananke.plexus.testing.models.result import TestRun
from ananke.plexus.testing.models.test import TestStatus


def generate_junit_xml(run: TestRun) -> str:
    """Return JUnit XML representing a TestRun."""
    failures = sum(1 for r in run.results if r.status == TestStatus.FAIL)
    errors = sum(1 for r in run.results if r.status == TestStatus.ERROR)
    skipped = sum(1 for r in run.results if r.status == TestStatus.SKIPPED)
    total_time = sum((r.duration_ms or 0.0) / 1000.0 for r in run.results)

    started_iso = run.started_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    suite = ET.Element(
        "testsuite",
        name=f"quality-{run.suite_id}",
        tests=str(len(run.results)),
        failures=str(failures),
        errors=str(errors),
        skipped=str(skipped),
        time=f"{total_time:.3f}",
        timestamp=started_iso,
    )

    for r in run.results:
        tc = ET.SubElement(
            suite,
            "testcase",
            name=r.test_id,
            classname=f"{r.engine}.{r.kind}",
            time=f"{(r.duration_ms or 0.0) / 1000.0:.3f}",
        )
        if r.status == TestStatus.FAIL:
            failure = ET.SubElement(tc, "failure", message=r.message or "test failed")
            failure.text = r.message or ""
        elif r.status == TestStatus.ERROR:
            error = ET.SubElement(tc, "error", message=r.message or "test error")
            error.text = r.message or ""
        elif r.status in (TestStatus.SKIPPED, TestStatus.UNAVAILABLE):
            ET.SubElement(tc, "skipped")

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    import io

    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(buf, encoding="unicode", xml_declaration=False)
    return buf.getvalue()

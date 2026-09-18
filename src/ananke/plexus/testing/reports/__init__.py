"""Testing reports package."""

from __future__ import annotations

from ananke.plexus.testing.reports.console import generate_console_report
from ananke.plexus.testing.reports.json import generate_json_report
from ananke.plexus.testing.reports.junit import generate_junit_xml
from ananke.plexus.testing.reports.markdown import generate_markdown_report
from ananke.plexus.testing.reports.sarif import generate_sarif_report

__all__ = [
    "generate_console_report",
    "generate_json_report",
    "generate_junit_xml",
    "generate_markdown_report",
    "generate_sarif_report",
]

"""save_test_evidence — persist a TestRun to the Ananke evidence store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ananke.plexus.testing.models.result import TestRun


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_test_evidence(run: TestRun, evidence_root: Path) -> Path:
    """Persist a TestRun to the Ananke evidence store.

    Writes the following files under
    ``<evidence_root>/<run_id>/quality/``:

    * ``results.json``     — full TestRun JSON
    * ``manifest.json``    — checksums of all artefacts
    * ``reports/summary.md`` — Markdown summary
    * ``reports/junit.xml``  — JUnit XML

    Returns the quality directory path.
    """
    from ananke.plexus.testing.reports.junit import generate_junit_xml
    from ananke.plexus.testing.reports.markdown import generate_markdown_report

    quality_dir = evidence_root / run.run_id / "quality"
    reports_dir = quality_dir / "reports"
    quality_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # results.json
    results_json = run.model_dump_json(indent=2)
    results_path = quality_dir / "results.json"
    results_path.write_text(results_json, encoding="utf-8")

    # reports/summary.md
    summary_md = generate_markdown_report(run)
    summary_path = reports_dir / "summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")

    # reports/junit.xml
    junit_xml = generate_junit_xml(run)
    junit_path = reports_dir / "junit.xml"
    junit_path.write_text(junit_xml, encoding="utf-8")

    # manifest.json
    manifest = {
        "run_id": run.run_id,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "artifacts": {
            "results.json": {
                "path": str(results_path),
                "sha256": _sha256_of_text(results_json),
            },
            "reports/summary.md": {
                "path": str(summary_path),
                "sha256": _sha256_of_text(summary_md),
            },
            "reports/junit.xml": {
                "path": str(junit_path),
                "sha256": _sha256_of_text(junit_xml),
            },
        },
    }
    manifest_json = json.dumps(manifest, indent=2)
    manifest_path = quality_dir / "manifest.json"
    manifest_path.write_text(manifest_json, encoding="utf-8")

    return quality_dir

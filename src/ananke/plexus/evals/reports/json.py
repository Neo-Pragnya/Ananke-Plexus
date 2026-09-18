"""JSON report generator."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.evals.models.report import EvalReport


def generate_json_report(report: EvalReport) -> str:
    return report.model_dump_json(indent=2)


def write_json_report(report: EvalReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_json_report(report), encoding="utf-8")
    return output_path

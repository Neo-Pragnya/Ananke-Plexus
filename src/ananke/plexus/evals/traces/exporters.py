"""Trace exporters — serialize canonical AgentTrace to JSON."""

from __future__ import annotations

from pathlib import Path

from ananke.plexus.evals.models.trace import AgentTrace


def export_trace_to_json(trace: AgentTrace, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        trace.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return output_path


def trace_to_dict(trace: AgentTrace) -> dict[str, object]:
    return trace.model_dump(mode="json")

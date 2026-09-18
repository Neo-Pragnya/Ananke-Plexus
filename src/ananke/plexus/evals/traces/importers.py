"""Trace importers — load canonical AgentTrace from JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from ananke.plexus.evals.models.trace import AgentTrace
from ananke.plexus.evals.traces.normalize import normalize_trace


def load_trace_from_file(path: Path, runtime: str = "generic") -> AgentTrace:
    """Load a trace from a JSON file, normalizing to canonical AgentTrace."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"spans": data, "trace_id": path.stem}
    return normalize_trace(data, runtime=runtime)


def load_trace_from_ananke_evidence(evidence_dir: Path, run_id: str) -> AgentTrace | None:
    """Attempt to load a trace stored under .ananke/evidence/<run_id>/trace.json."""
    trace_file = evidence_dir / run_id / "trace.json"
    if not trace_file.exists():
        return None
    return load_trace_from_file(trace_file, runtime="ananke")

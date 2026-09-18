"""Tests for trace normalization, importers, and exporters in the eval harness."""

from __future__ import annotations

import json

from ananke.plexus.evals.models.trace import AgentTrace, SpanKind, Usage
from ananke.plexus.evals.traces.exporters import export_trace_to_json, trace_to_dict
from ananke.plexus.evals.traces.importers import load_trace_from_file
from ananke.plexus.evals.traces.normalize import (
    build_trace_from_run_id,
    normalize_otel_span,
    normalize_trace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_trace() -> AgentTrace:
    return AgentTrace(
        trace_id="trace-001",
        run_id="run-001",
        runtime="test",
        spans=[],
        usage=Usage(total_tokens=100),
        final_output="hello",
    )


# ---------------------------------------------------------------------------
# build_trace_from_run_id
# ---------------------------------------------------------------------------


class TestBuildTraceFromRunId:
    def test_returns_agent_trace(self):
        trace = build_trace_from_run_id("run-001")
        assert isinstance(trace, AgentTrace)

    def test_run_id_matches(self):
        trace = build_trace_from_run_id("run-001")
        assert trace.run_id == "run-001"

    def test_trace_id_matches_run_id(self):
        trace = build_trace_from_run_id("run-abc")
        assert trace.trace_id == "run-abc"

    def test_default_runtime(self):
        trace = build_trace_from_run_id("r1")
        assert trace.runtime == "ananke"

    def test_custom_runtime(self):
        trace = build_trace_from_run_id("r1", runtime="custom-runtime")
        assert trace.runtime == "custom-runtime"

    def test_output_set(self):
        trace = build_trace_from_run_id("r1", output="my output")
        assert trace.final_output == "my output"

    def test_no_spans(self):
        trace = build_trace_from_run_id("r1")
        assert trace.spans == []


# ---------------------------------------------------------------------------
# normalize_otel_span
# ---------------------------------------------------------------------------


class TestNormalizeOtelSpan:
    def test_basic_fields(self):
        raw = {
            "span_id": "s1",
            "kind": "tool",
            "name": "search",
            "start_time": "2024-01-01T00:00:00+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.span_id == "s1"
        assert span.kind == SpanKind.TOOL
        assert span.name == "search"

    def test_unknown_kind_defaults_to_agent(self):
        raw = {
            "span_id": "s1",
            "kind": "unknown_kind",
            "name": "x",
            "start_time": "2024-01-01T00:00:00+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.kind == SpanKind.AGENT

    def test_parent_span_id_set(self):
        raw = {
            "span_id": "child",
            "parent_span_id": "parent",
            "kind": "tool",
            "name": "x",
            "start_time": "2024-01-01T00:00:00+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.parent_span_id == "parent"

    def test_no_parent_span_id(self):
        raw = {
            "span_id": "s1",
            "kind": "agent",
            "name": "x",
            "start_time": "2024-01-01T00:00:00+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.parent_span_id is None

    def test_end_time_parsed(self):
        raw = {
            "span_id": "s1",
            "kind": "tool",
            "name": "x",
            "start_time": "2024-01-01T00:00:00+00:00",
            "end_time": "2024-01-01T00:00:01+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.ended_at is not None

    def test_attributes_from_dict(self):
        raw = {
            "span_id": "s1",
            "kind": "tool",
            "name": "x",
            "start_time": "2024-01-01T00:00:00+00:00",
            "attributes": {"key": "val"},
        }
        span = normalize_otel_span(raw)
        assert span.attributes == {"key": "val"}

    def test_alternate_field_names(self):
        raw = {
            "id": "alt-id",
            "kind": "model",
            "operation_name": "llm-call",
            "started_at": "2024-01-01T00:00:00+00:00",
        }
        span = normalize_otel_span(raw)
        assert span.span_id == "alt-id"
        assert span.name == "llm-call"


# ---------------------------------------------------------------------------
# normalize_trace
# ---------------------------------------------------------------------------


class TestNormalizeTrace:
    def test_basic_normalization(self):
        raw = {
            "trace_id": "t1",
            "run_id": "r1",
            "runtime": "test",
            "spans": [],
        }
        trace = normalize_trace(raw)
        assert trace.trace_id == "t1"
        assert trace.run_id == "r1"

    def test_usage_extracted(self):
        raw = {
            "trace_id": "t1",
            "usage": {"total_tokens": 150, "duration_ms": 500},
        }
        trace = normalize_trace(raw)
        assert trace.usage.total_tokens == 150
        assert trace.usage.duration_ms == 500

    def test_spans_normalized(self):
        raw = {
            "trace_id": "t1",
            "spans": [
                {
                    "span_id": "s1",
                    "kind": "tool",
                    "name": "search",
                    "start_time": "2024-01-01T00:00:00+00:00",
                }
            ],
        }
        trace = normalize_trace(raw)
        assert len(trace.spans) == 1
        assert trace.spans[0].name == "search"

    def test_final_output_extracted(self):
        raw = {"trace_id": "t1", "final_output": "answer"}
        trace = normalize_trace(raw)
        assert trace.final_output == "answer"

    def test_model_extracted(self):
        raw = {"trace_id": "t1", "model": "claude-3"}
        trace = normalize_trace(raw)
        assert trace.model == "claude-3"

    def test_fallback_runtime(self):
        raw = {"trace_id": "t1"}
        trace = normalize_trace(raw, runtime="fallback-runtime")
        assert trace.runtime == "fallback-runtime"

    def test_unknown_trace_id_fallback(self):
        trace = normalize_trace({})
        assert trace.trace_id == "unknown"


# ---------------------------------------------------------------------------
# Export and import (round-trip)
# ---------------------------------------------------------------------------


class TestExportAndImportTrace:
    def test_export_creates_file(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "trace.json"
        result = export_trace_to_json(trace, out)
        assert result == out
        assert out.exists()

    def test_exported_file_is_valid_json(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "trace.json"
        export_trace_to_json(trace, out)
        data = json.loads(out.read_text())
        assert isinstance(data, dict)

    def test_export_contains_trace_id(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "trace.json"
        export_trace_to_json(trace, out)
        data = json.loads(out.read_text())
        assert data["trace_id"] == "trace-001"

    def test_round_trip_trace_id(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "trace.json"
        export_trace_to_json(trace, out)
        loaded = load_trace_from_file(out)
        assert loaded.trace_id == "trace-001"

    def test_round_trip_run_id(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "trace.json"
        export_trace_to_json(trace, out)
        loaded = load_trace_from_file(out)
        assert loaded.run_id == "run-001"

    def test_export_creates_parent_dirs(self, tmp_path):
        trace = _minimal_trace()
        out = tmp_path / "subdir" / "nested" / "trace.json"
        export_trace_to_json(trace, out)
        assert out.exists()

    def test_load_trace_from_json_list(self, tmp_path):
        spans = [
            {
                "span_id": "s1",
                "kind": "tool",
                "name": "search",
                "started_at": "2024-01-01T00:00:00+00:00",
            }
        ]
        json_file = tmp_path / "spans.json"
        json_file.write_text(json.dumps(spans))
        trace = load_trace_from_file(json_file)
        assert len(trace.spans) == 1

    def test_load_trace_from_dict_json(self, tmp_path):
        data = {"trace_id": "t99", "run_id": "r99", "spans": []}
        json_file = tmp_path / "trace.json"
        json_file.write_text(json.dumps(data))
        trace = load_trace_from_file(json_file)
        assert trace.trace_id == "t99"


# ---------------------------------------------------------------------------
# trace_to_dict
# ---------------------------------------------------------------------------


class TestTraceToDict:
    def test_returns_dict(self):
        trace = _minimal_trace()
        d = trace_to_dict(trace)
        assert isinstance(d, dict)

    def test_contains_trace_id(self):
        trace = _minimal_trace()
        d = trace_to_dict(trace)
        assert d["trace_id"] == "trace-001"

    def test_json_serializable(self):
        trace = _minimal_trace()
        d = trace_to_dict(trace)
        # Should not raise
        json.dumps(d)

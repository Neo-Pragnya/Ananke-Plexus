"""Runtime-neutral trace normalizer — maps framework events to canonical AgentTrace."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage


def normalize_otel_span(raw: dict[str, Any]) -> AgentSpan:
    """Convert an OpenTelemetry span dict to canonical AgentSpan."""
    kind_str = raw.get("kind", "agent").lower()
    try:
        kind = SpanKind(kind_str)
    except ValueError:
        kind = SpanKind.AGENT

    started = raw.get("start_time") or raw.get("started_at")
    ended = raw.get("end_time") or raw.get("ended_at")

    def _parse_ts(v: Any) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except (ValueError, TypeError):
            return datetime.now(tz=UTC)

    return AgentSpan(
        span_id=str(raw.get("span_id") or raw.get("id") or "unknown"),
        parent_span_id=str(raw["parent_span_id"]) if raw.get("parent_span_id") else None,
        kind=kind,
        name=str(raw.get("name") or raw.get("operation_name") or kind_str),
        started_at=_parse_ts(started) or datetime.now(tz=UTC),
        ended_at=_parse_ts(ended),
        input=raw.get("input") or raw.get("attributes", {}).get("input"),
        output=raw.get("output") or raw.get("attributes", {}).get("output"),
        attributes=raw.get("attributes") or raw.get("resource_attributes") or {},
        events=raw.get("events") or [],
    )


def normalize_trace(
    raw: dict[str, Any],
    runtime: str = "generic",
) -> AgentTrace:
    """Normalize a raw trace dict (any runtime) to canonical AgentTrace."""
    spans_raw = raw.get("spans") or []
    spans = [normalize_otel_span(s) for s in spans_raw]

    usage_raw = raw.get("usage") or {}
    usage = Usage(
        prompt_tokens=usage_raw.get("prompt_tokens"),
        completion_tokens=usage_raw.get("completion_tokens"),
        total_tokens=usage_raw.get("total_tokens"),
        cost_usd=usage_raw.get("cost_usd"),
        duration_ms=usage_raw.get("duration_ms"),
        tool_calls=usage_raw.get("tool_calls", 0),
        retries=usage_raw.get("retries", 0),
    )

    return AgentTrace(
        trace_id=str(raw.get("trace_id") or raw.get("id") or "unknown"),
        run_id=str(raw.get("run_id") or raw.get("trace_id") or "unknown"),
        runtime=raw.get("runtime") or runtime,
        model=raw.get("model"),
        spans=spans,
        final_output=raw.get("final_output") or raw.get("output"),
        usage=usage,
        spec_hash=raw.get("spec_hash"),
        architecture_hash=raw.get("architecture_hash"),
        policy_hash=raw.get("policy_hash"),
        dataset_case_id=raw.get("dataset_case_id"),
    )


def _make_minimal_span(
    name: str, kind: SpanKind, input_: Any = None, output: Any = None
) -> AgentSpan:
    return AgentSpan(
        span_id=name,
        kind=kind,
        name=name,
        started_at=datetime.now(tz=UTC),
        input=input_,
        output=output,
    )


def build_trace_from_run_id(run_id: str, output: Any = None, runtime: str = "ananke") -> AgentTrace:
    """Build a minimal canonical trace from an Ananke run_id."""
    return AgentTrace(
        trace_id=run_id,
        run_id=run_id,
        runtime=runtime,
        final_output=output,
    )

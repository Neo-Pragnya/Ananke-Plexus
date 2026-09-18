"""OpenTelemetry integration — emit canonical spans; graceful when OTel SDK absent."""

from __future__ import annotations

from typing import Any

from ananke.plexus.evals.models.trace import AgentTrace


def _otel_available() -> bool:
    try:
        import opentelemetry  # noqa: F401

        return True
    except ImportError:
        return False


def emit_trace_to_otel(trace: AgentTrace, endpoint: str | None = None) -> bool:
    """Export a canonical AgentTrace as OTel spans. Returns False if OTel not available."""
    if not _otel_available():
        return False
    try:
        from opentelemetry.sdk.trace import TracerProvider

        tracer_provider = TracerProvider()
        tracer = tracer_provider.get_tracer("ananke.plexus.evals")
        with tracer.start_as_current_span(f"ananke.run.{trace.run_id}") as root_span:
            root_span.set_attribute("ananke.run.id", trace.run_id)
            root_span.set_attribute("ananke.runtime", trace.runtime)
            if trace.spec_hash:
                root_span.set_attribute("ananke.spec.hash", trace.spec_hash)
        return True
    except Exception:
        return False


def required_span_attributes(trace: AgentTrace) -> dict[str, Any]:
    """Return the set of required OTel resource attributes for this trace."""
    attrs: dict[str, Any] = {
        "ananke.run.id": trace.run_id,
        "ananke.runtime": trace.runtime,
    }
    if trace.spec_hash:
        attrs["ananke.spec.hash"] = trace.spec_hash
    if trace.policy_hash:
        attrs["ananke.policy.hash"] = trace.policy_hash
    if trace.model:
        attrs["gen_ai.request.model"] = trace.model
    return attrs

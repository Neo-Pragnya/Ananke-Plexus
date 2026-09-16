"""Miscellaneous coverage tests for small/low-coverage modules."""

from ananke.plexus.events.bus import EventBus
from ananke.plexus.events.models import SPEC_CREATED, Event
from ananke.plexus.events.subscribers import console_subscriber, register_console_subscriber
from ananke.plexus.hooks.runner import dispatch
from ananke.plexus.mcp.prompts import get_prompt, list_prompts

# ---------- event subscribers ----------


def test_console_subscriber_does_not_raise(capsys):
    ev = Event(event_type=SPEC_CREATED, payload={})
    console_subscriber(ev)
    captured = capsys.readouterr()
    assert SPEC_CREATED in captured.err


def test_register_console_subscriber():
    bus = EventBus()
    register_console_subscriber(bus)
    # Verify subscriber is registered (wildcard)
    assert len(bus._handlers.get("*", [])) == 1


# ---------- hooks runner dispatch ----------


def test_dispatch_pre_commit(tmp_path):
    result = dispatch(tmp_path, "pre-commit")
    assert result.stage == "pre-commit"
    assert isinstance(result.ok, bool)


def test_dispatch_pre_push(tmp_path):
    result = dispatch(tmp_path, "pre-push")
    assert result.stage == "pre-push"


def test_dispatch_verbose(tmp_path, capsys):
    dispatch(tmp_path, "pre-commit", verbose=True)
    # verbose may output to stderr — should not raise


# ---------- MCP prompts ----------


def test_list_prompts():
    prompts = list_prompts()
    assert "architecture-aware-implementation" in prompts
    assert "blast-radius-review" in prompts


def test_get_prompt_known():
    p = get_prompt("pr-evidence-summary")
    assert len(p) > 0


def test_get_prompt_unknown():
    p = get_prompt("does-not-exist")
    assert "not found" in p


# ---------- graph providers registry fallback ----------


def test_resolve_graphifyy_falls_back_to_native():
    from ananke.plexus.graph.providers.native import NativeGraphProvider
    from ananke.plexus.graph.providers.registry import resolve_graph_provider

    p = resolve_graph_provider("graphifyy")
    assert isinstance(p, NativeGraphProvider)


def test_resolve_crg_falls_back_to_native():
    from ananke.plexus.graph.providers.native import NativeGraphProvider
    from ananke.plexus.graph.providers.registry import resolve_graph_provider

    p = resolve_graph_provider("code-review-graph")
    assert isinstance(p, NativeGraphProvider)

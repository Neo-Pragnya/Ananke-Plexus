"""Tests for the evaluator registry in the eval harness."""

from __future__ import annotations

from ananke.plexus.evals.evaluators.outcome.exact import ExactMatchEvaluator
from ananke.plexus.evals.evaluators.registry import (
    EvaluatorMetadata,
    EvaluatorRegistry,
    get_default_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metadata(eid: str = "test.ev", dimension: str = "outcome") -> EvaluatorMetadata:
    return EvaluatorMetadata(id=eid, dimension=dimension, description="test evaluator")


# ---------------------------------------------------------------------------
# EvaluatorRegistry
# ---------------------------------------------------------------------------


class TestEvaluatorRegistry:
    def test_register_and_get(self):
        registry = EvaluatorRegistry()
        ev = ExactMatchEvaluator()
        meta = _metadata("ananke.outcome.exact_match")
        registry.register(evaluator=ev, metadata=meta)
        result = registry.get("ananke.outcome.exact_match")
        assert result is ev

    def test_get_returns_none_for_unknown(self):
        registry = EvaluatorRegistry()
        assert registry.get("nonexistent.evaluator") is None

    def test_list_all_includes_registered(self):
        registry = EvaluatorRegistry()
        ev = ExactMatchEvaluator()
        registry.register(evaluator=ev, metadata=_metadata("test.ev"))
        all_evs = registry.list_all()
        ids = [e["id"] for e in all_evs]
        assert "test.ev" in ids

    def test_list_all_entry_structure(self):
        registry = EvaluatorRegistry()
        registry.register(evaluator=ExactMatchEvaluator(), metadata=_metadata("test.ev"))
        entry = registry.list_all()[0]
        assert "id" in entry
        assert "version" in entry
        assert "dimension" in entry
        assert "deterministic" in entry
        assert "enabled" in entry

    def test_list_enabled_includes_enabled(self):
        registry = EvaluatorRegistry()
        registry.register(evaluator=ExactMatchEvaluator(), metadata=_metadata("ev.a"))
        assert "ev.a" in registry.list_enabled()

    def test_disable_removes_from_get(self):
        registry = EvaluatorRegistry()
        registry.register(evaluator=ExactMatchEvaluator(), metadata=_metadata("ev.b"))
        registry.disable("ev.b")
        assert registry.get("ev.b") is None

    def test_disable_removes_from_list_enabled(self):
        registry = EvaluatorRegistry()
        registry.register(evaluator=ExactMatchEvaluator(), metadata=_metadata("ev.c"))
        registry.disable("ev.c")
        assert "ev.c" not in registry.list_enabled()

    def test_enable_restores_after_disable(self):
        registry = EvaluatorRegistry()
        ev = ExactMatchEvaluator()
        registry.register(evaluator=ev, metadata=_metadata("ev.d"))
        registry.disable("ev.d")
        registry.enable("ev.d")
        assert registry.get("ev.d") is ev

    def test_enable_nonexistent_does_not_crash(self):
        registry = EvaluatorRegistry()
        registry.enable("does.not.exist")  # should not raise

    def test_disable_nonexistent_does_not_crash(self):
        registry = EvaluatorRegistry()
        registry.disable("does.not.exist")  # should not raise

    def test_register_multiple(self):
        registry = EvaluatorRegistry()
        for i in range(3):
            registry.register(
                evaluator=ExactMatchEvaluator(),
                metadata=_metadata(f"ev.{i}"),
            )
        assert len(registry.list_all()) == 3

    def test_register_overwrites_existing_id(self):
        registry = EvaluatorRegistry()
        ev1 = ExactMatchEvaluator()
        ev2 = ExactMatchEvaluator()
        registry.register(evaluator=ev1, metadata=_metadata("ev.x"))
        registry.register(evaluator=ev2, metadata=_metadata("ev.x"))
        assert registry.get("ev.x") is ev2

    def test_list_all_disabled_entry_marked(self):
        registry = EvaluatorRegistry()
        registry.register(evaluator=ExactMatchEvaluator(), metadata=_metadata("ev.disabled"))
        registry.disable("ev.disabled")
        entry = next(e for e in registry.list_all() if e["id"] == "ev.disabled")
        assert entry["enabled"] is False


# ---------------------------------------------------------------------------
# EvaluatorMetadata
# ---------------------------------------------------------------------------


class TestEvaluatorMetadata:
    def test_defaults(self):
        meta = EvaluatorMetadata(id="test.ev")
        assert meta.version == "1"
        assert meta.deterministic is True
        assert meta.network is False
        assert meta.license == "Apache-2.0"
        assert meta.requires == []

    def test_custom_fields(self):
        meta = EvaluatorMetadata(
            id="test.ev",
            version="2",
            description="desc",
            dimension="safety",
            deterministic=False,
            network=True,
        )
        assert meta.dimension == "safety"
        assert meta.network is True


# ---------------------------------------------------------------------------
# get_default_registry singleton
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_singleton_same_object(self):
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is r2

    def test_returns_evaluator_registry_instance(self):
        registry = get_default_registry()
        assert isinstance(registry, EvaluatorRegistry)

    def test_list_all_returns_list(self):
        registry = get_default_registry()
        result = registry.list_all()
        assert isinstance(result, list)

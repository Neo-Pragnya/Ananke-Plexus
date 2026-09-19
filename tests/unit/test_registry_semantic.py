"""Optional similarity search: policy gating, determinism, ranking, cache, plugin governance."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry import semantic
from ananke.plexus.registry.errors import PolicyViolationError, RegistryError
from ananke.plexus.registry.policy import RegistryPolicy, SemanticPolicy
from ananke.plexus.registry.registry import Registry


def _registry(tmp_path: Path, **sem: Any) -> Registry:
    reg = open_registry(tmp_path, RegistryPolicy(semantic=SemanticPolicy(enabled=True, **sem)))
    add(reg, "graph-review", summary="Reviews pull requests using code graph blast radius")
    add(reg, "test-runner", summary="Executes unit tests and reports failures")
    add(reg, "secret-scanner", summary="Scans repositories for leaked credentials")
    return reg


class TestEmbedder:
    def test_deterministic_unit_vectors(self) -> None:
        e = semantic.LocalHashEmbedder()
        a, b = (
            e.embed_one("Graph review of pull requests"),
            e.embed_one("Graph review of pull requests"),
        )
        assert a == b and len(a) == e.dim
        assert math.isclose(sum(x * x for x in a), 1.0, abs_tol=1e-3)
        assert e.embed_one("") == [0.0] * e.dim

    def test_shared_vocabulary_scores_higher_than_unrelated(self) -> None:
        e = semantic.LocalHashEmbedder()
        q = e.embed_one("reviewing graphs")
        near = e.embed_one("graph review")
        far = e.embed_one("credential leak scanner")
        assert semantic._cosine(q, near) > semantic._cosine(q, far) + 0.2

    def test_tolerates_typos_and_camel_case(self) -> None:
        e = semantic.LocalHashEmbedder()
        typo = e.embed_one("grahp reveiw")
        assert semantic._cosine(typo, e.embed_one("graph review")) > 0.2
        assert semantic._cosine(typo, e.embed_one("credential leak scanner")) < 0.05
        assert e.tokens("CodeGraphReview") == ["code", "graph", "review"]


class TestSearch:
    def test_disabled_by_default(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg)
        with pytest.raises(PolicyViolationError) as exc:
            reg.search("graph", semantic=True)
        assert exc.value.code == "SEMANTIC_DISABLED"
        assert reg.search("graph")  # lexical search is unaffected

    def test_ranks_by_similarity_and_respects_filters(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        hits = reg.search("reviewing code graphs", semantic=True)
        assert hits and hits[0].name == "graph-review"
        assert reg.search("kind:agent reviewing code graphs", semantic=True) == []
        assert reg.search("", semantic=True) == []

    def test_lexical_misses_what_similarity_finds(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        assert reg.search("secrit scaner credentails") == []  # misspelt: no exact/prefix match
        hits = reg.search("secrit scaner credentails", semantic=True)
        assert hits and hits[0].name == "secret-scanner"

    def test_vectors_cached_and_invalidated_by_metadata_change(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.search("graph", semantic=True)
        cache = semantic.KVCache(reg.root / "cache.db")
        before = cache.count()
        assert before >= 3
        reg.search("graph", semantic=True)
        assert cache.count() == before  # nothing recomputed or duplicated
        reg.correct_metadata("core/test-runner@1.0.0", summary="Now also scans for credentials")
        hits = reg.search("credentials", semantic=True)
        assert "test-runner" in [h.name for h in hits]

    def test_corrupt_cache_entry_is_recomputed(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        cache = semantic.KVCache(reg.root / "cache.db")
        reg.search("graph", semantic=True)
        for rec in reg.store.list_records():
            emb = semantic.LocalHashEmbedder()
            key = (
                f"emb:{emb.model}:{emb.version}:"
                + __import__("hashlib").sha256(semantic.record_text(rec).encode()).hexdigest()[:32]
            )
            cache.set(key, "not json")
        assert reg.search("graph", semantic=True)

    def test_unknown_provider(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path, provider="nope")
        with pytest.raises(RegistryError) as exc:
            reg.search("x", semantic=True)
        assert exc.value.code == "SEMANTIC_PROVIDER_UNKNOWN"

    def test_remote_embedder_needs_consent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Remote:
            model, version, remote = "cloud", "1", True

            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        class EP:
            name = "cloud"

            def load(self) -> type[Remote]:
                return Remote

        class EPs:
            def select(self, group: str) -> list[EP]:
                assert group == "ananke.registry.embedders"
                return [EP()]

        monkeypatch.setattr(semantic.importlib.metadata, "entry_points", lambda: EPs())
        denied = _registry(tmp_path / "a", provider="cloud")
        with pytest.raises(PolicyViolationError) as exc:
            denied.search("x", semantic=True)
        assert exc.value.code == "SEMANTIC_REMOTE_DENIED"

        allowed = _registry(tmp_path / "b", provider="cloud", allow_remote=True)
        assert allowed.search("anything", semantic=True)  # constant vectors: everything matches

    def test_cli_and_policy_roundtrip(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from ananke.plexus.registry.cli import registry_app
        from ananke.plexus.registry.policy import dump_policy_toml, load_policy

        runner = CliRunner()
        proj = ["--project", str(tmp_path)]
        assert runner.invoke(registry_app, ["init", *proj]).exit_code == 0
        skill = tmp_path / "s"
        skill.mkdir()
        (skill / "ananke.yaml").write_text(
            "kind: skill\nnamespace: core\nname: graph-review\nversion: 1.0.0\n"
            "summary: Reviews pull requests using code graph blast radius\n"
            "license: {expression: MIT}\ncapabilities: [graph.query]\n"
        )
        assert runner.invoke(registry_app, ["learn", str(skill), *proj]).exit_code == 0

        off = runner.invoke(registry_app, ["search", "reviewing graphs", "--semantic", *proj])
        assert off.exit_code == 3 and "SEMANTIC_DISABLED" in off.output + (off.stderr or "")

        policy_file = tmp_path / ".ananke" / "registry" / "policy.toml"
        policy = load_policy(policy_file.parent)
        policy.semantic.enabled = True
        policy_file.write_text(dump_policy_toml(policy))
        assert load_policy(policy_file.parent).semantic.enabled is True
        res = runner.invoke(registry_app, ["search", "reviewing graphs", "--semantic", *proj])
        assert res.exit_code == 0 and "graph-review" in res.output

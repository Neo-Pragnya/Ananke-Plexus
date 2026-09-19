"""Resolver, lockfile and explainability (spec §46-§54, §166)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from registry_support import add, artifact, open_registry

from ananke.plexus.registry.errors import InvalidRequirementError, ResolutionError
from ananke.plexus.registry.lockfile import (
    build_lock,
    dump_lock,
    load_lock,
    parse_lock,
    verify_lock,
    write_lock,
)
from ananke.plexus.registry.models import LifecycleStatus, ResolutionMode, TrustStatus
from ananke.plexus.registry.policy import (
    LicensePolicy,
    RegistryPolicy,
    ResolvePolicy,
    enterprise_policy,
)
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    Resolver,
    RuleContext,
    RuleOutcome,
    resolve,
)


def R(reg, ref: str, **kw):
    return Resolver(reg, **kw).resolve(ref)


def picked(reg, ref: str, **kw) -> str:
    result = R(reg, ref, **kw)
    result.raise_if_failed()
    assert result.selected is not None
    return result.selected.version


class TestSelection:
    def test_highest_matching_version(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        for v in ("1.0.0", "1.4.2", "1.9.0", "2.0.0"):
            add(reg, version=v)
        assert picked(reg, "core/graph-review@^1") == "1.9.0"
        assert picked(reg, "core/graph-review@~1.4") == "1.4.2"
        assert picked(reg, "core/graph-review") == "2.0.0"
        assert picked(reg, "core/graph-review@=1.0.0") == "1.0.0"

    def test_lowest_compatible_mode(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        for v in ("1.2.0", "1.5.0"):
            add(reg, version=v)
        opts = ResolutionOptions(mode=ResolutionMode.LOWEST_COMPATIBLE)
        assert picked(reg, "core/graph-review@^1", options=opts) == "1.2.0"

    def test_prerelease_excluded_by_default(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0-beta.1", channel="stable")
        assert picked(reg, "core/graph-review@^1") == "1.0.0"
        assert picked(reg, "core/graph-review@>=1.1.0-beta.1") == "1.1.0-beta.1"
        assert picked(reg, "core/graph-review@=1.1.0-beta.1") == "1.1.0-beta.1"
        opts = ResolutionOptions(allow_prerelease=True)
        assert picked(reg, "core/graph-review@^1", options=opts) == "1.1.0-beta.1"

    def test_channel_filter_and_symbolic_stable(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0", channel="stable")
        add(reg, version="1.1.0", channel="candidate")
        add(reg, version="1.2.0", channel="beta")
        assert picked(reg, "core/graph-review") == "1.0.0"  # stable outranks a newer candidate
        assert (
            picked(reg, "core/graph-review", options=ResolutionOptions(channels=["candidate"]))
            == "1.1.0"
        )
        assert picked(reg, "core/graph-review@stable") == "1.0.0"
        assert (
            picked(reg, "core/graph-review", options=ResolutionOptions(channels=["beta"]))
            == "1.2.0"
        )
        stable_only = ResolutionOptions(mode=ResolutionMode.STABLE_ONLY)
        assert picked(reg, "core/graph-review", options=stable_only) == "1.0.0"

    def test_trust_is_ranked_before_semver(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="2.4.1", approve=True)
        add(reg, version="2.5.0")  # only discovered
        assert picked(reg, "core/graph-review@^2") == "2.4.1"

    def test_minimum_trust_policy(self, tmp_path: Path) -> None:
        policy = RegistryPolicy(resolve=ResolvePolicy(minimum_trust=TrustStatus.APPROVED))
        reg = open_registry(tmp_path, policy)
        add(reg, version="1.0.0")
        result = R(reg, "core/graph-review")
        assert not result.ok and "trust status = discovered" in result.explain()
        reg.promote(
            "core/graph-review@1.0.0", trust=TrustStatus.APPROVED, channel="stable", run_gate=False
        )
        assert picked(reg, "core/graph-review") == "1.0.0"

    def test_highest_approved_mode_needs_approved(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        opts = ResolutionOptions(mode=ResolutionMode.HIGHEST_APPROVED)
        assert not R(reg, "core/graph-review", options=opts).ok
        reg.promote(
            "core/graph-review@1.0.0", trust=TrustStatus.APPROVED, channel="stable", run_gate=False
        )
        assert picked(reg, "core/graph-review", options=opts) == "1.0.0"

    def test_restricted_needs_policy_opt_in(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, trust=TrustStatus.RESTRICTED)
        assert not R(reg, "core/graph-review").ok
        reg.policy = RegistryPolicy(resolve=ResolvePolicy(allow_restricted=True))
        assert picked(reg, "core/graph-review") == "1.0.0"

    def test_symbolic_approved_requires_approved(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0", approve=True)
        add(reg, version="1.1.0")
        assert picked(reg, "core/graph-review@approved") == "1.0.0"

    def test_license_policy_filters(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        reg.policy = RegistryPolicy(licenses=LicensePolicy(allow=["MIT"]))
        # the stored approval was decided at registration; deny after the fact via metadata correction
        from ananke.plexus.registry.models import LicenseInfo

        reg.correct_metadata(
            "core/graph-review@1.0.0", license=LicenseInfo(expression="GPL-3.0-only")
        )
        assert "license" in R(reg, "core/graph-review").explain()
        assert not R(reg, "core/graph-review").ok

    def test_security_findings_block(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.models import Security

        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")
        reg.update_security(
            "core/graph-review@1.1.0",
            Security(
                status="vulnerable",
                critical_count=1,
                findings_count=1,
                last_scanned_at="t",
                scanner="x",
            ),
        )
        assert picked(reg, "core/graph-review") == "1.0.0"

    def test_quality_requirement(self, tmp_path: Path) -> None:
        from ananke.plexus.registry.models import Quality

        policy = RegistryPolicy(resolve=ResolvePolicy(require_quality=["tests"]))
        reg = open_registry(tmp_path, policy)
        add(reg, version="1.0.0")
        assert not R(reg, "core/graph-review").ok
        reg.update_quality("core/graph-review@1.0.0", Quality(tests_status="pass"))
        assert picked(reg, "core/graph-review") == "1.0.0"


class TestLifecycleRules:
    def test_yanked_deprecated_quarantined_excluded(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
            add(reg, version=v)
        reg.yank("core/graph-review@1.3.0")
        reg.deprecate("core/graph-review@1.2.0")
        reg.quarantine("core/graph-review@1.1.0", "bad")
        assert picked(reg, "core/graph-review") == "1.0.0"

    def test_quarantined_never_selected_even_when_pinned_or_allowed(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        reg.quarantine("core/graph-review@1.0.0", "bad")
        opts = ResolutionOptions(
            allow_yanked=True,
            allow_deprecated=True,
            allow_prerelease=True,
            minimum_trust=TrustStatus.UNKNOWN,
        )
        assert not R(reg, "core/graph-review@=1.0.0", options=opts).ok
        locked = ResolutionOptions(
            pins={
                "ananke://skill/core/graph-review": (
                    "1.0.0",
                    reg.exact_version("core/graph-review@1.0.0").digest_sha256,
                )
            },
            pin_mode="strict",
        )
        assert not R(reg, "core/graph-review", options=locked).ok

    def test_deprecated_allowed_when_explicit_or_policy(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        reg.deprecate("core/graph-review", "old", replacement="core/new")
        result = R(reg, "core/graph-review")
        assert not result.ok and "use core/new" in result.explain()
        assert picked(reg, "core/graph-review@=1.0.0") == "1.0.0"
        assert (
            picked(reg, "core/graph-review", options=ResolutionOptions(allow_deprecated=True))
            == "1.0.0"
        )

    def test_yank_reproducibility_via_lock(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")
        lock = build_lock(
            R(reg, "core/graph-review@^1"), [("ananke://skill/core/graph-review", "^1")]
        )
        assert lock.entries[0].version == "1.1.0"
        reg.yank("core/graph-review@1.1.0")
        assert picked(reg, "core/graph-review@^1") == "1.0.0"  # new resolution avoids it
        opts = ResolutionOptions(pins=lock.pins(), pin_mode="strict")
        assert (
            picked(reg, "core/graph-review@^1", options=opts) == "1.1.0"
        )  # lock still materializes


class TestDependencies:
    def test_transitive_resolution_and_topological_order(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "graph-query", "1.8.0")
        add(reg, "graph-query", "1.8.2")
        add(
            reg,
            "repo-context",
            "1.4.1",
            dependencies=[{"skill": "core/graph-query", "version": "^1"}],
        )
        add(
            reg,
            "graph-review",
            "2.1.0",
            dependencies=[{"skill": "core/repo-context", "version": "^1.4"}],
        )
        res = R(reg, "core/graph-review@^2")
        assert res.ok
        order = [n.ref.ref for n in res.nodes]
        assert order == [
            "ananke://skill/core/graph-query@1.8.2",
            "ananke://skill/core/repo-context@1.4.1",
            "ananke://skill/core/graph-review@2.1.0",
        ]
        assert [n.root for n in res.nodes] == [False, False, True]
        assert res.nodes[1].requested_by == ["ananke://skill/core/graph-review@2.1.0"]

    def test_backtracking_picks_compatible_combination(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        add(reg, "lib", "2.0.0")
        add(reg, "a", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^1"}])
        add(reg, "b", "1.0.0", dependencies=[{"skill": "core/lib", "version": ">=1,<3"}])
        res = Resolver(reg).resolve_many(["core/a", "core/b"])
        assert res.ok
        lib = next(n for n in res.nodes if n.ref.uri.endswith("/lib"))
        assert lib.ref.version == "1.0.0"  # ^1 constrains, so 2.0.0 is not chosen

    def test_backtracks_off_a_version_whose_dependency_is_unsatisfiable(
        self, tmp_path: Path
    ) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        add(reg, "app", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^1"}])
        add(reg, "app", "2.0.0", dependencies=[{"skill": "core/lib", "version": "^5"}])
        assert picked(reg, "core/app") == "1.0.0"

    def test_conflicting_requirements_fail_with_explanation(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        add(reg, "lib", "2.0.0")
        add(reg, "a", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^1"}])
        add(reg, "b", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^2"}])
        res = Resolver(reg).resolve_many(["core/a", "core/b"])
        assert not res.ok
        assert "conflicting requirements" in res.explain() or "could not" in res.explain().lower()

    def test_missing_dependency_fails(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "app", "1.0.0", dependencies=[{"skill": "core/ghost", "version": "^1"}])
        res = R(reg, "core/app")
        assert not res.ok and "not registered" in res.explain()

    def test_optional_missing_dependency_is_skipped(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(
            reg,
            "app",
            "1.0.0",
            dependencies=[
                {
                    "type": "artifact",
                    "id": "ananke://skill/core/ghost",
                    "version": "^1",
                    "optional": True,
                }
            ],
        )
        assert R(reg, "core/app").ok

    def test_agent_resolves_full_capability_graph(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "graph-review", "2.1.3", approve=True)
        add(reg, "test-runner", "3.4.0", approve=True)
        add(
            reg,
            "coding-agent",
            "3.2.0",
            kind="agent",
            namespace="engineering",
            skills=[
                {"ref": "core/graph-review", "version": "^2.0"},
                {"ref": "core/test-runner", "version": "^3.0"},
            ],
            runtime={"provider": "pydantic", "supported": ["pydantic"]},
        )
        res = R(reg, "engineering/coding-agent@^3")
        assert res.ok and {n.ref.version for n in res.nodes} == {"2.1.3", "3.4.0", "3.2.0"}


class TestCompatibility:
    def test_runtime_filter(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0", runtime={"supported": ["microsoft"]})
        add(reg, version="1.1.0", runtime={"supported": ["pydantic", "microsoft"]})
        env = ResolutionEnvironment.detect(runtime="pydantic")
        assert picked(reg, "core/graph-review", env=env) == "1.1.0"
        env_other = ResolutionEnvironment.detect(runtime="langgraph")
        assert not R(reg, "core/graph-review", env=env_other).ok

    def test_ananke_python_os_arch_ranges(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0", compatibility={"ananke": ">=99"})
        add(reg, version="1.1.0", compatibility={"python": "<3"})
        add(reg, version="1.2.0", compatibility={"operating_systems": ["plan9"]})
        add(reg, version="1.3.0", compatibility={"architecture": ["sparc"]})
        res = R(reg, "core/graph-review")
        text = res.explain()
        assert not res.ok
        for needle in (
            "requires Ananke >=99",
            "requires Python <3",
            "operating system",
            "architecture",
        ):
            assert needle in text

    def test_model_capability_requirement(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0", model_requirements={"tool_calling": True})
        assert R(reg, "core/graph-review").ok  # environment capabilities unknown -> not enforced
        assert not R(
            reg,
            "core/graph-review",
            env=ResolutionEnvironment.detect(model_capabilities=["structured_output"]),
        ).ok
        assert R(
            reg,
            "core/graph-review",
            env=ResolutionEnvironment.detect(model_capabilities=["tool_calling"]),
        ).ok


class TestExplain:
    def test_matches_spec_shape(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="2.4.1", approve=True, runtime={"supported": ["pydantic"]})
        add(reg, version="2.5.0-beta.2", channel="beta")
        add(reg, version="2.4.2")
        policy = RegistryPolicy(
            resolve=ResolvePolicy(minimum_trust=TrustStatus.APPROVED, channel=["stable"])
        )
        reg.policy = policy
        res = R(reg, "core/graph-review@^2", env=ResolutionEnvironment.detect(runtime="pydantic"))
        text = res.explain()
        assert res.selected is not None and res.selected.version == "2.4.1"
        assert "Selected 2.4.1" in text and "✓ matches ^2" in text and "✓ stable" in text
        assert "✓ approved" in text and "✓ supports runtime pydantic" in text
        assert "Rejected 2.5.0-beta.2:" in text and "✗ beta channel not allowed" in text
        assert "Rejected 2.4.2:" in text and "✗ trust status = discovered" in text

    def test_candidate_ranks_reported(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")
        res = R(reg, "core/graph-review")
        ranks = {c.version: c.rank for c in res.decisions[0].candidates}
        assert ranks == {"1.1.0": 1, "1.0.0": 2}
        assert res.decisions[0].candidates[0].selected

    def test_no_match_message(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        res = R(reg, "core/graph-review@^9")
        assert not res.ok and "no registered version matches ^9" in res.decisions[0].failure  # type: ignore[operator]

    def test_unknown_artifact(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        res = R(reg, "core/nothing")
        assert not res.ok and "core/nothing" in (res.decisions[0].failure or "")
        with pytest.raises(ResolutionError):
            resolve(reg, "core/nothing")


class TestExtensionRules:
    def test_custom_rule_can_reject_and_penalize(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")

        class NoOnePointOne:
            id = "custom"

            def evaluate(self, rec, ctx: RuleContext):  # type: ignore[no-untyped-def]
                if rec.version == "1.1.0":
                    return [RuleOutcome("reject", "banned by house rule")]
                return []

        from ananke.plexus.registry.resolver import default_rules

        res = Resolver(reg, rules=[*default_rules(), NoOnePointOne()]).resolve("core/graph-review")
        assert res.selected is not None and res.selected.version == "1.0.0"
        assert "banned by house rule" in res.explain()

    def test_penalty_lowers_rank(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        add(reg, version="1.1.0")

        class Penalise:
            id = "p"

            def evaluate(self, rec, ctx):  # type: ignore[no-untyped-def]
                return [RuleOutcome("penalty", "flaky", 1.0)] if rec.version == "1.1.0" else []

        from ananke.plexus.registry.resolver import default_rules

        res = Resolver(reg, rules=[*default_rules(), Penalise()]).resolve("core/graph-review")
        assert res.selected is not None and res.selected.version == "1.0.0"


class TestModes:
    def test_exact_mode_requires_pin(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, version="1.0.0")
        opts = ResolutionOptions(mode=ResolutionMode.EXACT)
        assert picked(reg, "core/graph-review@=1.0.0", options=opts) == "1.0.0"
        with pytest.raises(InvalidRequirementError):
            R(reg, "core/graph-review@^1", options=opts)

    def test_enterprise_preset(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path, enterprise_policy())
        add(reg, version="1.0.0")
        assert not R(reg, "core/graph-review").ok  # candidate/discovered is not enough
        reg.promote(
            "core/graph-review@1.0.0", trust=TrustStatus.APPROVED, channel="stable", run_gate=False
        )
        assert picked(reg, "core/graph-review") == "1.0.0"


class TestLockfile:
    def _setup(self, reg) -> None:
        add(reg, "lib", "1.0.0")
        add(reg, "lib", "1.1.0")
        add(reg, "app", "1.0.0", dependencies=[{"skill": "core/lib", "version": "^1"}])

    def test_deterministic_and_roundtrip(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        self._setup(reg)
        lock1 = build_lock(R(reg, "core/app"), [("ananke://skill/core/app", "*")])
        lock2 = build_lock(R(reg, "core/app"), [("ananke://skill/core/app", "*")])
        text = dump_lock(lock1)
        assert text == dump_lock(lock2)
        assert "version = 1" in text and "[[artifact]]" in text and 'digest = "sha256:' in text
        assert parse_lock(text) == lock1

    def test_write_records_lock_and_verifies(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        self._setup(reg)
        lock = build_lock(R(reg, "core/app"), [("ananke://skill/core/app", "*")])
        path, digest = write_lock(reg, tmp_path / "ananke.lock", lock)
        assert load_lock(path) == lock and digest.startswith("sha256:")
        assert reg.store.locked_digests() == {e.sha256 for e in lock.entries}
        assert all(c.ok for c in verify_lock(reg, lock))
        assert "lock.created" in [e["event_type"] for e in reg.store.list_events()]

    def test_verify_detects_problems(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        self._setup(reg)
        lock = build_lock(R(reg, "core/app"), [("ananke://skill/core/app", "*")])
        lib = next(e for e in lock.entries if e.id.endswith("/lib"))
        reg.quarantine(f"core/lib@{lib.version}", "bad")
        bad = {c.id: c for c in verify_lock(reg, lock)}
        assert not bad[lib.ref].ok and "quarantined" in bad[lib.ref].detail
        # tamper with the blob
        blob = reg.cas._path(lib.sha256)
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        assert "blob unavailable" in {c.id: c for c in verify_lock(reg, lock)}[lib.ref].detail

    def test_locked_version_never_silently_changes(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        self._setup(reg)
        lock = build_lock(R(reg, "core/app"), [("ananke://skill/core/app", "*")])
        add(reg, "lib", "1.2.0")  # a newer compatible version appears
        assert picked(reg, "core/app") is not None
        strict = ResolutionOptions(pins=lock.pins(), pin_mode="strict")
        res = R(reg, "core/app", options=strict)
        assert {n.ref.uri.rsplit("/", 1)[1]: n.ref.version for n in res.nodes}["lib"] == "1.1.0"
        assert res.lock_preserved is True

    def test_locked_mode_rejects_unlocked_artifacts(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        self._setup(reg)
        opts = ResolutionOptions(mode=ResolutionMode.LOCKED, pins={})
        res = R(reg, "core/app", options=opts)
        assert not res.ok and "not in the lockfile" in res.explain()

    def test_digest_mismatch_with_lock_is_rejected(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        opts = ResolutionOptions(
            pins={"ananke://skill/core/lib": ("1.0.0", "0" * 64)}, pin_mode="strict"
        )
        res = R(reg, "core/lib", options=opts)
        assert not res.ok and "digest differs from lockfile" in res.explain()

    def test_prefer_mode_falls_back_when_pin_no_longer_valid(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        add(reg, "lib", "1.0.0")
        add(reg, "lib", "1.1.0")
        pins = {
            "ananke://skill/core/lib": ("1.0.0", reg.exact_version("core/lib@1.0.0").digest_sha256)
        }
        prefer = ResolutionOptions(pins=pins, pin_mode="prefer")
        assert picked(reg, "core/lib@^1", options=prefer) == "1.0.0"
        assert picked(reg, "core/lib@^1.1", options=prefer) == "1.1.0"  # pin no longer satisfies


class TestProperties:
    """Spec §166 resolver properties, checked over random registries."""

    def _random_registry(self, tmp_path: Path, seed: int):
        rng = random.Random(seed)
        reg = open_registry(tmp_path / f"r{seed}")
        names = ["a", "b", "c", "d"]
        for name in names:
            for major in (1, 2):
                for minor in range(rng.randint(1, 3)):
                    deps = []
                    idx = names.index(name)
                    for dep in names[idx + 1 :]:
                        if rng.random() < 0.5:
                            deps.append(
                                {
                                    "skill": f"core/{dep}",
                                    "version": rng.choice(["^1", "^2", ">=1,<3"]),
                                }
                            )
                    add(
                        reg,
                        name,
                        f"{major}.{minor}.0",
                        dependencies=deps,
                        channel=rng.choice(["stable", "candidate", "beta"]),
                    )
                    if rng.random() < 0.15:
                        reg.quarantine(f"core/{name}@{major}.{minor}.0", "x")
                    elif rng.random() < 0.15:
                        reg.yank(f"core/{name}@{major}.{minor}.0")
        return reg, names

    @pytest.mark.parametrize("seed", range(8))
    def test_properties(self, tmp_path: Path, seed: int) -> None:
        reg, names = self._random_registry(tmp_path, seed)
        for name in names:
            first = R(reg, f"core/{name}@^1")
            second = R(reg, f"core/{name}@^1")
            assert first.model_dump() == second.model_dump()  # same inputs -> same resolution
            if not first.ok:
                continue
            for node in first.nodes:
                rec = reg.exact_version(node.ref.ref)
                assert (
                    rec.lifecycle is LifecycleStatus.ACTIVE
                )  # never quarantined/yanked by default
                assert rec.trust is not TrustStatus.QUARANTINED
                assert rec.digest == node.ref.digest  # digest matches what the registry holds
                for dep in rec.manifest.artifact_dependencies():  # all constraints satisfied
                    target = next(n for n in first.nodes if n.ref.uri == dep.id)
                    from ananke.plexus.registry.semver import Version, VersionReq

                    assert VersionReq.parse(dep.version).matches(Version.parse(target.ref.version))
            lock = build_lock(first, [(first.nodes[-1].ref.uri, "^1")])
            again = R(
                reg,
                f"core/{name}@^1",
                options=ResolutionOptions(pins=lock.pins(), pin_mode="strict"),
            )
            assert [n.ref for n in again.nodes] == [
                n.ref for n in first.nodes
            ]  # locked never changes

    def test_exact_pin_selects_only_that_version(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        for v in ("1.0.0", "1.1.0", "1.2.0"):
            add(reg, version=v)
        for v in ("1.0.0", "1.1.0", "1.2.0"):
            assert picked(reg, f"core/graph-review@={v}") == v
        assert not R(reg, "core/graph-review@=1.3.0").ok


def test_requirement_parse() -> None:
    r = Requirement.parse("ananke://skill/core/x@^2")
    assert (r.name, r.namespace, r.req) == ("x", "core", "^2")
    assert Requirement.parse("core/x", version="~1.2").req == "~1.2"
    assert artifact  # imported helper re-export sanity

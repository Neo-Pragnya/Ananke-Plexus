"""Diff heuristics, licences, policy round-trip, cache, TOML writer, duplicates (spec §42-§45, §91, §119, §127)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from registry_support import add, open_registry

from ananke.plexus.registry.cache import KVCache
from ananke.plexus.registry.diff import (
    apply_bump,
    diff_files,
    diff_manifests,
    diff_permissions,
    render_diff_text,
    schema_breaking,
)
from ananke.plexus.registry.duplicates import find_duplicates
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.jsonschema_util import _basic, check_json_schema
from ananke.plexus.registry.models import ArtifactManifest, Permissions
from ananke.plexus.registry.policy import (
    LicensePolicy,
    RegistryPolicy,
    dump_policy_toml,
    enterprise_policy,
    evaluate_license,
    load_policy,
    policy_path,
)
from ananke.plexus.registry.tomlw import dumps


def manifest(**fields: Any) -> ArtifactManifest:
    base: dict[str, Any] = {"kind": "skill", "namespace": "core", "name": "x", "version": "1.0.0"}
    base.update(fields)
    return ArtifactManifest.model_validate(base)


OBJ = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


class TestSchemaBreaking:
    def test_no_change_and_additive_input(self) -> None:
        assert schema_breaking(OBJ, OBJ, "input") == []
        wider = {**OBJ, "properties": {**OBJ["properties"], "limit": {"type": "integer"}}}
        assert schema_breaking(OBJ, wider, "input") == []  # optional new property is compatible

    def test_input_breaking_cases(self) -> None:
        new_required = {
            **OBJ,
            "required": ["q", "mode"],
            "properties": {**OBJ["properties"], "mode": {"type": "string"}},
        }
        assert any(
            "new required property" in r for r in schema_breaking(OBJ, new_required, "input")
        )
        removed = {"type": "object", "properties": {}}
        assert any("property removed" in r for r in schema_breaking(OBJ, removed, "input"))
        retyped = {**OBJ, "properties": {"q": {"type": "integer"}}}
        assert any("type string -> integer" in r for r in schema_breaking(OBJ, retyped, "input"))
        enum_old = {"type": "string", "enum": ["a", "b"]}
        assert any(
            "enum value 'b' removed" in r
            for r in schema_breaking(enum_old, {"type": "string", "enum": ["a"]}, "input")
        )
        assert schema_breaking(enum_old, {"type": "string", "enum": ["a", "b", "c"]}, "input") == []

    def test_output_breaking_cases(self) -> None:
        loosened = {**OBJ, "required": []}
        assert any("no longer guaranteed" in r for r in schema_breaking(OBJ, loosened, "output"))
        enum_old = {"type": "string", "enum": ["a"]}
        assert any(
            "enum value 'b' added" in r
            for r in schema_breaking(enum_old, {"type": "string", "enum": ["a", "b"]}, "output")
        )
        assert any("output schema removed" in r for r in schema_breaking(OBJ, None, "output"))
        assert schema_breaking(None, OBJ, "output") == []

    def test_schema_introduced_or_removed_for_inputs(self) -> None:
        assert schema_breaking(None, OBJ, "input") and schema_breaking(OBJ, None, "input") == []
        assert schema_breaking(None, None, "input") == []

    def test_nested_and_array_items(self) -> None:
        old = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"id": {"type": "string"}}},
                }
            },
        }
        new = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object", "properties": {}}}
            },
        }
        reasons = schema_breaking(old, new, "output")
        assert any("$.items[].id: property removed" in r for r in reasons)

    def test_depth_guard(self) -> None:
        deep: dict[str, Any] = {"type": "object", "properties": {}}
        cursor = deep
        for _ in range(20):
            child: dict[str, Any] = {"type": "object", "properties": {}}
            cursor["properties"]["n"] = child
            cursor = child
        assert schema_breaking(deep, deep, "input") == []


class TestDiffEngine:
    def test_tool_and_contract_changes(self) -> None:
        old = manifest(tools=[{"name": "t", "input_schema": OBJ}], inputs={"json_schema": OBJ})
        new_tools = manifest(
            tools=[
                {"name": "t", "input_schema": {"type": "object", "properties": {}}},
                {"name": "extra"},
            ],
            inputs={"json_schema": OBJ},
        )
        d = diff_manifests(old, new_tools)
        assert d.tools_added == ["extra"] and d.input_breaking and d.suggested_bump == "MAJOR"
        removed = diff_manifests(old, manifest(inputs={"json_schema": OBJ}))
        assert removed.tools_removed == ["t"] and removed.suggested_bump == "MAJOR"
        compat = diff_manifests(
            manifest(inputs={"json_schema": OBJ}),
            manifest(
                inputs={
                    "json_schema": {
                        **OBJ,
                        "properties": {**OBJ["properties"], "n": {"type": "integer"}},
                    }
                }
            ),
        )
        assert (
            compat.input_changed and not compat.input_breaking and compat.suggested_bump == "MINOR"
        )

    def test_dependencies_runtimes_compat_metadata(self) -> None:
        old = manifest(
            dependencies=[
                {"skill": "core/a", "version": "^1"},
                {"skill": "core/b", "version": "^1"},
            ],
            runtime={"supported": ["pydantic", "microsoft"]},
            compatibility={"ananke": ">=1"},
        )
        new = manifest(
            dependencies=[
                {"skill": "core/a", "version": "^2"},
                {"skill": "core/c", "version": "*"},
            ],
            runtime={"supported": ["pydantic", "langgraph"]},
            compatibility={"ananke": ">=2"},
            summary="new",
            metadata={"tags": ["x"]},
            model_requirements={"tool_calling": True},
        )
        d = diff_manifests(old, new)
        assert [c.id for c in d.dependencies_added] == ["ananke://skill/core/c"]
        assert [c.id for c in d.dependencies_removed] == ["ananke://skill/core/b"]
        assert d.dependencies_changed[0].old == "^1" and d.dependencies_changed[0].new == "^2"
        assert d.runtimes_added == ["langgraph"] and d.runtimes_removed == ["microsoft"]
        assert d.compatibility_changed == ["ananke"] and {
            "summary",
            "metadata",
            "model_requirements",
        } <= set(d.metadata_changed)
        assert d.suggested_bump == "MAJOR" and {
            "dependencies",
            "runtime",
            "behavioral contract",
        } <= set(d.change_classes)

    def test_provider_change_is_breaking(self) -> None:
        d = diff_manifests(
            manifest(runtime={"provider": "pydantic"}), manifest(runtime={"provider": "microsoft"})
        )
        assert d.suggested_bump == "MAJOR" and "runtime" in d.change_classes

    def test_identical_and_metadata_only(self) -> None:
        assert diff_manifests(manifest(), manifest()).identical
        meta = diff_manifests(manifest(), manifest(summary="typo fix"))
        assert meta.suggested_bump == "PATCH" and meta.change_classes == ["metadata-only"]

    def test_permission_narrowing_is_patch(self) -> None:
        d = diff_manifests(
            manifest(permissions={"filesystem": {"read": ["**"]}}),
            manifest(permissions={"filesystem": {"read": ["src/**"]}}),
        )
        assert not d.permissions.expanded and d.permissions.removed and d.suggested_bump == "PATCH"

    def test_file_classification_and_text_diff(self) -> None:
        old = {
            "prompts/a.md": b"one\ntwo",
            "README.md": b"docs",
            "src/x.py": b"a",
            "tests/t.py": b"t",
            "schemas/s.json": b"{}",
            "blob.bin": b"\xff\xfe",
        }
        new = {
            "prompts/a.md": b"one\nthree",
            "README.md": b"docs2",
            "src/x.py": b"b",
            "tests/t.py": b"t",
            "schemas/s.json": b"{ }",
            "new.txt": b"n",
            "blob.bin": b"\xff\xff",
        }
        changes = {c.path: c for c in diff_files(old, new)}
        assert changes["prompts/a.md"].kind == "prompt" and "+three" in "\n".join(
            changes["prompts/a.md"].text_diff
        )
        assert (
            changes["README.md"].kind == "docs"
            and changes["src/x.py"].kind == "code"
            and changes["schemas/s.json"].kind == "schema"
        )
        assert changes["new.txt"].change == "added" and "tests/t.py" not in changes
        assert changes["blob.bin"].text_diff == []  # binary files are never diffed as text
        full = diff_manifests(manifest(), manifest(), old_files=old, new_files=new)
        assert {"prompts", "payload"} <= set(full.change_classes)

    def test_docs_only_change_is_metadata_only(self) -> None:
        d = diff_manifests(
            manifest(), manifest(), old_files={"README.md": b"a"}, new_files={"README.md": b"b"}
        )
        assert d.change_classes == ["metadata-only"] and d.suggested_bump == "PATCH"

    def test_permission_coverage_patterns(self) -> None:
        wide = Permissions.model_validate({"filesystem": {"read": ["src/**"]}})
        assert not diff_permissions(
            wide, Permissions.model_validate({"filesystem": {"read": ["src/a/b.py", "src/**"]}})
        ).expanded
        assert diff_permissions(
            wide, Permissions.model_validate({"filesystem": {"read": ["tests/**"]}})
        ).expanded
        star = Permissions.model_validate({"filesystem": {"read": ["src/*"]}})
        assert not diff_permissions(
            star, Permissions.model_validate({"filesystem": {"read": ["src/a.py"]}})
        ).expanded
        assert diff_permissions(
            star, Permissions.model_validate({"filesystem": {"read": ["src/a/b.py"]}})
        ).expanded

    def test_render_text_sections(self) -> None:
        d = diff_manifests(
            manifest(
                capabilities=["graph.query"], dependencies=[{"skill": "core/a", "version": "^1"}]
            ),
            manifest(
                capabilities=["graph.write"],
                dependencies=[
                    {"skill": "core/a", "version": "^2"},
                    {"skill": "core/b", "version": "*"},
                ],
                permissions={"network": True},
                inputs={"json_schema": OBJ},
            ),
        )
        text = render_diff_text(d)
        for needle in (
            "Capabilities:",
            "+ graph.write",
            "- graph.query",
            "EXPANDED",
            "Dependencies:",
            "^1 -> ^2",
            "Change classes:",
            "Suggested semver:",
            "MAJOR",
        ):
            assert needle in text, needle

    def test_apply_bump(self) -> None:
        assert apply_bump("1.4.2", "PATCH") == "1.4.3" and apply_bump("1.4.2", "MINOR") == "1.5.0"
        assert (
            apply_bump("1.4.2", "MAJOR") == "2.0.0" and apply_bump("0.4.2", "MAJOR") == "0.5.0"
        )  # 0.x: breaking bumps minor


class TestLicenses:
    @pytest.mark.parametrize(
        ("expr", "allow", "deny", "expected"),
        [
            ("MIT", [], [], "approved"),
            ("MIT", ["MIT"], [], "approved"),
            ("GPL-3.0-only", ["MIT", "Apache-2.0"], [], "denied"),
            ("MIT", [], ["MIT"], "denied"),
            ("MIT OR GPL-3.0-only", ["MIT"], [], "approved"),
            ("MIT AND GPL-3.0-only", ["MIT"], [], "denied"),
            ("(MIT OR Apache-2.0) AND BSD-3-Clause", ["MIT", "BSD-3-Clause"], [], "approved"),
            ("Apache-2.0 WITH LLVM-exception", ["Apache-2.0"], [], "approved"),
            ("mit", ["MIT"], [], "approved"),
            ("GPL-3.0-only OR MIT", [], ["GPL-3.0-only"], "approved"),
        ],
    )
    def test_spdx_expressions(
        self, expr: str, allow: list[str], deny: list[str], expected: str
    ) -> None:
        assert evaluate_license(expr, LicensePolicy(allow=allow, deny=deny))[0] == expected

    def test_unknown(self) -> None:
        for empty in (None, "", "   ", "***"):
            assert evaluate_license(empty, LicensePolicy())[0] == "unknown"


class TestPolicyFile:
    def test_round_trip_for_every_preset(self, tmp_path: Path) -> None:
        for policy in (RegistryPolicy(), enterprise_policy()):
            reg_dir = tmp_path / str(id(policy))
            reg_dir.mkdir()
            policy_path(reg_dir).write_text(dump_policy_toml(policy))
            assert load_policy(reg_dir) == policy

    def test_yaml_style_wrapper_key_and_dynamic_helpers(self, tmp_path: Path) -> None:
        (tmp_path / "policy.toml").write_text(
            '[registry_policy.resolve]\nminimum_trust = "approved"\n'
        )
        assert load_policy(tmp_path).resolve.minimum_trust.value == "approved"
        p = RegistryPolicy()
        assert not p.dynamic_allowed() and p.dynamic_allowed(cli_override=True)
        assert not p.network_allowed("git") and p.network_allowed("mcp", cli_override=True)
        assert not enterprise_policy().dynamic_allowed(cli_override=True)

    def test_invalid_files(self, tmp_path: Path) -> None:
        (tmp_path / "policy.toml").write_text("not [valid toml")
        with pytest.raises(RegistryError, match="INVALID_POLICY"):
            load_policy(tmp_path)
        (tmp_path / "policy.toml").write_text('[resolve]\nminimum_trust = "godlike"\n')
        with pytest.raises(RegistryError, match="INVALID_POLICY"):
            load_policy(tmp_path)

    def test_missing_file_is_default(self, tmp_path: Path) -> None:
        assert load_policy(tmp_path) == RegistryPolicy()


class TestInfra:
    def test_kv_cache_is_rebuildable(self, tmp_path: Path) -> None:
        cache = KVCache(tmp_path / "c" / "cache.db")
        assert cache.get("k") is None and cache.count() == 0
        cache.set("k", "v")
        cache.set("k", "v2")
        cache.set("page:a", "1")
        assert cache.get("k") == "v2" and cache.count() == 2
        cache.delete_prefix("page:")
        assert cache.get("page:a") is None and cache.count() == 1
        cache.clear()
        assert cache.get("k") is None
        (tmp_path / "corrupt.db").write_bytes(b"not sqlite")
        broken = KVCache(tmp_path / "corrupt.db")
        assert (
            broken.get("x") is None and broken.count() == 0
        )  # corruption degrades to a cache miss
        broken.set("x", "y")  # and heals itself
        assert broken.get("x") == "y"

    def test_toml_writer_round_trips(self) -> None:
        data = {
            "version": 1,
            "flag": True,
            "ratio": 0.5,
            "name": 'quote " and \\ backslash and unicode é',
            "tags": ["a", "b"],
            "table": {"inner": "x", "nested": {"deep": 1}},
            "item": [{"id": "1", "list": ["p"]}, {"id": "2"}],
            "odd key": {"k.k": "v"},
        }
        assert tomllib.loads(dumps(data)) == data
        assert dumps({"a": 1}, header="# hello").startswith("# hello\n")
        with pytest.raises(TypeError):
            dumps({"bad": object()})

    def test_json_schema_basic_checker(self) -> None:
        for good in (
            {},
            True,
            {
                "type": "object",
                "properties": {"a": {"type": ["string", "null"]}},
                "required": ["a"],
            },
            {"items": {"type": "integer"}},
        ):
            assert _basic(good) is None
        for bad in (
            "nope",
            {"type": "blob"},
            {"properties": []},
            {"properties": {"a": 1}},
            {"required": "a"},
            {"enum": 1},
            {"items": 3},
        ):
            assert _basic(bad) is not None
        assert (
            check_json_schema({"type": "nonsense"}) is not None
            and check_json_schema({"type": "string"}) is None
        )

    def test_duplicate_detection_groups(self, tmp_path: Path) -> None:
        reg = open_registry(tmp_path)
        same = {"README.md": b"identical payload", "x.txt": b"x"}
        add(
            reg,
            "one",
            "1.0.0",
            namespace="a",
            files=dict(same),
            capabilities=["graph.query"],
            tools=[{"name": "t", "description": "d"}],
        )
        add(
            reg,
            "two",
            "1.0.0",
            namespace="b",
            files=dict(same),
            capabilities=["graph.query"],
            tools=[{"name": "t", "description": "d"}],
        )
        add(reg, "solo", "1.0.0", capabilities=["test.run"])
        reasons = {g.reason: g.artifacts for g in find_duplicates(reg)}
        assert reasons["same tool schemas"] == ["ananke://skill/a/one", "ananke://skill/b/two"]
        assert "same capability set (low confidence)" in reasons
        assert all("solo" not in " ".join(v) for v in reasons.values())
        assert find_duplicates(open_registry(tmp_path / "empty")) == []

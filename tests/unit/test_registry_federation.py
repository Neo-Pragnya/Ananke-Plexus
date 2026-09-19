"""Pull-only federation: transport safety, policy gating, digest verification, trust boundaries."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from registry_support import add, artifact, open_registry
from typer.testing import CliRunner

from ananke.plexus.registry import remote as remote_mod
from ananke.plexus.registry.cli import registry_app
from ananke.plexus.registry.errors import (
    IntegrityError,
    PolicyViolationError,
    RegistryError,
    RemoteSourceError,
)
from ananke.plexus.registry.models import TrustStatus
from ananke.plexus.registry.policy import (
    RegistryPolicy,
    RemoteSourcesPolicy,
    SourceConfig,
    dump_policy_toml,
)
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.remote import RemoteRegistry, open_remote, pull, validate_url
from ananke.plexus.registry.server import make_server
from ananke.plexus.registry.sources import open_sources, source_statuses


@contextmanager
def serving(reg: Registry, token: str | None = None) -> Iterator[str]:
    server = make_server(reg.root, "127.0.0.1", 0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _local_policy(url: str, **kw: Any) -> RegistryPolicy:
    return RegistryPolicy(
        remote_sources=RemoteSourcesPolicy(enterprise=True, **kw),
        sources={"enterprise": SourceConfig(type="remote", url=url, token_env="ANANKE_TEST_TOKEN")},
    )


@pytest.fixture
def upstream(tmp_path: Path) -> Registry:
    reg = open_registry(tmp_path / "up")
    add(reg, "graph-review", "1.0.0", approve=True)
    add(reg, "graph-review", "1.1.0", approve=True)
    reg.register(
        artifact(
            "coding-agent",
            "2.0.0",
            kind="agent",
            skills=[{"ref": "core/graph-review", "version": "^1"}],
        )
    )
    reg.promote(
        "core/coding-agent@2.0.0", trust=TrustStatus.APPROVED, channel="stable", run_gate=False
    )
    return reg


class TestTransportSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://x.example",
            "http://registry.example.com",  # plain http off-loopback
            "https://user:pw@registry.example.com",
            "https://",
            "not a url",
        ],
    )
    def test_bad_urls_rejected(self, url: str) -> None:
        with pytest.raises(RemoteSourceError):
            validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://registry.example.com/",
            "http://127.0.0.1:8765",
            "http://localhost:1",
            "http://[::1]:9",
        ],
    )
    def test_good_urls(self, url: str) -> None:
        assert validate_url(url) == url.rstrip("/")

    def test_redirects_are_refused(self) -> None:
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()

            def log_message(self, *a: object) -> None:
                return

        srv = HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            client = RemoteRegistry(
                "x", f"http://127.0.0.1:{srv.server_address[1]}", token="secret"
            )
            with pytest.raises(RemoteSourceError, match="302"):
                client.health()
        finally:
            srv.shutdown()
            srv.server_close()

    def test_response_size_is_capped(
        self, upstream: Registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(remote_mod, "MAX_JSON_BYTES", 10)
        with serving(upstream) as url, pytest.raises(RemoteSourceError, match="exceeds"):
            RemoteRegistry("up", url).search("graph")

    def test_unreachable_host(self) -> None:
        with pytest.raises(RemoteSourceError, match="cannot reach"):
            RemoteRegistry("x", "http://127.0.0.1:1", timeout=1).health()


class TestServerAuth:
    def test_token_required_and_checked(self, upstream: Registry) -> None:
        with serving(upstream, token="s3cret") as url:
            with pytest.raises(RemoteSourceError, match="401"):
                RemoteRegistry("up", url).health()
            with pytest.raises(RemoteSourceError, match="401"):
                RemoteRegistry("up", url, token="wrong").health()
            assert RemoteRegistry("up", url, token="s3cret").health()["ok"] is True

    def test_refuses_public_bind_without_token(self, upstream: Registry) -> None:
        with pytest.raises(RegistryError) as exc:
            make_server(upstream.root, "0.0.0.0", 0)  # noqa: S104
        assert exc.value.code == "AUTH_REQUIRED"
        srv = make_server(upstream.root, "0.0.0.0", 0, token="t")  # noqa: S104
        srv.server_close()

    def test_quarantined_versions_are_not_distributed(self, upstream: Registry) -> None:
        upstream.quarantine("core/graph-review@1.0.0", "bad")
        with serving(upstream) as url, pytest.raises(RemoteSourceError, match="quarantined"):
            RemoteRegistry("up", url).bundle(["ananke://skill/core/graph-review@1.0.0"])


class TestPolicyGate:
    def test_denied_by_default_and_overridable(self, tmp_path: Path) -> None:
        pol = RegistryPolicy(
            sources={
                "enterprise": SourceConfig(type="remote", url="https://r.example", token_env=None)
            }
        )
        with pytest.raises(PolicyViolationError) as exc:
            open_remote(pol, "enterprise")
        assert exc.value.code == "NETWORK_DENIED"
        assert open_remote(pol, "enterprise", cli_override=True).url == "https://r.example"

    def test_override_can_be_disabled(self) -> None:
        pol = RegistryPolicy(
            remote_sources=RemoteSourcesPolicy(allow_cli_override=False),
            sources={"enterprise": SourceConfig(type="remote", url="https://r.example")},
        )
        with pytest.raises(PolicyViolationError):
            open_remote(pol, "enterprise", cli_override=True)

    def test_unknown_or_disabled_source(self) -> None:
        with pytest.raises(RemoteSourceError, match="no enabled remote source"):
            open_remote(RegistryPolicy(), "enterprise")

    def test_token_comes_from_environment_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pol = _local_policy("http://127.0.0.1:9")
        monkeypatch.delenv("ANANKE_TEST_TOKEN", raising=False)
        with pytest.raises(RemoteSourceError, match="ANANKE_TEST_TOKEN"):
            open_remote(pol, "enterprise")
        monkeypatch.setenv("ANANKE_TEST_TOKEN", "abc")
        assert open_remote(pol, "enterprise")._token == "abc"
        assert "abc" not in dump_policy_toml(pol)  # the policy names the variable, never the value

    def test_statuses_and_local_resolution_ignore_remote(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        reg = Registry.for_project(proj, create=True, actor="t")
        reg.init()
        reg.close()
        pol = _local_policy("https://registry.example.internal")
        st = {s.name: s for s in source_statuses(proj, pol)}
        assert st["enterprise"].enabled and "pull-only" in st["enterprise"].note
        assert [n for n, _r in open_sources(proj, pol)] == ["project"]  # no network on resolve


class TestPull:
    def _client(self, tmp_path: Path, url: str) -> Registry:
        return open_registry(tmp_path / "down", _local_policy(url))

    def test_pull_with_dependencies_and_no_inherited_trust(
        self, tmp_path: Path, upstream: Registry
    ) -> None:
        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            res = pull(local, RemoteRegistry("enterprise", url), "core/coding-agent@^2")
            assert {i.action for i in res.items} == {"pulled"}
            assert {i.uri.rsplit("/", 1)[-1] for i in res.items} == {"coding-agent", "graph-review"}
            agent = local.exact_version("core/coding-agent@2.0.0")
            skill = local.exact_version("core/graph-review@1.1.0")
            assert agent.trust is TrustStatus.DISCOVERED and agent.channel == "candidate"
            assert skill.trust is TrustStatus.DISCOVERED  # approved upstream, not here
            assert (
                skill.digest_sha256
                == upstream.exact_version("core/graph-review@1.1.0").digest_sha256
            )
            assert [e for e in local.store.list_events() if e["event_type"] == "registry.pulled"]

            again = pull(local, RemoteRegistry("enterprise", url), "core/coding-agent@^2")
            assert {i.action for i in again.items} == {"already-present"}

    def test_no_deps_and_dry_run(self, tmp_path: Path, upstream: Registry) -> None:
        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            client = RemoteRegistry("enterprise", url)
            dry = pull(local, client, "core/coding-agent@^2", dry_run=True)
            assert {i.action for i in dry.items} == {
                "would-pull"
            } and local.store.count_versions() == 0
            only = pull(local, client, "core/coding-agent@^2", with_dependencies=False)
            assert [i.uri.rsplit("/", 1)[-1] for i in only.items] == ["coding-agent"]
            assert local.store.count_versions() == 1

    def test_remote_resolution_failure_is_reported(
        self, tmp_path: Path, upstream: Registry
    ) -> None:
        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            with pytest.raises(RemoteSourceError, match="could not resolve"):
                pull(local, RemoteRegistry("enterprise", url), "core/nope@^1")

    def test_digest_mismatch_aborts_before_writing(
        self, tmp_path: Path, upstream: Registry
    ) -> None:
        class Lying(RemoteRegistry):
            def resolve(self, ref: str, **kw: Any) -> dict[str, Any]:
                data = super().resolve(ref, **kw)
                data["nodes"][0]["ref"]["digest"] = "sha256:" + "0" * 64
                return data

        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            with pytest.raises(IntegrityError, match="digest mismatch"):
                pull(local, Lying("enterprise", url), "core/graph-review@1.0.0")
            assert local.store.count_versions() == 0

    def test_swapped_bundle_is_rejected(self, tmp_path: Path, upstream: Registry) -> None:
        class Swapping(RemoteRegistry):
            def bundle(self, uris: list[str]) -> bytes:
                return super().bundle(["ananke://skill/core/graph-review@1.1.0"])

        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            with pytest.raises(IntegrityError, match="differ from what was requested"):
                pull(local, Swapping("enterprise", url), "core/graph-review@1.0.0")
            assert local.store.count_versions() == 0

    def test_corrupt_bundle_is_rejected(self, tmp_path: Path, upstream: Registry) -> None:
        class Corrupt(RemoteRegistry):
            def bundle(self, uris: list[str]) -> bytes:
                return b"\x1f\x8b not really gzip"

        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            with pytest.raises(RegistryError):
                pull(local, Corrupt("enterprise", url), "core/graph-review@1.0.0")
            assert local.store.count_versions() == 0

    def test_local_content_conflict_is_refused(self, tmp_path: Path, upstream: Registry) -> None:
        with serving(upstream) as url:
            local = self._client(tmp_path, url)
            local.register(artifact("graph-review", "1.0.0", files={"README.md": b"different\n"}))
            with pytest.raises(IntegrityError, match="different content"):
                pull(local, RemoteRegistry("enterprise", url), "core/graph-review@1.0.0")

    def test_local_policy_still_applies_to_pulled_content(
        self, tmp_path: Path, upstream: Registry
    ) -> None:
        strict = _local_policy("http://127.0.0.1:9")
        strict.licenses.allow = ["MIT"]  # upstream is Apache-2.0
        with serving(upstream) as url:
            local = open_registry(tmp_path / "strict", strict)
            with pytest.raises(PolicyViolationError):
                pull(local, RemoteRegistry("enterprise", url), "core/graph-review@1.0.0")
            assert local.store.count_versions() == 0


class TestFederationCli:
    def test_list_search_pull(
        self, tmp_path: Path, upstream: Registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANANKE_TEST_TOKEN", "tok")
        runner = CliRunner()
        proj = tmp_path / "proj"
        args = ["--project", str(proj)]
        with serving(upstream, token="tok") as url:
            assert runner.invoke(registry_app, ["init", *args]).exit_code == 0
            policy_file = proj / ".ananke" / "registry" / "policy.toml"
            policy_file.write_text(dump_policy_toml(_local_policy(url)))

            listed = runner.invoke(registry_app, ["remote", "list", *args])
            assert listed.exit_code == 0 and "pull-only" in listed.output

            found = runner.invoke(registry_app, ["remote", "search", "graph", *args])
            assert found.exit_code == 0 and "graph-review" in found.output

            pulled = runner.invoke(registry_app, ["remote", "pull", "core/graph-review@^1", *args])
            assert pulled.exit_code == 0, pulled.output
            assert "discovered" in pulled.output
            shown = runner.invoke(registry_app, ["show", "core/graph-review@1.1.0", *args])
            assert shown.exit_code == 0

            monkeypatch.delenv("ANANKE_TEST_TOKEN")
            no_token = runner.invoke(registry_app, ["remote", "pull", "core/graph-review", *args])
            assert no_token.exit_code == 6

    def test_policy_denial_exit_code(self, tmp_path: Path) -> None:
        runner = CliRunner()
        proj = tmp_path / "proj"
        args = ["--project", str(proj)]
        runner.invoke(registry_app, ["init", *args])
        pol = RegistryPolicy(
            sources={"enterprise": SourceConfig(type="remote", url="https://r.example")}
        )
        (proj / ".ananke" / "registry" / "policy.toml").write_text(dump_policy_toml(pol))
        res = runner.invoke(registry_app, ["remote", "search", "x", *args])
        assert res.exit_code == 3

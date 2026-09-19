"""Pull-only federation client (spec §98).

A remote registry is another Ananke registry exposed with ``ananke registry serve``. The client
is deliberately small and defensive:

* **Gated.** Network access needs ``[remote_sources] enterprise|public = true`` (or the CLI
  override, unless policy disables overrides). Nothing else in the registry touches the network.
* **Transport.** ``https`` only, except loopback ``http`` for local development. Redirects are
  refused, timeouts and response sizes are capped, the bearer token comes from an *environment
  variable named in policy* (never stored) and is only ever sent over a permitted transport.
* **Content is verified, not trusted.** Every pulled version is checked against the digest the
  remote advertised *and* re-validated by the normal import path (checksums, allow-listed archive
  members, secrets, licence and permission policy). It arrives as ``discovered`` in the
  ``candidate`` channel: promotion and trust remain a local decision. Signatures travel with the
  version and are verified against *this* registry's trusted keys.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import (
    IntegrityError,
    PolicyViolationError,
    RegistryError,
    RemoteSourceError,
)
from ananke.plexus.registry.policy import RegistryPolicy, SourceConfig
from ananke.plexus.registry.portable import bundle_digests, import_registry
from ananke.plexus.registry.registry import Registry

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
_LOCAL_NAMES = {"localhost"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # a redirect could carry the bearer token to another host


def _is_loopback(host: str) -> bool:
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise RemoteSourceError(f"invalid registry url {url!r}")
    if parts.username or parts.password:
        raise RemoteSourceError("credentials in the url are not allowed (use token_env)")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise RemoteSourceError(
            "plain http is only allowed for loopback; use https for remote registries"
        )
    return url.rstrip("/")


class RemoteRegistry:
    def __init__(
        self,
        name: str,
        url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.name = name
        self.url = validate_url(url)
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _get(self, route: str, params: list[tuple[str, str]] | None, limit: int) -> bytes:
        query = urllib.parse.urlencode(params or [])
        target = f"{self.url}/api/v1/{route}" + (f"?{query}" if query else "")
        request = urllib.request.Request(target, method="GET")  # noqa: S310 - scheme validated
        request.add_header("Accept", "application/json, application/gzip")
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                data: bytes = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            detail = ""
            with contextlib.suppress(ValueError, KeyError, TypeError):
                detail = json.loads(exc.read(4096))["error"]["message"]
            raise RemoteSourceError(
                f"{self.name}: HTTP {exc.code}" + (f" — {detail}" if detail else "")
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteSourceError(f"{self.name}: cannot reach {self.url}: {exc}") from None
        if len(data) > limit:
            raise RemoteSourceError(f"{self.name}: response exceeds {limit} bytes")
        return data

    def _json(self, route: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        try:
            body = json.loads(self._get(route, params, MAX_JSON_BYTES))
        except ValueError as exc:
            raise RemoteSourceError(f"{self.name}: response is not JSON") from exc
        if not isinstance(body, dict):
            raise RemoteSourceError(f"{self.name}: unexpected response shape")
        return body

    def health(self) -> dict[str, Any]:
        return self._json("health")

    def search(
        self, query: str, *, kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        params = [("q", query), ("limit", str(limit))] + ([("kind", kind)] if kind else [])
        results = self._json("search", params).get("results", [])
        return [r for r in results if isinstance(r, dict)]

    def resolve(
        self, ref: str, *, kind: str | None = None, runtime: str | None = None
    ) -> dict[str, Any]:
        params = [("ref", ref)]
        if kind:
            params.append(("kind", kind))
        if runtime:
            params.append(("runtime", runtime))
        return self._json("resolve", params)

    def bundle(self, uris: list[str]) -> bytes:
        return self._get("bundle", [("uri", u) for u in uris], MAX_BUNDLE_BYTES)


def open_remote(
    policy: RegistryPolicy,
    name: str,
    *,
    cli_override: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> RemoteRegistry:
    cfg: SourceConfig | None = policy.sources.get(name)
    if cfg is None or cfg.type != "remote" or not cfg.enabled:
        raise RemoteSourceError(
            f"no enabled remote source named {name!r} (configure [sources.{name}] in policy.toml)"
        )
    if not cfg.url:
        raise RemoteSourceError(f"remote source {name!r} has no url")
    if not policy.remote_registry_allowed(name, cli_override):
        raise PolicyViolationError(
            f"network access to the {name} registry is denied by policy "
            "([remote_sources]); set it to true or pass --allow-network",
            code="NETWORK_DENIED",
        )
    token = None
    if cfg.token_env:
        token = os.environ.get(cfg.token_env)
        if not token:
            raise RemoteSourceError(
                f"environment variable {cfg.token_env} (token_env for {name}) is not set"
            )
    return RemoteRegistry(name, cfg.url, token=token, timeout=timeout)


class PullItem(BaseModel):
    uri: str
    version: str
    digest: str
    action: str  # pulled | would-pull | already-present


class PullResult(BaseModel):
    source: str
    requested: str
    items: list[PullItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False


def _node_id(node: dict[str, Any]) -> tuple[str, str, str]:
    ref = node["ref"]
    return str(ref["uri"]), str(ref["version"]), str(ref["digest"]).removeprefix("sha256:")


def pull(
    registry: Registry,
    remote: RemoteRegistry,
    ref: str,
    *,
    kind: str | None = None,
    runtime: str | None = None,
    with_dependencies: bool = True,
    dry_run: bool = False,
) -> PullResult:
    """Fetch ``ref`` (and, by default, its resolved dependency closure) into the local registry."""
    resolved = remote.resolve(ref, kind=kind, runtime=runtime)
    if not resolved.get("ok"):
        raise RemoteSourceError(
            f"{remote.name} could not resolve {ref!r}:\n{resolved.get('explanation', '')}"
        )
    nodes = resolved.get("nodes") or []
    if not nodes:
        raise RemoteSourceError(f"{remote.name} resolved {ref!r} to nothing")
    wanted = [_node_id(n) for n in nodes if with_dependencies or n.get("root")]
    result = PullResult(source=remote.name, requested=ref, dry_run=dry_run)

    missing: list[tuple[str, str, str]] = []
    for uri, version, digest in wanted:
        full = f"{uri}@{version}"
        try:
            existing = registry.exact_version(full)
        except RegistryError:
            missing.append((uri, version, digest))
            continue
        if existing.digest_sha256 != digest:
            raise IntegrityError(
                f"{full} exists locally with different content than {remote.name} advertises "
                "(versions are immutable; refusing to overwrite)"
            )
        result.items.append(
            PullItem(uri=uri, version=version, digest=digest, action="already-present")
        )

    if missing and not dry_run:
        blob = remote.bundle([f"{u}@{v}" for u, v, _ in missing])
        with tempfile.TemporaryDirectory(prefix="ananke-pull-") as tmp:
            archive = Path(tmp) / "bundle.tar.gz"
            archive.write_bytes(blob)
            advertised = {f"{u}@{v}": d for u, v, d in missing}
            actual = bundle_digests(archive)  # verifies every checksum first
            if set(actual) != set(advertised):
                raise IntegrityError(
                    "bundle contents differ from what was requested: "
                    f"expected {sorted(advertised)}, got {sorted(actual)}"
                )
            for key, digest in advertised.items():
                if actual[key] != digest:
                    raise IntegrityError(
                        f"digest mismatch for {key}: remote advertised {digest}, bundle has {actual[key]}"
                    )
            imported = import_registry(registry, archive, preserve_trust=False)
        result.warnings.extend(imported.warnings)
        registry.emit(
            "registry.pulled",
            None,
            {
                "source": remote.name,
                "url": remote.url,
                "versions": [f"{u}@{v}" for u, v, _ in missing],
            },
        )
    for uri, version, digest in missing:
        result.items.append(
            PullItem(
                uri=uri,
                version=version,
                digest=digest,
                action="would-pull" if dry_run else "pulled",
            )
        )
    return result

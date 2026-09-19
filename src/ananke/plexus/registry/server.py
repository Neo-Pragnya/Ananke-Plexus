"""Optional local registry server (spec §82, §83).

Serves the generated static docs and a small read-only JSON API. Write operations are
disabled: every method other than GET/HEAD gets ``405``. Binds to loopback by default and
opens a fresh registry connection per request (SQLite connections are not shared across
threads). Static HTML remains the primary documentation artifact — this server is a
convenience.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ananke.plexus.registry.errors import NotFoundError, RegistryError
from ananke.plexus.registry.models import ArtifactKind, LifecycleStatus, ResolutionMode
from ananke.plexus.registry.portable import export_bundle
from ananke.plexus.registry.present import record_to_dict
from ananke.plexus.registry.registry import Registry
from ananke.plexus.registry.resolver import (
    Requirement,
    ResolutionEnvironment,
    ResolutionOptions,
    Resolver,
)
from ananke.plexus.registry.search import search

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}
_MAX_QUERY = 2048
_MAX_BUNDLE = 50
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "AnankeRegistry/1"
    registry_root: Path
    docs_dir: Path
    project_root: Path | None
    token: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    # ---- helpers
    def _send(self, status: int, body: bytes, content_type: str, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in _SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, head_only: bool = False) -> None:
        self._send(
            status,
            json.dumps(payload, indent=2, default=str).encode(),
            "application/json; charset=utf-8",
            head_only,
        )

    def _error(self, status: int, code: str, message: str, head_only: bool = False) -> None:
        self._json(status, {"error": {"code": code, "message": message}}, head_only)

    # ---- verbs
    def do_GET(self) -> None:
        self._dispatch(False)

    def do_HEAD(self) -> None:
        self._dispatch(True)

    def _reject(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        body = json.dumps(
            {"error": {"code": "READ_ONLY", "message": "the registry HTTP server is read-only"}}
        ).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in _SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject

    def _authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode(), self.token.encode()
        )

    def _dispatch(self, head_only: bool) -> None:
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="ananke-registry"')
            body = json.dumps(
                {"error": {"code": "UNAUTHORIZED", "message": "bearer token required"}}
            ).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in _SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return
        parts = urlsplit(self.path)
        if len(parts.query) > _MAX_QUERY or len(self.path) > _MAX_QUERY:
            self._error(414, "URI_TOO_LONG", "request URI too long", head_only)
            return
        path = unquote(parts.path)
        if path.startswith("/api/v1/"):
            try:
                self._api(path[len("/api/v1/") :].strip("/"), parse_qs(parts.query), head_only)
            except NotFoundError as exc:
                self._error(404, exc.code, exc.message, head_only)
            except RegistryError as exc:
                self._error(400, exc.code, exc.message, head_only)
            except ValueError as exc:
                self._error(400, "INVALID_REQUEST", str(exc), head_only)
            return
        self._static(path, head_only)

    # ---- API
    def _api(self, route: str, q: dict[str, list[str]], head_only: bool) -> None:
        def first(key: str, default: str = "") -> str:
            return q.get(key, [default])[0]

        with Registry.open(self.registry_root, project_root=self.project_root) as reg:
            if route == "health":
                self._json(
                    200,
                    {"ok": True, "registry_id": reg.registry_id, "snapshot": reg.snapshot_id()},
                    head_only,
                )
            elif route == "artifacts":
                kind = first("kind") or None
                items = [
                    {
                        "kind": a.kind.value,
                        "namespace": a.namespace,
                        "name": a.name,
                        "uri": a.uri,
                        "versions": a.version_count,
                        "latest": a.latest_version,
                    }
                    for a in reg.list_artifacts(kind)
                ]
                self._json(200, {"artifacts": items}, head_only)
            elif route.startswith("artifacts/"):
                segs = route.split("/")
                if len(segs) not in {4, 5} or (len(segs) == 5 and segs[4] != "versions"):
                    raise NotFoundError(f"unknown route {route!r}")
                kind, ns, name = ArtifactKind(segs[1]), segs[2], segs[3]
                versions = reg.store.versions_of(kind, ns, name)
                if not versions:
                    raise NotFoundError(f"{kind.value}/{ns}/{name} not found")
                if len(segs) == 5:
                    self._json(
                        200,
                        {"versions": [record_to_dict(v, None, detail=False) for v in versions]},
                        head_only,
                    )
                else:
                    self._json(200, record_to_dict(versions[-1], reg), head_only)
            elif route == "search":
                hits = search(
                    reg,
                    first("q"),
                    kind=first("kind") or None,
                    capability=first("capability") or None,
                    runtime=first("runtime") or None,
                    trust=first("trust") or None,
                    channel=first("channel") or None,
                    limit=min(int(first("limit", "20") or 20), 100),
                )
                self._json(200, {"results": [h.model_dump(mode="json") for h in hits]}, head_only)
            elif route == "resolve":
                ref = first("ref")
                if not ref:
                    raise RegistryError("ref is required")
                env = ResolutionEnvironment.detect(runtime=first("runtime") or None)
                try:
                    opts = (
                        ResolutionOptions(mode=ResolutionMode(first("mode")))
                        if first("mode")
                        else ResolutionOptions()
                    )
                except ValueError as exc:
                    raise RegistryError(f"invalid resolution mode: {first('mode')!r}") from exc
                res = Resolver(reg, env=env, options=opts).resolve(
                    Requirement.parse(ref, first("kind") or None)
                )
                self._json(
                    200,
                    {
                        "ok": res.ok,
                        "explanation": res.explain(),
                        "selected": res.selected.model_dump() if res.selected else None,
                        **res.model_dump(mode="json"),
                    },
                    head_only,
                )
            elif route == "bundle":
                uris = q.get("uri", [])
                if not uris or len(uris) > _MAX_BUNDLE:
                    raise RegistryError(f"provide between 1 and {_MAX_BUNDLE} `uri` parameters")
                for uri in uris:
                    if reg.exact_version(uri).lifecycle is LifecycleStatus.QUARANTINED:
                        raise RegistryError(f"{uri} is quarantined and is not distributed")
                self._send(200, export_bundle(reg, uris), "application/gzip", head_only)
            elif route == "capabilities":
                idx = reg.store.capability_index()
                self._json(
                    200,
                    {
                        "capabilities": {
                            c: sorted({reg.store.record_by_id(v).version_uri for v in vids})
                            for c, vids in sorted(idx.items())
                        }
                    },
                    head_only,
                )
            else:
                raise NotFoundError(f"unknown route {route!r}")

    # ---- static docs
    def _static(self, path: str, head_only: bool) -> None:
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        base = self.docs_dir.resolve()
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            self._error(403, "FORBIDDEN", "path escapes the docs directory", head_only)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._error(
                404, "NOT_FOUND", "page not found (run `ananke registry docs build`)", head_only
            )
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, head_only)


def make_server(
    registry_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    project_root: Path | None = None,
    docs_dir: Path | None = None,
    token: str | None = None,
) -> ThreadingHTTPServer:
    if token is None and host not in _LOOPBACK:
        raise RegistryError(
            f"refusing to serve on {host!r} without a bearer token (use --token-env); "
            "an unauthenticated registry may only listen on loopback",
            code="AUTH_REQUIRED",
        )
    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "registry_root": registry_root,
            "docs_dir": docs_dir or (registry_root / "docs"),
            "project_root": project_root,
            "token": token,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    registry_root: Path, host: str = "127.0.0.1", port: int = 8765, **kwargs: Any
) -> None:  # pragma: no cover - blocking
    server = make_server(registry_root, host, port, **kwargs)
    try:
        server.serve_forever()
    finally:
        server.server_close()

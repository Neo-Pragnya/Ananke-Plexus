"""Site builder: multi-page site with incremental rebuilds, and the single-file dump."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ananke.plexus.registry.docsgen.assets import CSS, JS
from ananke.plexus.registry.docsgen.model import (
    RegistryDocumentationModel,
    build_documentation_model,
)
from ananke.plexus.registry.docsgen.render import TEMPLATE_VERSION, PageSpec, Site
from ananke.plexus.registry.hashing import canonical_json, sha256_hex

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

CACHE_NAME = ".build-cache.json"


class BuildResult(BaseModel):
    out_dir: str
    total_pages: int
    pages_written: int
    pages_skipped: int
    pages_removed: int = 0
    single_file: str | None = None
    snapshot: str = ""


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _load_cache(path: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("template") != TEMPLATE_VERSION:
        return {}
    pages = raw.get("pages", {})
    return pages if isinstance(pages, dict) else {}


def _fingerprint(site: Site, uri: str) -> dict[str, object]:
    """What *other* pages need to know about a referenced artifact (links, labels, graph edges)."""
    art = site.by_uri[uri]
    default = art.version(art.default_version)
    return {
        "uri": uri,
        "kind": art.kind,
        "title": art.title,
        "default": art.default_version,
        "deps": [(d.type, d.label, d.target_uri) for d in default.dependencies],
        "caps": default.capabilities,
    }


def _spec_digest(
    spec: PageSpec, model: RegistryDocumentationModel, full_digest: str, site: Site
) -> str:
    if spec.template in {"artifact", "version", "compare"}:
        own_uri = spec.ctx["a"].uri
        parts: list[object] = [site.by_uri[own_uri].model_dump_json()]
        parts += [
            _fingerprint(site, u) for u in sorted(spec.refs) if u in site.by_uri and u != own_uri
        ]
        parts += [
            {"uri": u, "kind": site.by_uri[u].kind, "title": site.by_uri[u].title}
            for u in sorted(spec.link_refs)
            if u in site.by_uri
        ]
        if spec.template == "compare":
            parts.append(str(spec.ctx["d"].model_dump_json()))
        payload = canonical_json(
            {"t": TEMPLATE_VERSION, "k": spec.key, "nav": model.kinds_present, "parts": parts}
        )
    else:
        payload = canonical_json({"t": TEMPLATE_VERSION, "k": spec.key, "full": full_digest})
    return sha256_hex(payload)


def build_site(
    registry: Registry,
    out_dir: Path | None = None,
    *,
    incremental: bool = True,
    with_diffs: bool = True,
    emit: bool = True,
) -> BuildResult:
    """Generate the multi-page static site. Unchanged pages are not re-written (spec §111)."""
    model = build_documentation_model(registry, with_diffs=with_diffs)
    site = Site(model, single=False)
    out = out_dir or (registry.root / "docs")
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / CACHE_NAME
    old = _load_cache(cache_path) if incremental else {}
    full_digest = sha256_hex(model.model_dump_json().encode())
    new_cache: dict[str, dict[str, str]] = {}
    written = skipped = 0

    for spec in site.pages():
        digest = _spec_digest(spec, model, full_digest, site)
        target = out / spec.key
        prev = old.get(spec.key)
        if (
            prev
            and prev.get("src") == digest
            and target.is_file()
            and sha256_hex(target.read_bytes()) == prev.get("out")
        ):
            new_cache[spec.key] = prev
            skipped += 1
            continue
        html = site.wrap(spec, site.render_body(spec)).encode("utf-8")
        _atomic_write(target, html)
        new_cache[spec.key] = {"src": digest, "out": sha256_hex(html)}
        written += 1

    for name, data in (("assets/style.css", CSS), ("assets/app.js", JS)):
        target = out / name
        blob = data.encode("utf-8")
        if not target.is_file() or target.read_bytes() != blob:
            _atomic_write(target, blob)
    _atomic_write(
        out / "search-index.json",
        json.dumps(model.search_index, indent=1, sort_keys=True).encode("utf-8"),
    )

    removed = 0
    for key in set(old) - set(new_cache):
        stale = out / key
        if stale.is_file():
            stale.unlink()
            removed += 1
            with contextlib.suppress(OSError):
                stale.parent.rmdir()
    _atomic_write(
        cache_path,
        json.dumps(
            {"template": TEMPLATE_VERSION, "pages": new_cache}, indent=1, sort_keys=True
        ).encode(),
    )
    result = BuildResult(
        out_dir=str(out),
        total_pages=len(new_cache),
        pages_written=written,
        pages_skipped=skipped,
        pages_removed=removed,
        snapshot=model.snapshot,
    )
    if emit:
        registry.emit(
            "docs.generated",
            None,
            {"pages": len(new_cache), "written": written, "skipped": skipped},
        )
    return result


def build_dump(
    registry: Registry, output: Path | None = None, *, with_diffs: bool = True, emit: bool = True
) -> Path:
    """One self-contained offline HTML file with every artifact, version, diagram and search (spec §73, §109)."""
    model = build_documentation_model(registry, with_diffs=with_diffs)
    html = Site(model, single=True).assemble_single()
    target = output or (registry.root / "docs" / "registry.html")
    _atomic_write(target, html.encode("utf-8"))
    if emit:
        registry.emit("docs.generated", None, {"single_file": target.name, "bytes": len(html)})
    return target

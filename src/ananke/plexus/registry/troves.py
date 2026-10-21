"""Troves: installed capability libraries (such as Dex Trove) that Ananke learns when you switch them on.

A *trove* is any installed distribution that exposes an ``ananke.troves`` entry point whose callable returns the
trove root: a folder of artifacts, each with an ``ananke.yaml`` manifest. A ``catalog.json`` at the root (Dex
Trove's format: items with ``kind``, ``namespace``, ``name``, ``dir`` and optional ``secret_flags``) lets Ananke
learn a filtered slice; without one, the whole root is learned.

Switch one on in ``.ananke/config.toml`` (or with ``ananke registry troves enable``)::

    [troves.dex-trove]
    enabled = true
    kinds = ["skill", "agent"]      # optional; empty = every kind
    namespaces = ["neo-pragnya"]    # optional; empty = every namespace
    include_flagged = false         # items whose placeholders trip the secret scanner

then ``ananke registry troves sync``. Learning goes through the normal importer, policy and secret scanner,
so a trove gets no special trust: it is only a convenient, versioned source.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from ananke.plexus.config.models import TroveConfig

GROUP = "ananke.troves"


@dataclass
class TroveInfo:
    name: str
    distribution: str
    version: str
    root: Path | None
    items: int = 0
    error: str = ""


@dataclass
class TroveSyncResult:
    name: str
    selected: int = 0
    registered: int = 0
    unchanged: int = 0
    denied: int = 0
    other: int = 0
    error: str = ""
    denied_examples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


def _catalog(root: Path) -> list[dict[str, Any]] | None:
    path = root / "catalog.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data) if isinstance(data, dict) else data
    return [i for i in items if isinstance(i, dict)]


def discover() -> list[TroveInfo]:
    """Every installed trove, in entry-point name order."""
    found: list[TroveInfo] = []
    for ep in sorted(entry_points(group=GROUP), key=lambda e: e.name):
        dist = ep.dist.name if ep.dist else ""
        version = ep.dist.version if ep.dist else ""
        try:
            target = ep.load()
            root = Path(target() if callable(target) else target)
            items = _catalog(root)
            found.append(
                TroveInfo(ep.name, dist, version, root, len(items) if items is not None else 0)
            )
        except Exception as exc:  # a broken trove must not break the CLI
            found.append(
                TroveInfo(ep.name, dist, version, None, error=f"{type(exc).__name__}: {exc}")
            )
    return found


def select(root: Path, cfg: TroveConfig) -> list[dict[str, Any]] | None:
    """Catalog items matching the config, or ``None`` to learn the whole root (no catalog, no filters)."""
    items = _catalog(root)
    if items is None:
        return None
    chosen = [
        i
        for i in items
        if (not cfg.kinds or i.get("kind") in cfg.kinds)
        and (not cfg.namespaces or i.get("namespace") in cfg.namespaces)
        and (cfg.include_flagged or not i.get("secret_flags"))
    ]
    return None if len(chosen) == len(items) else chosen


def sync(
    registry: Any, configs: dict[str, TroveConfig], *, dry_run: bool = False
) -> list[TroveSyncResult]:
    """Learn every enabled trove into ``registry``."""
    from ananke.plexus.registry.learn import learn

    installed = {t.name: t for t in discover()}
    results: list[TroveSyncResult] = []
    for name, cfg in sorted(configs.items()):
        if not cfg.enabled:
            continue
        res = TroveSyncResult(name)
        info = installed.get(name)
        if info is None or info.root is None:
            res.error = (
                info.error if info else f"not installed (no '{GROUP}' entry point named {name!r})"
            )
            results.append(res)
            continue
        chosen = select(info.root, cfg)
        with tempfile.TemporaryDirectory(prefix="ananke-trove-") as tmp:
            if chosen is None:
                target = info.root
                res.selected = info.items
            else:
                target = Path(tmp) / name
                for item in chosen:
                    shutil.copytree(info.root / item["dir"], target / item["dir"])
                res.selected = len(chosen)
            if res.selected == 0 and chosen is not None:
                res.error = "the filters select no items"
                results.append(res)
                continue
            report = learn(registry, str(target), dry_run=dry_run)
        for cand in report.candidates:
            if cand.action == "registered" or cand.action == "dry-run":
                res.registered += 1
            elif cand.action in ("unchanged", "skipped"):
                res.unchanged += 1
            elif cand.action == "denied":
                res.denied += 1
                if len(res.denied_examples) < 3:
                    res.denied_examples.append(f"{cand.uri}: {cand.message}"[:160])
            else:
                res.other += 1
                if len(res.denied_examples) < 3:
                    res.denied_examples.append(
                        f"{cand.uri or cand.source}: {cand.action}: {cand.message}"[:160]
                    )
        results.append(res)
    return results


# ---------------------------------------------------------------------------------------------- config editing
def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_config(config_path: Path, name: str, cfg: TroveConfig) -> None:
    """Replace (or append) the ``[troves.<name>]`` table, leaving the rest of the file untouched."""
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    header = f"[troves.{name}]" if re.fullmatch(r"[A-Za-z0-9_-]+", name) else f'[troves."{name}"]'
    body = "\n".join(f"{k} = {_toml_value(v)}" for k, v in cfg.model_dump().items())
    block = f"{header}\n{body}\n"
    pattern = re.compile(
        rf"^\[troves\.(?:{re.escape(name)}|\"{re.escape(name)}\")\]\n(?:(?!\[).*\n?)*", re.M
    )
    if pattern.search(text):
        text = pattern.sub(block + "\n", text, count=1)
    else:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + block
    config_path.write_text(text.rstrip() + "\n", encoding="utf-8")

"""Repo intelligence: what a repository contains and how it connects, for products to present.

One call reads a repository and answers in ``ananke-repo-intel/1`` (docs/reference/repo-intelligence.md). Each section comes
from its own analyzer. Analyzers read files and run nothing; the only network use is the CVE lookup, and only when asked.

A section always has ``status`` and ``limits``, because unknown isn't zero: no coverage file is ``not_found``, never 0%, and a
skipped CVE scan is ``not_scanned``, never clean. Everything found cites its evidence as ``path:line``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ananke.plexus.intel import (
    api,
    calm,
    coverage,
    data_model,
    dependencies,
    findings,
    flows,
    notebooks,
    overview,
    quality,
)
from ananke.plexus.intel.context import RepoContext

SCHEMA = "ananke-repo-intel/1"
# Order matters: flows reads what data and api found, and calm what overview found.
SECTIONS = (
    ("overview", overview.analyze),
    ("data", data_model.analyze),
    ("api", api.analyze),
    ("flows", flows.analyze),
    ("calm", calm.analyze),
    ("quality", quality.analyze),
    ("coverage", coverage.analyze),
    ("dependencies", dependencies.analyze),
    ("notebooks", notebooks.analyze),
    ("findings", findings.analyze),
)


def analyze(root: Path, *, check_vulnerabilities: bool = False) -> dict[str, Any]:
    """Every section for ``root``. One analyzer failing leaves its section ``failed``, and the rest still answer."""
    ctx = RepoContext.open(root, check_vulnerabilities=check_vulnerabilities)
    for name, analyzer in SECTIONS:
        try:
            ctx.sections[name] = analyzer(ctx)
        except Exception as exc:
            ctx.sections[name] = {
                "status": "failed",
                "limits": [f"this analysis failed: {type(exc).__name__}: {exc}"],
            }
    return {
        "schema": SCHEMA,
        "sections": ctx.sections,
        "content_digest": digest(ctx.sections),
    }


def digest(sections: dict[str, Any]) -> str:
    """Identifies the findings: the same sources give the same digest, whenever they were analysed."""
    payload = json.dumps(sections, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


__all__ = ["SCHEMA", "RepoContext", "analyze", "digest"]

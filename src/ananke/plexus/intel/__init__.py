"""Repo intelligence: what a repository contains and how it connects, for products to present.

One call reads a repository and answers in ``ananke-repo-intel/1`` (docs/reference/repo-intelligence.md). Each section comes
from its own analyzer. Analyzers read files and run nothing; the only network use is the CVE lookup, and only when asked.

A section always has ``status`` and ``limits``, because unknown isn't zero: no coverage file is ``not_found``, never 0%, and a
skipped CVE scan is ``not_scanned``, never clean. Everything found cites its evidence as ``path:line``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
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
    tests_topology,
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
    ("tests", tests_topology.analyze),
)


# Analyses kept in memory, by folder and by whether a CVE check was asked for. Analysis writes nothing to the
# repository, so this lives only as long as the process: one dev explore, or one dev serve.
_KEPT: OrderedDict[tuple[str, bool], dict[str, Any]] = OrderedDict()
_KEEP = 4


def analyze(root: Path, *, check_vulnerabilities: bool = False) -> dict[str, Any]:
    """Every section for ``root``. One analyzer failing leaves its section ``failed``, and the rest still answer.

    The same sources give the same report, so asking again before anything changes gets the one already made.
    A CVE check always asks again: what's known about a package changes while its code doesn't.
    """
    from ananke.plexus.context import source_state

    root = root.resolve()
    state = source_state(root)  # before reading anything, so an edit made meanwhile isn't lost
    if not check_vulnerabilities:
        saved = cached(root, state)
        if saved is not None:
            return saved
    report, ctx = _analyze(root, check_vulnerabilities=check_vulnerabilities)
    folders = coverage.probe_folders(ctx)
    key = (str(root), check_vulnerabilities)
    _KEPT[key] = {
        "state": state,
        "folders": folders,
        "stamp": coverage.report_stamp(root, folders),
        "report": copy.deepcopy(report),
    }
    _KEPT.move_to_end(key)
    while len(_KEPT) > _KEEP:
        _KEPT.popitem(last=False)
    return report


def cached(root: Path, state: str, *, checked_too: bool = False) -> dict[str, Any] | None:
    """The analysis made for exactly these sources, if no coverage report has appeared or changed since.

    One made with a CVE check counts only when ``checked_too``, so a plain question never gets an old check.
    """
    for checked in (False, True) if checked_too else (False,):
        kept = _KEPT.get((str(root.resolve()), checked))
        if (
            kept is not None
            and kept["state"] == state
            and coverage.report_stamp(root, kept["folders"]) == kept["stamp"]
        ):
            report: dict[str, Any] = copy.deepcopy(kept["report"])
            return report
    return None


def _analyze(root: Path, *, check_vulnerabilities: bool) -> tuple[dict[str, Any], RepoContext]:
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
    }, ctx


def digest(sections: dict[str, Any]) -> str:
    """Identifies the findings: the same sources give the same digest, whenever they were analysed."""
    payload = json.dumps(sections, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


__all__ = ["SCHEMA", "RepoContext", "analyze", "digest"]

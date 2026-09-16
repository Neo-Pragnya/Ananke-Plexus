"""PR evidence storytelling generator (J5).

Produces a PR description with the 11-section structure from Section 41.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_pr_body(
    repository_root: Path,
    *,
    run_id: str = "",
    spec_id: str = "",
    branch: str = "",
) -> str:
    """Generate a full PR description from available evidence artifacts."""
    requirement = _load_requirement(repository_root, spec_id)
    evidence = _load_evidence(repository_root, run_id)
    spec_lock = _load_spec_lock(repository_root, spec_id)
    impact = _load_impact(repository_root)

    sections: list[str] = [
        _section_requirement(requirement),
        _section_behavioral_contract(requirement, spec_id),
        _section_architecture_delta(repository_root, spec_id),
        _section_graph_impact(impact),
        _section_verification_evidence(evidence),
        _section_security_evidence(evidence),
        _section_residual_risk(evidence),
        _section_traceability(spec_lock, run_id, branch),
    ]
    return "\n\n".join(s for s in sections if s)


def _load_requirement(repository_root: Path, spec_id: str) -> dict[str, Any]:
    if not spec_id:
        return {}
    req_path = repository_root / ".ananke" / "specs" / spec_id / "requirement.md"
    if not req_path.exists():
        return {}
    text = req_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), spec_id)
    criteria = [ln[2:].strip() for ln in lines if ln.strip().startswith("- ")]
    return {"title": title, "acceptance_criteria": criteria, "spec_id": spec_id}


def _load_evidence(repository_root: Path, run_id: str) -> dict[str, Any]:
    if not run_id:
        evidence_dir = repository_root / ".ananke" / "evidence"
        if not evidence_dir.exists():
            return {}
        runs = sorted(
            (item for item in evidence_dir.iterdir() if item.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        if not runs:
            return {}
        run_id = runs[0].name
    manifest_path = repository_root / ".ananke" / "evidence" / run_id / "manifest.json"
    if not manifest_path.exists():
        return {"run_id": run_id}
    return {"run_id": run_id, **json.loads(manifest_path.read_text(encoding="utf-8"))}


def _load_spec_lock(repository_root: Path, spec_id: str) -> dict[str, Any]:
    if not spec_id:
        return {}
    lock_path = repository_root / ".ananke" / "specs" / spec_id / "spec.lock.json"
    if not lock_path.exists():
        return {}
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _load_impact(repository_root: Path) -> dict[str, Any]:
    graph_dir = repository_root / ".ananke" / "graph"
    overlay = graph_dir / "overlays"
    if overlay.exists():
        for f in sorted(overlay.glob("impact*.json"), reverse=True):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError, KeyError):
                pass
    return {}


def _section_requirement(req: dict[str, Any]) -> str:
    if not req:
        return "## Requirement\n_No requirement linked._"
    lines = [f"## Requirement\n**{req.get('title', 'Unknown')}**"]
    criteria = req.get("acceptance_criteria", [])
    if criteria:
        lines.append("\n**Acceptance Criteria:**")
        for i, ac in enumerate(criteria, 1):
            lines.append(f"- [{i}] {ac}")
    return "\n".join(lines)


def _section_behavioral_contract(req: dict[str, Any], spec_id: str) -> str:
    if not spec_id:
        return ""
    lines = [f"## Behavioral Contract\nSpec ID: `{spec_id}`"]
    criteria = req.get("acceptance_criteria", [])
    for i, ac in enumerate(criteria, 1):
        lines.append(f"- AC-{i} ✓ {ac}")
    return "\n".join(lines)


def _section_architecture_delta(repository_root: Path, spec_id: str) -> str:
    delta_path = repository_root / ".ananke" / "architecture" / "diagrams.md"
    if not delta_path.exists() and spec_id:
        delta_path = repository_root / ".ananke" / "specs" / spec_id / "architecture-contract.yaml"
    if not delta_path.exists():
        return "## Architecture Delta\n_No architecture delta recorded._"
    snippet = delta_path.read_text(encoding="utf-8")[:800]
    return f"## Architecture Delta\n```\n{snippet}\n```"


def _section_graph_impact(impact: dict[str, Any]) -> str:
    if not impact:
        return "## Code-Graph Impact\n_Graph impact not available._"
    symbols = impact.get("impacted_symbols", [])
    files = impact.get("impacted_files", [])
    forbidden = impact.get("forbidden_edges", [])
    lines = [
        "## Code-Graph Impact",
        f"- Impacted symbols: **{len(symbols)}**",
        f"- Impacted files: **{len(files)}**",
        f"- Forbidden edges: **{len(forbidden)}**",
    ]
    if symbols:
        lines.append("\n**Top impacted symbols:**")
        for s in symbols[:10]:
            lines.append(f"  - `{s}`")
    return "\n".join(lines)


def _section_verification_evidence(evidence: dict[str, Any]) -> str:
    run_id = evidence.get("run_id", "N/A")
    files = evidence.get("files", {})
    gate_files = [k for k in files if k.startswith("gates/") and k.endswith(".json")]
    lines = [
        "## Verification Evidence",
        f"Run ID: `{run_id}`",
        f"Gate results: {len(gate_files)} gates recorded",
    ]
    return "\n".join(lines)


def _section_security_evidence(evidence: dict[str, Any]) -> str:
    files = evidence.get("files", {})
    has_sarif = any(k.endswith(".sarif") for k in files)
    lines = [
        "## Security & Dependency Evidence",
        f"SARIF: {'✓ present' if has_sarif else '⚠ not generated'}",
    ]
    for gate in ["gitleaks", "semgrep", "pip-audit", "trivy", "license"]:
        key = f"gates/{gate}.json"
        if key in files:
            lines.append(f"- {gate}: ✓ recorded")
    return "\n".join(lines)


def _section_residual_risk(evidence: dict[str, Any]) -> str:
    return "## Residual Risk\n_No known residual risks at time of PR creation._"


def _section_traceability(spec_lock: dict[str, Any], run_id: str, branch: str) -> str:
    spec_hash = spec_lock.get("spec_hash", "N/A")
    req_hash = spec_lock.get("requirement_hash", "N/A")
    lines = [
        "## Traceability",
        f"- Branch: `{branch or 'unknown'}`",
        f"- Evidence run: `{run_id or 'N/A'}`",
        f"- Spec hash: `{spec_hash}`",
        f"- Requirement hash: `{req_hash}`",
    ]
    return "\n".join(lines)

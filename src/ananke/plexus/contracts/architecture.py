"""Architecture contract compiler (D7).

Architecture contracts are checked against:
- CALM architecture
- graph provider facts
- import rules
- component allow/deny relationships
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ananke.plexus.specs.models import Requirement


def components_for(requirement: Requirement, calm: dict[str, Any]) -> list[dict[str, str]]:
    """CALM nodes the requirement names by unique id or by name, whole words only."""
    text = " ".join([requirement.title, requirement.body, *requirement.acceptance_criteria]).lower()
    found = []
    for node in calm.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for key in ("unique-id", "name"):
            value = str(node.get(key) or "").strip()
            if len(value) >= 3 and re.search(
                rf"(?<![\w-]){re.escape(value.lower())}(?![\w-])", text
            ):
                found.append(
                    {"unique_id": str(node.get("unique-id") or ""), "name": str(node.get("name") or ""),
                     "node_type": str(node.get("node-type") or ""), "matched": key}
                )  # fmt: skip
                break
    return found


def compile_architecture(
    feature_dir: Path, requirement: Requirement, *, calm: dict[str, Any] | None = None
) -> Path:
    """Generate an architecture contract from a requirement; with a CALM model, the components it names."""
    req_id = requirement.requirement_id

    contract: dict[str, object] = {
        "contract_id": f"arch-{req_id.lower()}",
        "requirement_id": req_id,
        "title": requirement.title,
        "calm_ref": ".ananke/architecture/system.calm.json",
        "allowed_dependencies": [],
        "denied_dependencies": [],
        "invariant_ids": [],
        "import_rules": {
            "deny_cross_domain_imports": True,
            "deny_circular_imports": True,
        },
        "protocol_constraints": [],
        "deployment_restrictions": [],
    }

    if calm is not None:
        contract["components"] = components_for(requirement, calm)

    out_path = feature_dir / "architecture-contract.yaml"
    out_path.write_text(yaml.dump(contract, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out_path

"""Architecture contract compiler (D7).

Architecture contracts are checked against:
- CALM architecture
- graph provider facts
- import rules
- component allow/deny relationships
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ananke.plexus.specs.models import Requirement


def compile_architecture(feature_dir: Path, requirement: Requirement) -> Path:
    """Generate an architecture contract skeleton from a requirement."""
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

    out_path = feature_dir / "architecture-contract.yaml"
    out_path.write_text(yaml.dump(contract, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out_path

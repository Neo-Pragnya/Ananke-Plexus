"""Model contract compiler (D6).

Model contracts define API schemas, event payload schemas, domain value
objects, boundary models, and compatibility rules.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ananke.plexus.specs.models import Requirement


def compile_model(feature_dir: Path, requirement: Requirement) -> Path:
    """Generate a model contract skeleton YAML from a requirement."""
    req_id = requirement.requirement_id

    contract: dict[str, object] = {
        "contract_id": f"model-{req_id.lower()}",
        "requirement_id": req_id,
        "title": requirement.title,
        "schemas": [
            {
                "name": f"{req_id.lower()}_request",
                "type": "object",
                "description": "API request boundary for this requirement",
                "properties": {},
                "required": [],
            },
            {
                "name": f"{req_id.lower()}_response",
                "type": "object",
                "description": "API response boundary for this requirement",
                "properties": {},
                "required": [],
            },
        ],
        "compatibility_policy": "backward_compatible",
        "boundary_types": ["api_request", "api_response"],
        "error_schemas": [
            {
                "name": f"{req_id.lower()}_error",
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                },
            }
        ],
    }

    out_path = feature_dir / "model-contract.yaml"
    out_path.write_text(yaml.dump(contract, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out_path

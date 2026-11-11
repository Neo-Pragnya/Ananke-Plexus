"""Model contract compiler (D6).

Model contracts define API schemas, event payload schemas, domain value
objects, boundary models, and compatibility rules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ananke.plexus.specs.models import Requirement

_OPERATION = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s`'\"),;]*)", re.I)
_PATH = re.compile(r"(?<![\w/])(/[A-Za-z0-9_\-{}:<>]+(?:/[A-Za-z0-9_\-{}:<>.]*)*)")


def _shape(path: str) -> tuple[str, ...]:
    return tuple(
        "{}" if re.fullmatch(r"\{[^}]*\}|:\w+|<[^>]*>", seg) else seg
        for seg in path.strip("/").split("/")
        if seg
    )


def _named_refs(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        if isinstance(obj.get("$ref"), str):
            found.add(obj["$ref"])
        for value in obj.values():
            found |= _named_refs(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _named_refs(value)
    return found


def operations_for(requirement: Requirement, api_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """The contract's operations a requirement names: ``POST /orders``, a bare ``/orders/{id}`` path, or an
    operation id in backticks. Nothing is guessed from loose words."""
    text = " ".join([requirement.title, requirement.body, *requirement.acceptance_criteria])
    named_ops = {(m.group(1).upper(), _shape(m.group(2))) for m in _OPERATION.finditer(text)}
    bare = {_shape(m.group(1)) for m in _PATH.finditer(_OPERATION.sub(" ", text))}
    ticked = set(re.findall(r"`([A-Za-z_][\w.]*)`", text))
    chosen = []
    for op in api_contract.get("operations") or []:
        shape = _shape(str(op.get("path") or ""))
        if (
            (str(op.get("method")), shape) in named_ops
            or (shape and shape in bare)
            or op.get("operation_id") in ticked
        ):
            chosen.append(op)
    return chosen


def _schemas_for(ops: list[dict[str, Any]], api_contract: dict[str, Any]) -> list[dict[str, Any]]:
    by_name = {
        s["name"]: s
        for s in api_contract.get("schemas") or []
        if isinstance(s, dict) and "name" in s
    }
    wanted: set[str] = set()
    for op in ops:
        if op.get("request"):
            wanted.add(str(op["request"]))
        wanted |= {str(v) for v in (op.get("responses") or {}).values() if v and v != "api_error"}
    pending = [w for w in wanted if w in by_name]
    while pending:  # what the chosen schemas refer to comes along
        for ref in _named_refs(by_name[pending.pop()].get("properties") or {}):
            if ref in by_name and ref not in wanted:
                wanted.add(ref)
                pending.append(ref)
    return [by_name[n] for n in sorted(wanted) if n in by_name]


def compile_model(
    feature_dir: Path, requirement: Requirement, *, api_contract: dict[str, Any] | None = None
) -> Path:
    """Generate a model contract YAML from a requirement.

    With an API contract (``ananke triage api-contract``), the operations the requirement names and their schemas
    replace the empty request and response skeleton; without one, or when it names none, the skeleton stays and says
    why.
    """
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

    if api_contract is not None:
        ops = operations_for(requirement, api_contract)
        contract["source_contract"] = {
            "contract_id": api_contract.get("contract_id"),
            "digest": api_contract.get("digest"),
        }
        if ops:
            contract["schemas"] = _schemas_for(ops, api_contract)
            contract["operations"] = ops
            errors = api_contract.get("error_schemas") or []
            if errors:
                contract["error_schemas"] = errors
        else:
            contract["unmapped"] = (
                "the requirement names no operation in the API contract (write it as METHOD /path or a path)"
            )

    out_path = feature_dir / "model-contract.yaml"
    out_path.write_text(yaml.dump(contract, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out_path

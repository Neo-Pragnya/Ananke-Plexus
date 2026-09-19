"""JSON Schema validation used at registration (spec §173: "schemas valid").

Uses ``jsonschema`` when installed; otherwise a conservative structural check so the
registry keeps working with zero optional dependencies.
"""

from __future__ import annotations

from typing import Any

_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def _basic(schema: Any, path: str = "$", depth: int = 0) -> str | None:
    if isinstance(schema, bool):
        return None
    if not isinstance(schema, dict):
        return f"{path}: schema must be an object or boolean"
    if depth > 32:
        return f"{path}: schema nesting too deep"
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not all(isinstance(x, str) and x in _TYPES for x in types):
            return f"{path}: invalid 'type' {t!r}"
    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            return f"{path}: 'properties' must be an object"
        for key, sub in props.items():
            err = _basic(sub, f"{path}.{key}", depth + 1)
            if err:
                return err
    req = schema.get("required")
    if req is not None and not (isinstance(req, list) and all(isinstance(r, str) for r in req)):
        return f"{path}: 'required' must be a list of strings"
    if "enum" in schema and not isinstance(schema["enum"], list):
        return f"{path}: 'enum' must be a list"
    items = schema.get("items")
    if items is not None and not isinstance(items, (dict, list, bool)):
        return f"{path}: invalid 'items'"
    if isinstance(items, dict):
        return _basic(items, f"{path}[]", depth + 1)
    return None


def check_json_schema(schema: Any) -> str | None:
    """Return an error message, or ``None`` when the schema is acceptable."""
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        return _basic(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return str(exc.message)
    return None

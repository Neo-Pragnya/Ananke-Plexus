"""Minimal deterministic TOML writer (stdlib only reads TOML).

Supports what the registry writes: nested tables, arrays of tables, and scalar/list values.
Keys are emitted in insertion order (callers build dicts deterministically), scalars before
tables as TOML requires, so output is byte-stable and human-friendly.
"""

from __future__ import annotations

import json
import re
from typing import Any

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: str) -> str:
    return key if _BARE.match(key) else json.dumps(key, ensure_ascii=False)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {value!r}")


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def dumps(data: dict[str, Any], *, header: str = "") -> str:
    lines: list[str] = [header] if header else []

    def emit(prefix: str, table: dict[str, Any]) -> None:
        scalars = {
            k: v
            for k, v in table.items()
            if v is not None and not isinstance(v, dict) and not _is_table_array(v)
        }
        for k in scalars:
            lines.append(f"{_key(k)} = {_scalar(scalars[k])}")
        for k in table:
            v = table[k]
            if isinstance(v, dict):
                lines.append("")
                lines.append(f"[{prefix}{_key(k)}]")
                emit(f"{prefix}{_key(k)}.", v)
            elif _is_table_array(v):
                for item in v:
                    lines.append("")
                    lines.append(f"[[{prefix}{_key(k)}]]")
                    emit(f"{prefix}{_key(k)}.", item)

    emit("", data)
    return "\n".join(lines).rstrip() + "\n"

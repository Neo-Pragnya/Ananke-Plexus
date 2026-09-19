"""Runtime translation (spec §88).

Canonical artifacts are translated into framework-neutral *descriptors* that a runtime
adapter consumes. No framework is imported and registry identity is never altered: every
descriptor carries the original ``uri``/``version``/``digest``.
"""

from __future__ import annotations

from typing import Any

from ananke.plexus.registry.errors import TranslationError
from ananke.plexus.registry.models import ArtifactKind, VersionRecord

KNOWN_RUNTIMES = ("pydantic", "microsoft", "generic-mcp", "mcp", "langgraph", "crewai")
_EMPTY: dict[str, Any] = {"type": "object", "properties": {}}


def _tools(rec: VersionRecord) -> list[dict[str, Any]]:
    m = rec.manifest
    if m.tools:
        return [
            {
                "name": t.name,
                "description": t.description or m.summary,
                "input": t.input_schema or _EMPTY,
                "output": t.output_schema,
            }
            for t in m.tools
        ]
    if m.kind in {ArtifactKind.SKILL, ArtifactKind.TOOL}:
        return [
            {
                "name": m.name.replace("-", "_"),
                "description": m.summary or m.description[:200],
                "input": (m.inputs.json_schema if m.inputs and m.inputs.json_schema else _EMPTY),
                "output": m.outputs.json_schema if m.outputs else None,
            }
        ]
    return []


def _identity(rec: VersionRecord) -> dict[str, str]:
    return {"uri": rec.uri, "version": rec.version, "digest": rec.digest}


def translate(rec: VersionRecord, runtime: str) -> dict[str, Any]:
    """Translate ``rec`` for ``runtime``. Raises ``TranslationError`` for unknown runtimes."""
    if runtime not in KNOWN_RUNTIMES:
        raise TranslationError(
            f"unknown runtime {runtime!r}; supported: {', '.join(KNOWN_RUNTIMES)}"
        )
    m = rec.manifest
    tools = _tools(rec)
    is_agent = m.kind is ArtifactKind.AGENT
    skills = [f"{s.ref}@{s.version}" for s in m.skills]
    base: dict[str, Any] = {"runtime": runtime, "registry": _identity(rec), "name": m.name}
    if runtime == "pydantic":
        base.update(
            {
                "kind": "agent" if is_agent else "capability",
                "description": m.summary,
                "instructions_ref": m.instructions.ref if m.instructions else None,
                "model_requirements": m.model_requirements,
                "skills": skills,
                "tools": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters_json_schema": t["input"],
                    }
                    for t in tools
                ],
            }
        )
    elif runtime == "microsoft":
        base.update(
            {
                "type": "agent" if is_agent else "plugin",
                "description": m.summary,
                "instructions_ref": m.instructions.ref if m.instructions else None,
                "plugins": skills,
                "functions": [
                    {"name": t["name"], "description": t["description"], "parameters": t["input"]}
                    for t in tools
                ],
            }
        )
    elif runtime in {"generic-mcp", "mcp"}:
        base.update(
            {
                "runtime": "mcp",
                "tools": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "inputSchema": t["input"],
                        **({"outputSchema": t["output"]} if t["output"] else {}),
                    }
                    for t in tools
                ],
            }
        )
    elif runtime == "langgraph":
        base.update(
            {
                "nodes": [
                    {"name": t["name"], "description": t["description"], "input_schema": t["input"]}
                    for t in tools
                ],
                "subgraphs": skills,
            }
        )
    else:  # crewai
        base.update(
            {
                "role": m.name if is_agent else None,
                "tools": [
                    {"name": t["name"], "description": t["description"], "args_schema": t["input"]}
                    for t in tools
                ],
            }
        )
    return base


def check_translations(rec: VersionRecord) -> list[tuple[str, str | None]]:
    """Translate for every declared runtime. Returns ``(runtime, error|None)`` pairs;
    runtimes without a built-in translator are reported as ``"no built-in translator"``."""
    out: list[tuple[str, str | None]] = []
    for runtime in rec.manifest.runtime.supported:
        if runtime not in KNOWN_RUNTIMES:
            out.append((runtime, "no built-in translator (custom runtime)"))
            continue
        try:
            translate(rec, runtime)
            out.append((runtime, None))
        except TranslationError as exc:
            out.append((runtime, str(exc)))
    return out

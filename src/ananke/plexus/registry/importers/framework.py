"""Framework importers (spec §40): static AST discovery + sandboxed dynamic introspection.

The registry core never depends on any agent framework. Static discovery parses source
with :mod:`ast` (nothing is imported or executed) and recognises tool decorators. Where a
framework only exposes metadata at runtime, an *introspection plugin* runs in the
sandbox from :mod:`ananke.plexus.registry.introspect`.
"""

from __future__ import annotations

import ast
import importlib.metadata as md
import re
import tomllib
from pathlib import Path
from typing import Any, ClassVar

from ananke.plexus.registry.errors import (
    DynamicIntrospectionError,
    ImporterError,
    PolicyViolationError,
)
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImporterPermissions,
    InspectionContext,
    ProbeResult,
    Source,
    slugify,
)
from ananke.plexus.registry.importers.common import now_iso, tree_fingerprint
from ananke.plexus.registry.introspect import run_introspection
from ananke.plexus.registry.models import ImporterInfo, Provenance
from ananke.plexus.registry.payload import collect_directory
from ananke.plexus.registry.semver import normalize_version

FRAMEWORKS: dict[str, tuple[str, frozenset[str]]] = {
    # name -> (registry runtime id, tool decorator names)
    "pydantic-ai": ("pydantic", frozenset({"tool", "tool_plain"})),
    "microsoft-agent-framework": ("microsoft", frozenset({"kernel_function", "ai_function"})),
    "langgraph": ("langgraph", frozenset({"tool"})),
    "crewai": ("crewai", frozenset({"tool"})),
    "generic-python": ("generic-python", frozenset({"tool", "function_tool", "skill", "mcp_tool"})),
}
_MAX_FILES = 500
_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "dict": "object",
    "bytes": "string",
}
_SKIP_PARAMS = {"self", "cls", "ctx", "context", "run_context"}


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _json_type(annotation: ast.expr | None) -> tuple[str | None, bool]:
    """Return (json type, optional)."""
    if annotation is None:
        return None, False
    text = ast.unparse(annotation)
    optional = "None" in text or text.startswith("Optional[")
    cleaned = re.sub(r"Optional\[|\]|\s*\|\s*None|None\s*\|\s*", " ", text)
    head = re.split(r"[\[\s,|]", cleaned.strip())[0].lower().split(".")[-1]
    if head in {"list", "sequence", "iterable", "tuple", "set"}:
        return "array", optional
    if head in {"dict", "mapping"}:
        return "object", optional
    return _TYPE_MAP.get(head), optional


def _tool_from(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(
        args.defaults
    )
    props: dict[str, Any] = {}
    required: list[str] = []
    for i, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        if arg.arg in _SKIP_PARAMS or (i == 0 and ("RunContext" in ann or "Context" in ann)):
            continue
        jtype, optional = _json_type(arg.annotation)
        props[arg.arg] = {"type": jtype} if jtype else {}
        if default is None and not optional:
            required.append(arg.arg)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        jtype, optional = _json_type(arg.annotation)
        props[arg.arg] = {"type": jtype} if jtype else {}
        if default is None and not optional:
            required.append(arg.arg)
    doc = ast.get_docstring(fn) or ""
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "name": fn.name,
        "description": doc.strip().split("\n\n")[0].replace("\n", " ")[:400],
        "input_schema": schema,
    }


def scan_python_tools(
    files: dict[str, bytes], decorators: frozenset[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    tools: list[dict[str, Any]] = []
    problems: list[str] = []
    scanned = 0
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        scanned += 1
        if scanned > _MAX_FILES:
            problems.append(f"stopped after {_MAX_FILES} files")
            break
        try:
            tree = ast.parse(files[path].decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{path}: cannot parse ({exc.__class__.__name__})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                _decorator_name(d) in decorators for d in node.decorator_list
            ):
                tool = _tool_from(node)
                tool["source_file"] = path
                tools.append(tool)
    seen: set[str] = set()
    unique = []
    for t in tools:
        if t["name"] not in seen:
            seen.add(t["name"])
            unique.append({k: v for k, v in t.items() if k != "source_file"})
    return unique, problems


def _split_target(target: str) -> tuple[str, str]:
    head, sep, rest = target.partition(":")
    if sep and head in FRAMEWORKS:
        return head, rest
    return "generic-python", target


class FrameworkImporter:
    id: ClassVar[str] = "framework-static"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme == "framework":
            return ProbeResult(ok=True, confidence=1.0, reason="framework: source")
        return ProbeResult(ok=False, reason="not a framework source")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        framework, raw = _split_target(source.target)
        runtime, decorators = FRAMEWORKS[framework]
        path = Path(raw).expanduser()
        if not path.exists():
            raise ImporterError(f"framework source not found: {path}")
        if path.is_file():
            files = {path.name: path.read_bytes()}
            root_name = path.stem
        else:
            files = collect_directory(path).files
            root_name = path.resolve().name
        tools, problems = scan_python_tools(files, decorators)
        draft: dict[str, Any] = {
            "kind": (ctx.kind.value if ctx.kind else "skill"),
            "name": slugify(root_name),
            "tools": tools,
            "runtime": {"supported": [runtime]},
            "summary": f"{len(tools)} {framework} tool(s) discovered statically",
        }
        pyproject = files.get("pyproject.toml")
        if pyproject:
            try:
                project = tomllib.loads(pyproject.decode("utf-8")).get("project", {})
                if project.get("version"):
                    draft["version"] = str(normalize_version(str(project["version"])))
                if project.get("name"):
                    draft["name"] = slugify(str(project["name"]))
                lic = project.get("license")
                if lic:
                    draft["license"] = {
                        "expression": lic.get("text") if isinstance(lic, dict) else str(lic),
                        "source": "pyproject.toml",
                    }
            except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError):
                pass
        cand = Candidate(draft=draft, files=files, source=source.raw)
        cand.provenance = Provenance(
            source_type=f"framework:{framework}",
            source_name=root_name,
            importer=ImporterInfo(id=self.id, version=self.version),
            discovered_at=now_iso(),
            fingerprint=tree_fingerprint(files),
        )
        cand.note(
            "info",
            "static-ast",
            f"{len(tools)} tool(s) found by AST scan; schemas inferred from annotations",
        )
        for problem in problems:
            cand.note("warning", "scan-problem", problem)
        cand.note(
            "warning",
            "no-permissions",
            "source code cannot declare permissions; declare them explicitly",
            "permissions",
        )
        if not tools:
            cand.note("warning", "no-tools", f"no {framework} tool decorators found", "tools")
        return [cand]


class DynamicImporter:
    id: ClassVar[str] = "dynamic-introspection"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = False
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions(executes_code=True)

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme == "dynamic":
            return ProbeResult(ok=True, confidence=1.0, reason="dynamic: source")
        return ProbeResult(ok=False, reason="not a dynamic source")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        if not ctx.policy.dynamic_allowed(ctx.allow_dynamic):
            raise PolicyViolationError(
                "dynamic introspection executes package code; enable "
                "dynamic_introspection.allowed or pass --allow-dynamic",
                code="DYNAMIC_INTROSPECTION_DISABLED",
            )
        framework, raw = _split_target(source.target)
        plugin = ctx.plugin or _entry_point_plugin(framework)
        if not plugin:
            raise DynamicIntrospectionError(
                f"no introspection plugin for {framework!r}: pass --plugin module:function or "
                "install a package providing entry point group 'ananke.registry.introspectors'"
            )
        artifacts = run_introspection(
            plugin=plugin,
            source=raw,
            framework=framework,
            sys_path=ctx.plugin_paths,
            policy=ctx.policy.dynamic_introspection,
        )
        out: list[Candidate] = []
        for item in artifacts:
            draft = item.get("manifest")
            if not isinstance(draft, dict):
                raise DynamicIntrospectionError("each artifact needs a 'manifest' object")
            files = {
                str(k): v.encode("utf-8") if isinstance(v, str) else b""
                for k, v in (item.get("files") or {}).items()
                if isinstance(v, str)
            }
            cand = Candidate(draft=draft, files=files, source=source.raw, dynamic=True)
            cand.provenance = Provenance(
                source_type=f"dynamic:{framework}",
                source_name=str(draft.get("name", raw)),
                importer=ImporterInfo(id=self.id, version=self.version),
                discovered_at=now_iso(),
                fingerprint=tree_fingerprint(files) if files else None,
            )
            cand.note(
                "warning",
                "dynamic",
                "discovered by executing code in a sandboxed subprocess; review before trusting",
            )
            out.append(cand)
        if not out:
            raise DynamicIntrospectionError("introspector returned no artifacts")
        return out


def _entry_point_plugin(framework: str) -> str | None:
    for ep in md.entry_points().select(group="ananke.registry.introspectors"):
        if ep.name == framework:
            return str(ep.value)
    return None

"""What each API operation takes and returns, who may call it, and the same facts as a BMAD model contract
(three-repo plan: CAP-api-contract-intelligence-01 and -02).

**Inventory (-01).** It starts from the routes the repository analysis found and reads each handler, without
importing or running anything:

* **Python** (FastAPI, Starlette, Flask, aiohttp, Tornado): path parameters from the route; for FastAPI the handler's
  parameters (a Pydantic model or dataclass is the body, a plain type the query), ``Depends``/``Security`` and the
  route's ``dependencies=`` for auth, ``response_model=`` and ``status_code=``; for every framework the request fields
  the handler reads (``request.args.get("q")``, ``payload["items"]``, ``request.get_json()["name"]``), auth decorators
  (``login_required``, ``jwt_required`` and the like) and the error statuses it raises or returns
  (``HTTPException(status_code=404)``, ``abort(403)``, ``return ..., 409``).
* **JavaScript and TypeScript** (Express, NestJS): auth middleware named in the route call, ``req.body``/``req.query``/
  ``req.params`` fields, ``res.status(404)`` and ``res.sendStatus(401)``.
* **OpenAPI 3 and Swagger 2** files: parameters, the request body and each response with its schema (local ``$ref``
  resolved), and ``security`` per operation or for the whole document.

Every fact carries the file and line it was read from and says whether it was **observed** (written down: an OpenAPI
operation, a type annotation, a ``response_model``) or **inferred** (read from how the handler uses its input).

**Normalised contract (-02).** The operations and their schemas become the model contract that ``ananke spec``
compiles into a BMAD bundle (``contracts/model.py``): ``contract_id``, ``schemas``, ``error_schemas``,
``boundary_types``, ``compatibility_policy``, plus ``operations`` that point at the schemas by name. An OpenAPI file,
where there is one, is the declared contract and the code fills in only what it leaves out.

**Findings.** Operations with no documented or raised error response, secured operations that document no 401 or
403, secured operations no test calls, secured operations whose failed-auth path no test checks, auth that the code and
the OpenAPI file disagree on, and frameworks whose handlers aren't read. Each names its evidence and a remedy, and
recommended tests are suggestions: nothing is run or written.

The same sources give the same result: the digest leaves out the time it was made.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import warnings
from pathlib import Path
from typing import Any

from ananke.plexus.intel.contracts import _matches, _segments, test_calls

SCHEMA = "ananke/api-contract@1"
CONTRACT_SCHEMA = "ananke/api-model-contract@1"
MAX_SPEC_BYTES = 5_000_000
MAX_FILE_BYTES = 2_000_000
MAX_REF_DEPTH = 12
JS_WINDOW = 60  # lines read after a JS route for its handler

SHAPE_FRAMEWORKS = {"fastapi", "starlette", "flask", "aiohttp", "tornado", "express", "nestjs"}
_AUTH_WORD = re.compile(
    r"(auth|login|token|jwt|bearer|oauth|api_?key|current_?user|require_?user|verify_?user|permission|"
    r"admin_?required|session_?required|guard|passport|is_?authenticated|ensure_?logged)",
    re.I,
)
_PY_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "bytes": "string",
    "Decimal": "number",
    "UUID": "string",
    "datetime": "string",
    "date": "string",
    "EmailStr": "string",
    "Any": "object",
    "Dict": "object",
    "List": "array",
    "Sequence": "array",
    "Mapping": "object",
}
_MODEL_BASES = {"BaseModel", "SQLModel", "Schema", "TypedDict", "Struct"}
_ENUM_BASES = {"Enum", "StrEnum", "IntEnum", "IntFlag", "Flag"}
_REQUEST_NAMES = {"request", "req"}
_BODY_CALLS = {"get_json", "json"}
_STATUS_CONST = re.compile(r"HTTP_(\d{3})")
_JS_STATUS = re.compile(
    r"\b(?:res|response|reply|ctx)\s*\.\s*(?:status|sendStatus|code)\(\s*(\d{3})\s*\)"
)
_JS_FIELD = re.compile(r"\breq(?:uest)?\s*\.\s*(body|query|params)\s*\.\s*([A-Za-z_$][\w$]*)")
_JS_DESTRUCTURE = re.compile(r"\{([^{}]{1,300})\}\s*=\s*req(?:uest)?\s*\.\s*(body|query|params)\b")
_JS_IDENT = re.compile(r"[A-Za-z_$][\w$.]*")
_NEST_GUARD = re.compile(r"@UseGuards\(([^)]*)\)")
_UNSUPPORTED_MARKERS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        ".go",
        re.compile(r"\b(?:http\.HandleFunc|mux\.Handle|gin\.Default|echo\.New|chi\.NewRouter)\b"),
        "Go HTTP",
    ),
    (".java", re.compile(r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b"), "Spring"),
    (".kt", re.compile(r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b"), "Spring (Kotlin)"),
    (".cs", re.compile(r"\[(?:HttpGet|HttpPost|HttpPut|HttpDelete|Route)\b"), "ASP.NET"),
    (
        ".rb",
        re.compile(r"^\s*(?:get|post|put|patch|delete|resources)\s+['\":]", re.M),
        "Rails/Sinatra",
    ),
    (".php", re.compile(r"Route::(?:get|post|put|delete|patch)\("), "Laravel"),
    (".rs", re.compile(r"#\[(?:get|post|put|delete|patch)\(|Router::new\(\)"), "Rust (actix/axum)"),
)


_VENDORED = {
    "node_modules",
    "site-packages",
    "dist-packages",
    "__pycache__",
    "venv",
    "virtualenv",
    "bower_components",
}


def project_files(root: Path, files: list[str]) -> list[str]:
    """The project's own files: no virtualenv (any folder holding ``pyvenv.cfg``), installed packages, dependency
    folders or dot-folders, whose code isn't the project's API and would change with the Python reading it."""
    envs = {f.rsplit("/", 1)[0] + "/" for f in files if f.endswith("/pyvenv.cfg")}
    if "pyvenv.cfg" in files:
        envs.add("")
    kept = []
    for rel in files:
        parts = rel.split("/")
        if any(p.startswith(".") or p in _VENDORED for p in parts[:-1]):
            continue
        if any(rel.startswith(env) for env in envs):
            continue
        kept.append(rel)
    return kept


class ApiContractError(ValueError):
    """Input the inventory can't use; the message says what to do instead."""


# ---------------------------------------------------------------- small helpers


def _fact(value: Any, evidence: str, how: str = "observed") -> dict[str, Any]:
    return {"value": value, "evidence": evidence, "status": how}


def _type_of(node: ast.expr | None) -> tuple[dict[str, Any], bool]:
    """A JSON-schema fragment for a Python annotation, and whether ``None`` is allowed."""
    if node is None:
        return {}, False
    if isinstance(node, ast.Constant) and node.value is None:
        return {"type": "null"}, True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):  # a forward reference
        try:
            return _type_of(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return {}, False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, ln = _type_of(node.left)
        right, rn = _type_of(node.right)
        kept = right if left.get("type") == "null" else left
        return kept, ln or rn
    name = _dotted(node.value if isinstance(node, ast.Subscript) else node).rsplit(".", 1)[-1]
    if isinstance(node, ast.Subscript):
        inner = node.slice
        args = list(inner.elts) if isinstance(inner, ast.Tuple) else [inner]
        if name in {"Optional"}:
            schema, _ = _type_of(args[0])
            return schema, True
        if name in {"Union"}:
            options = [_type_of(a) for a in args]
            real = [s for s, _ in options if s.get("type") != "null"]
            return (real[0] if real else {}), any(s.get("type") == "null" for s, _ in options)
        if name in {"Annotated"}:
            return _type_of(args[0])
        if name in {"list", "List", "Sequence", "set", "tuple", "Iterable"}:
            item, _ = _type_of(args[0])
            return {"type": "array", "items": item}, False
        if name in {"dict", "Dict", "Mapping"}:
            return {"type": "object"}, False
        if name == "Literal":
            values = [a.value for a in args if isinstance(a, ast.Constant)]
            return {"enum": values}, False
    if name in _PY_TYPES:
        return {"type": _PY_TYPES[name]}, False
    if name and name[0].isupper():
        return {"$ref": name}, False
    return {}, False


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _status_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and 100 <= node.value <= 599:
        return str(node.value)
    if isinstance(node, (ast.Attribute, ast.Name)):
        m = _STATUS_CONST.search(_dotted(node))
        if m:
            return m.group(1)
    return None


def _read(root: Path, rel: str) -> str | None:
    path = root / rel
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _split_evidence(evidence: str) -> tuple[str, int]:
    rel, _, line = evidence.rpartition(":")
    return rel, int(line) if line.isdigit() else 0


def _path_params(path: str) -> list[str]:
    return re.findall(r"\{([^}/]+)\}", path)


def stable_digest(obj: Any) -> str:
    """sha256 of the canonical JSON, leaving out ``generated_at`` wherever it appears."""

    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in {"generated_at", "digest"}}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    body = json.dumps(strip(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- Python models


class _Models:
    """Pydantic models, dataclasses and TypedDicts defined in the repository, by class name."""

    def __init__(self) -> None:
        self.found: dict[str, dict[str, Any]] = {}

    def add_file(self, rel: str, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {_dotted(b).rsplit(".", 1)[-1] for b in node.bases}
            if bases & _ENUM_BASES:
                self._add_enum(rel, node, bases)
                continue
            decorated = {_dotted(d).rsplit(".", 1)[-1] for d in node.decorator_list}
            if not (bases & _MODEL_BASES or "dataclass" in decorated or bases & set(self.found)):
                continue
            props: dict[str, Any] = {}
            required: list[str] = []
            for parent in sorted(
                bases & set(self.found)
            ):  # fields a model inherits from another one
                props.update(copy.deepcopy(self.found[parent]["properties"]))
                required += [r for r in self.found[parent]["required"] if r not in required]
            for item in node.body:
                if not (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)):
                    continue
                name = item.target.id
                if name.startswith("_") or _dotted(item.annotation).endswith("ClassVar"):
                    continue
                schema, nullable = _type_of(item.annotation)
                if nullable:
                    schema = {**schema, "nullable": True}
                props[name] = schema
                has_default = item.value is not None and not (
                    isinstance(item.value, ast.Call)
                    and _dotted(item.value.func).endswith("Field")
                    and (not item.value.args or _is_ellipsis(item.value.args[0]))
                    and not any(
                        k.arg in {"default", "default_factory"} for k in item.value.keywords
                    )
                )
                if not has_default and not nullable and name not in required:
                    required.append(name)
                elif has_default and name in required:
                    required.remove(name)
            self.found[node.name] = {
                "name": node.name,
                "type": "object",
                "description": (ast.get_docstring(node) or "").split("\n", 1)[0],
                "properties": props,
                "required": required,
                "evidence": f"{rel}:{node.lineno}",
                "status": "observed",
            }

    def _add_enum(self, rel: str, node: ast.ClassDef, bases: set[str]) -> None:
        values: list[Any] = []
        for item in node.body:
            if (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and not item.targets[0].id.startswith("_")
                and isinstance(item.value, ast.Constant)
            ):
                values.append(item.value.value)
        kinds = {type(v).__name__ for v in values}
        if "str" in bases or "StrEnum" in bases or kinds == {"str"}:
            kind = "string"
        elif "int" in bases or "IntEnum" in bases or kinds == {"int"}:
            kind = "integer"
        else:
            kind = "string" if not kinds else "object"
        self.found[node.name] = {
            "name": node.name,
            "type": kind,
            "description": (ast.get_docstring(node) or "").split("\n", 1)[0],
            "enum": values,
            "properties": {},
            "required": [],
            "evidence": f"{rel}:{node.lineno}",
            "status": "observed",
        }


def _is_ellipsis(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


# ---------------------------------------------------------------- Python handlers


def _find_function(
    tree: ast.Module, name: str, line: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            starts = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            if best is None or abs(starts - line) < abs(
                min([best.lineno, *(d.lineno for d in best.decorator_list)]) - line
            ):
                best = node
    return best


def _route_decorator(fn: ast.FunctionDef | ast.AsyncFunctionDef, line: int) -> ast.Call | None:
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and dec.lineno == line:
            return dec
    calls = [d for d in fn.decorator_list if isinstance(d, ast.Call)]
    return calls[0] if calls else None


def _python_handler(
    route: dict[str, Any], tree: ast.Module, rel: str, line: int, models: _Models
) -> dict[str, Any]:
    out: dict[str, Any] = {"auth": [], "request": {}, "responses": {}, "read": False}
    handler = str(route.get("handler") or "")
    name = handler.rsplit(":", 1)[-1].rsplit(".", 1)[-1] if handler else ""
    fn = _find_function(tree, name, line) if name else None
    if fn is None:
        return out
    out["read"] = True
    framework = route.get("framework")
    path_params = set(_path_params(route["path"]))
    body: dict[str, Any] = {}
    query: dict[str, Any] = {}
    header: dict[str, Any] = {}
    body_names: set[str] = set()
    dec = _route_decorator(fn, line)

    # decorators: auth, response_model, status_code, dependencies, responses
    for d in fn.decorator_list:
        word = _dotted(d)
        if _AUTH_WORD.search(word.rsplit(".", 1)[-1]) and d is not dec:
            out["auth"].append(_fact(word, f"{rel}:{d.lineno}"))
    if dec is not None:
        for kw in dec.keywords:
            if kw.arg == "response_model":
                schema, _ = _type_of(kw.value)
                ok = _status_value(_kw(dec, "status_code")) or "200"
                named = schema.get("$ref") if set(schema) == {"$ref"} else None
                out["responses"][ok] = _fact(named or schema, f"{rel}:{dec.lineno}")
            elif kw.arg == "status_code" and _status_value(kw.value):
                code = _status_value(kw.value) or "200"
                out["responses"].setdefault(code, _fact({}, f"{rel}:{dec.lineno}"))
            elif kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for dep in kw.value.elts:
                    target = _depends_target(dep)
                    if target and _AUTH_WORD.search(target):
                        out["auth"].append(_fact(target, f"{rel}:{dep.lineno}"))
            elif kw.arg == "responses" and isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    listed = _status_value(key)
                    if listed:
                        out["responses"].setdefault(code, _fact({}, f"{rel}:{dec.lineno}"))

    # parameters
    args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    defaults: dict[str, ast.expr] = {}
    positional = [*fn.args.posonlyargs, *fn.args.args]
    for arg, value in zip(
        positional[len(positional) - len(fn.args.defaults) :], fn.args.defaults, strict=False
    ):
        defaults[arg.arg] = value
    for arg, kw_default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=False):
        if kw_default is not None:
            defaults[arg.arg] = kw_default
    for arg in args:
        if arg.arg in {"self", "cls"}:
            continue
        where = f"{rel}:{arg.lineno}"
        default = defaults.get(arg.arg)
        target = _depends_target(default) if default is not None else None
        if target is not None:
            if _AUTH_WORD.search(target) or _AUTH_WORD.search(arg.arg):
                out["auth"].append(_fact(target, where))
            continue
        ann = arg.annotation
        annotated = (
            _dotted(ann.value) if isinstance(ann, ast.Subscript) else _dotted(ann) if ann else ""
        )
        if annotated.rsplit(".", 1)[-1] in {
            "Request",
            "HttpRequest",
            "WebSocket",
            "BackgroundTasks",
            "Response",
        }:
            continue
        if framework not in {"fastapi", "starlette"}:
            continue  # Flask-style view arguments are the path parameters
        schema, nullable = _type_of(ann)
        explicit = _dotted(default).rsplit(".", 1)[-1] if isinstance(default, ast.Call) else ""
        required = default is None or (
            isinstance(default, ast.Call)
            and (not default.args or _is_ellipsis(default.args[0]))
            and not any(k.arg in {"default", "default_factory"} for k in default.keywords)
        )
        required = required and not nullable
        how = "observed" if ann is not None else "inferred"
        if arg.arg in path_params or explicit == "Path":
            continue  # recorded from the route
        if explicit == "Header":
            header[arg.arg] = _fact(schema, where, how)
            if _AUTH_WORD.search(arg.arg):
                out["auth"].append(_fact(f"header {arg.arg}", where))
        elif (
            explicit == "Body"
            or ("$ref" in schema and schema["$ref"] in models.found)
            or (schema.get("type") == "object" and explicit != "Query")
        ):
            body_names.add(arg.arg)
            if "$ref" in schema and schema["$ref"] in models.found:
                out["request"]["body_schema"] = _fact(schema["$ref"], where, how)
            else:
                body[arg.arg] = _fact({**schema, "required": required}, where, how)
        else:
            query[arg.arg] = _fact({**schema, "required": required}, where, how)

    if fn.returns is not None and not any(c.startswith("2") for c in out["responses"]):
        schema, _ = _type_of(fn.returns)
        if schema and schema != {"type": "null"}:
            named = schema.get("$ref") if set(schema) == {"$ref"} else None
            out["responses"]["200"] = _fact(named or schema, f"{rel}:{fn.lineno}")

    containers = {k for k in body if k in body_names}
    before = set(body)
    _walk_body(fn, rel, body_names, body, query, out)
    if (
        set(body) - before
    ):  # ``payload: dict`` read as payload["items"]: the keys are the body, not the name
        for name in containers:
            body.pop(name, None)
    if not any(c.startswith("2") for c in out["responses"]):
        out["responses"]["200"] = _fact({}, f"{rel}:{fn.lineno}", "inferred")
    if body:
        out["request"]["body"] = body
    if query:
        out["request"]["query"] = query
    if header:
        out["request"]["header"] = header
    return out


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _depends_target(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1] in {
        "Depends",
        "Security",
    }:
        return _dotted(node.args[0]) if node.args else "Depends()"
    return None


def _is_request_body(node: ast.AST, body_names: set[str]) -> bool:
    """``request.json``, ``request.get_json()``, ``await request.json()`` or a name holding one of them."""
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in body_names
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _BODY_CALLS
        and _dotted(node.value).rsplit(".", 1)[-1] in _REQUEST_NAMES
    )


def _walk_body(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    rel: str,
    body_names: set[str],
    body: dict[str, Any],
    query: dict[str, Any],
    out: dict[str, Any],
) -> None:
    names = set(body_names)
    for node in ast.walk(fn):
        # data = request.get_json()
        if isinstance(node, ast.Assign) and _is_request_body(node.value, set()):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(fn):
        where = f"{rel}:{getattr(node, 'lineno', fn.lineno)}"
        if isinstance(node, ast.Subscript) and _is_request_body(node.value, names):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                body.setdefault(key.value, _fact({"required": True}, where, "inferred"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            first = node.args[0] if node.args else None
            literal = (
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else None
            )
            if node.func.attr == "get" and literal and _is_request_body(owner, names):
                body.setdefault(literal, _fact({"required": False}, where, "inferred"))
            elif (
                node.func.attr in {"get", "getlist"}
                and literal
                and _dotted(owner).endswith(
                    (".args", ".query", ".query_params", ".GET", ".rel_url.query")
                )
            ):
                query.setdefault(literal, _fact({"required": False}, where, "inferred"))
            elif (
                node.func.attr == "get"
                and literal
                and _dotted(owner).endswith(".headers")
                and _AUTH_WORD.search(literal)
            ):
                out["auth"].append(_fact(f"header {literal}", where, "inferred"))
        elif isinstance(node, ast.Subscript) and _dotted(node.value).endswith(
            (".args", ".query_params")
        ):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                query.setdefault(key.value, _fact({"required": True}, where, "inferred"))
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            exc = _dotted(node.exc.func).rsplit(".", 1)[-1]
            code = _status_value(_kw(node.exc, "status_code")) or (
                _status_value(node.exc.args[0]) if node.exc.args else None
            )
            if code is None and exc in {"PermissionDenied", "Forbidden", "HTTPForbidden"}:
                code = "403"
            elif code is None and exc in {"NotFound", "HTTPNotFound", "Http404"}:
                code = "404"
            elif code is None and exc in {"Unauthorized", "HTTPUnauthorized", "NotAuthenticated"}:
                code = "401"
            if code:
                out["responses"].setdefault(code, _fact({}, where))
        elif (
            isinstance(node, ast.Call)
            and _dotted(node.func).rsplit(".", 1)[-1] == "abort"
            and node.args
        ):
            code = _status_value(node.args[0])
            if code:
                out["responses"].setdefault(code, _fact({}, where))
        elif (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Tuple)
            and len(node.value.elts) >= 2
        ):
            code = _status_value(node.value.elts[1])
            if code:
                out["responses"].setdefault(code, _fact({}, where))
        elif isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1] in {
            "JSONResponse",
            "Response",
            "json_response",
            "make_response",
        }:
            code = _status_value(_kw(node, "status_code") or _kw(node, "status"))
            if code is None and len(node.args) > 1:
                code = _status_value(node.args[1])
            if code:
                out["responses"].setdefault(code, _fact({}, where))


# ---------------------------------------------------------------- JavaScript handlers


def _js_handler(route: dict[str, Any], text: str, rel: str, line: int) -> dict[str, Any]:
    lines = text.splitlines()
    out: dict[str, Any] = {"auth": [], "request": {}, "responses": {}, "read": False}
    if not 0 < line <= len(lines):
        return out
    out["read"] = True
    start = line - 1
    # NestJS guards sit in decorators just above the method, or on the controller class
    for back in range(start, max(-1, start - 8), -1):
        m = _NEST_GUARD.search(lines[back])
        if m:
            out["auth"].append(_fact(m.group(1).strip(), f"{rel}:{back + 1}"))
    for no, raw in enumerate(lines):
        if no >= start:
            break
        m = _NEST_GUARD.search(raw)
        if m and re.search(r"@Controller\(", "\n".join(lines[no : no + 4])):
            out["auth"].append(_fact(m.group(1).strip(), f"{rel}:{no + 1}"))
    window: list[str] = []
    depth = 0
    for no in range(start, min(len(lines), start + JS_WINDOW)):
        window.append(lines[no])
        depth += (
            lines[no].count("{")
            + lines[no].count("(")
            - lines[no].count("}")
            - lines[no].count(")")
        )
        if no > start and depth <= 0:
            break
    call = lines[start]
    head = call.split("=>", 1)[0].split("function", 1)[0]
    path_arg = re.search(r"""\(\s*(['"`])[^'"`]*\1\s*,(.*)$""", head)
    if path_arg:
        for ident in _JS_IDENT.findall(path_arg.group(2)):
            if ident in {"req", "res", "next", "async"}:
                continue
            if _AUTH_WORD.search(ident):
                out["auth"].append(_fact(ident, f"{rel}:{line}"))
    body: dict[str, Any] = {}
    query: dict[str, Any] = {}
    for offset, raw in enumerate(window):
        where = f"{rel}:{line + offset}"
        for m in _JS_FIELD.finditer(raw):
            target = body if m.group(1) == "body" else query if m.group(1) == "query" else None
            if target is not None:
                target.setdefault(m.group(2), _fact({"required": False}, where, "inferred"))
        for m in _JS_DESTRUCTURE.finditer(raw):
            target = body if m.group(2) == "body" else query if m.group(2) == "query" else None
            if target is None:
                continue
            for part in m.group(1).split(","):
                key = part.split(":", 1)[0].split("=", 1)[0].strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", key):
                    target.setdefault(key, _fact({"required": "=" not in part}, where, "inferred"))
        for m in _JS_STATUS.finditer(raw):
            out["responses"].setdefault(m.group(1), _fact({}, where))
        if re.search(
            r"\breq(?:uest)?\.headers?\b.*authorization|\bauthorization\b.*\breq", raw, re.I
        ):
            out["auth"].append(_fact("authorization header", where, "inferred"))
    if not any(c.startswith("2") for c in out["responses"]):
        out["responses"]["200"] = _fact({}, f"{rel}:{line}", "inferred")
    if body:
        out["request"]["body"] = body
    if query:
        out["request"]["query"] = query
    return out


# ---------------------------------------------------------------- OpenAPI


def load_spec(path: Path) -> dict[str, Any]:
    """An OpenAPI 3 or Swagger 2 document, or an actionable error."""
    if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise ApiContractError(
            f"{path.name}: unsupported format {path.suffix or '(none)'}; give an OpenAPI 3 or Swagger 2 file "
            "in JSON or YAML"
        )
    try:
        if path.stat().st_size > MAX_SPEC_BYTES:
            raise ApiContractError(
                f"{path.name} is over {MAX_SPEC_BYTES // 1_000_000} MB; split it or pass one part"
            )
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ApiContractError(f"{path} doesn't exist; check the --spec path") from exc
    except PermissionError as exc:
        raise ApiContractError(
            f"{path} can't be read (permission denied); grant read access"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ApiContractError(f"{path.name} isn't UTF-8 text") from exc
    try:
        if path.suffix.lower() == ".json":
            doc = json.loads(text)
        else:
            import yaml

            doc = yaml.safe_load(text)
    except Exception as exc:  # every parser error becomes one actionable message
        first = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
        raise ApiContractError(f"{path.name} is malformed: {first}") from exc
    if not isinstance(doc, dict) or not (
        str(doc.get("openapi", "")).startswith("3") or str(doc.get("swagger", "")).startswith("2")
    ):
        raise ApiContractError(
            f"{path.name} isn't an OpenAPI 3 or Swagger 2 document (no 'openapi: 3.x' or 'swagger: 2.0' key)"
        )
    if not isinstance(doc.get("paths"), dict):
        raise ApiContractError(f"{path.name} has no 'paths' object, so it describes no operations")
    return doc


class _Refs:
    """Local ``$ref`` resolution that names each schema once and stops at cycles."""

    def __init__(self, doc: dict[str, Any], rel: str, limits: set[str]) -> None:
        self.doc = doc
        self.rel = rel
        self.limits = limits
        self.named: dict[str, dict[str, Any]] = {}

    def target(self, ref: str) -> Any:
        node: Any = self.doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node.get(part) if isinstance(node, dict) else None
        return node

    def schema(self, schema: Any, depth: int = 0) -> dict[str, Any]:
        """The schema with each named ``$ref`` replaced by ``{"$ref": Name}`` and the named one recorded."""
        if not isinstance(schema, dict):
            return {}
        if depth > MAX_REF_DEPTH:
            self.limits.add(
                f"{self.rel}: schemas nest deeper than {MAX_REF_DEPTH} levels; the rest is left out"
            )
            return {}
        ref = schema.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                self.limits.add(f"{self.rel}: external reference {ref[:80]} isn't followed")
                return {"$ref": ref}
            name = ref.rsplit("/", 1)[-1]
            if name not in self.named:
                self.named[name] = {}  # placeholder: a cycle points back here and stops
                resolved = self.schema(self.target(ref), depth + 1)
                self.named[name] = resolved
            return {"$ref": name}
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                out[key] = {k: self.schema(v, depth + 1) for k, v in value.items()}
            elif key in {"items", "additionalProperties"} and isinstance(value, dict):
                out[key] = self.schema(value, depth + 1)
            elif key in {"allOf", "oneOf", "anyOf"} and isinstance(value, list):
                out[key] = [self.schema(v, depth + 1) for v in value]
            elif key in {
                "type",
                "format",
                "enum",
                "required",
                "nullable",
                "description",
                "title",
                "default",
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
                "pattern",
            }:
                out[key] = value
        return out


def _spec_operations(
    doc: dict[str, Any], rel: str, limits: set[str]
) -> tuple[list[dict[str, Any]], _Refs]:
    refs = _Refs(doc, rel, limits)
    swagger = "swagger" in doc
    global_security = doc.get("security")
    operations: list[dict[str, Any]] = []
    for path, item in sorted(doc["paths"].items()):
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        shared = [p for p in item.get("parameters") or [] if isinstance(p, dict)]
        for method in ("get", "put", "post", "delete", "patch", "head", "options"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            where = f"{rel}#/paths/{path.replace('~', '~0').replace('/', '~1')}/{method}"
            request: dict[str, Any] = {}
            for param in [*shared, *(p for p in op.get("parameters") or [] if isinstance(p, dict))]:
                if (
                    "$ref" in param
                    and isinstance(param["$ref"], str)
                    and param["$ref"].startswith("#/")
                ):
                    param = refs.target(param["$ref"]) or {}
                place, name = param.get("in"), param.get("name")
                if not (isinstance(place, str) and isinstance(name, str)):
                    continue
                if place == "body" and swagger:
                    request["body_schema"] = _schema_name(refs, param.get("schema"), where)
                    continue
                param_schema = refs.schema(
                    param.get("schema")
                    if "schema" in param
                    else {k: param[k] for k in ("type", "format", "enum") if k in param}
                )
                bucket = {
                    "query": "query",
                    "header": "header",
                    "path": "path",
                    "cookie": "cookie",
                    "formData": "body",
                }.get(place)
                if bucket:
                    request.setdefault(bucket, {})[name] = _fact(
                        {
                            **param_schema,
                            "required": bool(param.get("required")) or place == "path",
                        },
                        where,
                    )
            content = ((op.get("requestBody") or {}).get("content") or {}) if not swagger else {}
            if isinstance(content, dict) and content:
                media = "application/json" if "application/json" in content else next(iter(content))
                body = content.get(media) or {}
                if isinstance(body, dict) and body.get("schema") is not None:
                    request["body_schema"] = _schema_name(refs, body["schema"], where)
                    request["media_type"] = media
            responses: dict[str, Any] = {}
            for code, resp in (op.get("responses") or {}).items():
                if (
                    isinstance(resp, dict)
                    and isinstance(resp.get("$ref"), str)
                    and resp["$ref"].startswith("#/")
                ):
                    resp = refs.target(resp["$ref"]) or {}
                found: Any = None
                if isinstance(resp, dict):
                    if swagger:
                        found = resp.get("schema")
                    else:
                        rc = resp.get("content") or {}
                        if isinstance(rc, dict) and rc:
                            media = (
                                "application/json" if "application/json" in rc else next(iter(rc))
                            )
                            found = (rc.get(media) or {}).get("schema")
                responses[str(code)] = _fact(
                    _schema_name(refs, found, where)["value"] if found is not None else {}, where
                )
            security = op.get("security", global_security)
            schemes = sorted({k for s in security or [] if isinstance(s, dict) for k in s})
            operations.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": op.get("operationId") or "",
                    "summary": op.get("summary") or "",
                    "auth": [_fact(s, where) for s in schemes],
                    "auth_declared": security is not None,
                    "request": request,
                    "responses": responses,
                    "evidence": where,
                }
            )
    return operations, refs


def _schema_name(refs: _Refs, schema: Any, where: str) -> dict[str, Any]:
    resolved = refs.schema(schema)
    if "$ref" in resolved and len(resolved) == 1:
        return _fact(resolved["$ref"], where)
    return _fact(resolved, where)


def find_specs(root: Path, files: list[str]) -> list[str]:
    names = ("openapi", "swagger", "api-docs")
    return sorted(
        rel
        for rel in files
        if rel.rsplit("/", 1)[-1].lower().endswith((".json", ".yaml", ".yml"))
        and any(n in rel.rsplit("/", 1)[-1].lower() for n in names)
        and "/node_modules/" not in f"/{rel}"
        and not rel.startswith(("tests/", "test/"))
    )[:20]


# ---------------------------------------------------------------- the inventory


def _unsupported(root: Path, files: list[str]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for rel in files:
        for suffix, pattern, label in _UNSUPPORTED_MARKERS:
            if label in seen or not rel.endswith(suffix):
                continue
            text = _read(root, rel)
            if text:
                m = pattern.search(text)
                if m:
                    seen[label] = f"{rel}:{text.count(chr(10), 0, m.start()) + 1}"
    return [{"framework": k, "evidence": v} for k, v in sorted(seen.items())]


def inventory(
    root: Path, api: dict[str, Any], files: list[str], specs: list[Path] | None = None
) -> dict[str, Any]:
    """The operations the code serves and the OpenAPI files declare, each with auth, request and responses."""
    limits: set[str] = set()
    models = _Models()
    trees: dict[str, ast.Module | None] = {}
    for rel in files:
        if not rel.endswith(".py") or "/." in f"/{rel}":
            continue
        text = _read(root, rel)
        if text is None:
            continue
        try:
            # the project's own invalid escapes aren't this report's business
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (SyntaxError, ValueError):
            trees[rel] = None
            continue
        trees[rel] = tree
        models.add_file(rel, tree)
    # a model can inherit from one defined later in another file: one more pass picks those up
    for rel, maybe in trees.items():
        if maybe is not None:
            models.add_file(rel, maybe)

    operations: list[dict[str, Any]] = []
    unsupported_routes: dict[str, list[str]] = {}
    for route in api.get("routes") or []:
        if not route.get("method") or not route.get("path"):
            continue
        rel, line = _split_evidence(str(route.get("evidence") or ""))
        framework = str(route.get("framework") or "")
        facts: dict[str, Any] = {"auth": [], "request": {}, "responses": {}, "read": False}
        if framework in SHAPE_FRAMEWORKS:
            if rel.endswith(".py"):
                parsed = trees.get(rel)
                if parsed is not None:
                    facts = _python_handler(route, parsed, rel, line, models)
            else:
                text = _read(root, rel)
                if text is not None:
                    facts = _js_handler(route, text, rel, line)
        else:
            unsupported_routes.setdefault(framework or "unknown", []).append(
                route.get("evidence") or ""
            )
        params = _path_params(route["path"])
        if params:
            facts["request"]["path"] = {
                p: _fact({"type": "string", "required": True}, route.get("evidence") or "")
                for p in params
            }
        if not facts["read"] and framework in SHAPE_FRAMEWORKS:
            limits.add(f"{route['id']}: the handler at {route.get('evidence')} couldn't be read")
        operations.append(
            {
                "id": route["id"],
                "method": route["method"],
                "path": route["path"],
                "framework": framework,
                "handler": route.get("handler") or route.get("handler_name") or "",
                "evidence": route.get("evidence") or "",
                "source": "code",
                "auth": _dedupe(facts["auth"]),
                "request": facts["request"],
                "responses": dict(sorted(facts["responses"].items())),
                "shapes_read": bool(facts["read"]),
            }
        )
    for framework, where in sorted(unsupported_routes.items()):
        limits.add(
            f"{len(where)} {framework} routes are listed but their handlers aren't read for auth or shapes, "
            f"such as {where[0]}"
        )

    declared: list[dict[str, Any]] = []
    named: dict[str, dict[str, Any]] = {}
    spec_files: list[dict[str, Any]] = []
    for spec in specs or []:
        doc = load_spec(spec)  # an explicit --spec that can't be used is an error, not a limit
        rel = _rel(root, spec)
        ops, refs = _spec_operations(doc, rel, limits)
        declared += ops
        for name, schema in refs.named.items():
            named.setdefault(
                name,
                {**schema, "evidence": f"{rel}#/components/schemas/{name}", "status": "observed"},
            )
        spec_files.append(
            {
                "path": rel,
                "title": str((doc.get("info") or {}).get("title") or ""),
                "version": str(doc.get("openapi") or doc.get("swagger") or ""),
                "sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
                "operations": len(ops),
                "security_schemes": sorted(
                    (doc.get("components") or {}).get("securitySchemes")
                    or doc.get("securityDefinitions")
                    or {}
                ),
            }
        )

    used = _referenced(operations) | _referenced(declared)
    schemas = {name: m for name, m in models.found.items() if name in used}
    for name, schema in named.items():
        schemas.setdefault(name, schema)
    _close_over(schemas, models.found, named)

    frameworks = _unsupported(root, files)
    return {
        "operations": operations,
        "declared": declared,
        "schemas": dict(sorted(schemas.items())),
        "specs": spec_files,
        "unsupported_frameworks": frameworks,
        "limits": sorted(
            limits
            | {
                f"{f['framework']} routes aren't read at all (found at {f['evidence']}); their API isn't inventoried"
                for f in frameworks
            }
        ),
    }


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _dedupe(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out = []
    for f in facts:
        key = (str(f["value"]), f["evidence"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _refs_in(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and not ref.startswith(("#", "http", ".")):
            found.add(ref)
        for value in obj.values():
            found |= _refs_in(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _refs_in(value)
    return found


def _referenced(operations: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for op in operations:
        body = op["request"].get("body_schema")
        if body and isinstance(body["value"], str):
            names.add(body["value"])
        names |= _refs_in(op["request"])
        for resp in op["responses"].values():
            if isinstance(resp["value"], str):
                names.add(resp["value"])
            names |= _refs_in(resp["value"])
    return names


def _close_over(schemas: dict[str, Any], models: dict[str, Any], named: dict[str, Any]) -> None:
    pending = list(schemas)
    while pending:
        for ref in _refs_in(schemas[pending.pop()].get("properties") or {}):
            source = models.get(ref) or named.get(ref)
            if source is not None and ref not in schemas:
                schemas[ref] = source
                pending.append(ref)


# ---------------------------------------------------------------- merge, findings, contract


def _key(method: str, path: str) -> tuple[str, tuple[str, ...]]:
    return method, tuple("{}" if s.startswith("{") else s for s in _segments(path))


def merge(inv: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per operation: the OpenAPI declaration where there is one, the code's facts filling the gaps."""
    by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for op in inv["operations"]:
        by_key.setdefault(_key(op["method"], op["path"]), {**op, "code": op})
    for op in inv["declared"]:
        key = _key(op["method"], op["path"])
        match = by_key.get(key)
        if match is None:  # the code may mount the spec's paths under a prefix
            match = next(
                (
                    row
                    for k, row in by_key.items()
                    if k[0] == key[0] and len(k[1]) > len(key[1]) and k[1][-len(key[1]) :] == key[1]
                ),
                None,
            )
        if match is None:
            by_key[key] = {
                **op,
                "id": f"operation:{op['method']} {op['path']}",
                "source": "openapi",
                "spec": op,
            }
            continue
        code = match["code"]
        match.update(
            {
                "source": "openapi+code",
                "spec": op,
                "operation_id": op["operation_id"],
                "summary": op["summary"],
                "auth": op["auth"] if op["auth_declared"] else code["auth"],
                "code_auth": bool(code["auth"]),
                "request": {**code["request"], **op["request"]},
                "responses": dict(sorted({**code["responses"], **op["responses"]}.items())),
            }
        )
    rows = []
    for row in by_key.values():
        row = {k: v for k, v in row.items() if k not in {"code"}}
        rows.append(row)
    return sorted(rows, key=lambda r: (r["path"], r["method"]))


def _finding(
    rule: str,
    severity: str,
    op: dict[str, Any],
    message: str,
    remedy: str,
    status: str = "observed",
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "operation": f"{op['method']} {op['path']}",
        "message": message,
        "evidence": evidence or op.get("evidence") or "",
        "status": status,
        "remediation": remedy,
    }


def findings(
    root: Path, rows: list[dict[str, Any]], inv: dict[str, Any], files: list[str]
) -> list[dict[str, Any]]:
    calls = test_calls(root, files)
    texts: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for op in rows:
        codes = set(op["responses"])
        secured = bool(op["auth"])
        if not any(c.startswith("4") for c in codes) and op.get("shapes_read", True):
            out.append(
                _finding(
                    "missing-error-response",
                    "medium",
                    op,
                    "no 4xx response is documented or raised, so clients can't tell how it fails",
                    "document the failure statuses it can return (400 for bad input, 404 for a missing resource)"
                    " and test one",
                    "observed" if op["source"] != "code" else "inferred",
                )
            )
        if secured and not codes & {"401", "403"}:
            out.append(
                _finding(
                    "auth-without-401",
                    "high",
                    op,
                    f"it requires auth ({', '.join(str(a['value']) for a in op['auth'])}) but documents no 401 or 403",
                    "declare 401 (no or bad credentials) and 403 (not allowed) responses",
                )
            )
        spec = op.get("spec")
        code_auth = bool(op.get("code_auth"))
        if spec is not None and op["source"] == "openapi+code":
            declared = bool(spec["auth"])
            if declared != code_auth and spec["auth_declared"]:
                out.append(
                    _finding(
                        "auth-mismatch",
                        "high",
                        op,
                        "the OpenAPI file and the code disagree on whether it needs auth "
                        f"(declared: {'yes' if declared else 'no'}, code: {'yes' if code_auth else 'no auth seen'})",
                        "make the handler enforce what the contract declares, or correct the contract",
                        "inferred",
                    )
                )
        if not secured:
            continue
        shape = _segments(op["path"])
        hits = [
            c
            for c in calls
            if c["method"] == op["method"] and _matches(shape, _segments(c["path"]))
        ]
        if not hits:
            out.append(
                _finding(
                    "untested-auth",
                    "high",
                    op,
                    "it requires auth and no test calls it",
                    f"add a test that calls {op['method']} {op['path']} without credentials and expects 401 or 403",
                )
            )
            continue
        checked = False
        for hit in hits:
            rel, _ = _split_evidence(hit["evidence"])
            if rel not in texts:
                texts[rel] = _read(root, rel) or ""
            if re.search(r"\b40[13]\b|unauthori[sz]ed|forbidden", texts[rel], re.I):
                checked = True
                break
        if not checked:
            out.append(
                _finding(
                    "untested-auth",
                    "medium",
                    op,
                    "tests call it, but none checks the failed-auth response",
                    "add a case without credentials that expects 401 or 403",
                    "inferred",
                    hits[0]["evidence"],
                )
            )
    for fw in inv["unsupported_frameworks"]:
        out.append(
            {
                "rule": "unsupported-framework",
                "severity": "info",
                "operation": "",
                "message": f"{fw['framework']} routes aren't read, so their API isn't in this inventory",
                "evidence": fw["evidence"],
                "status": "observed",
                "remediation": "describe those services with an OpenAPI file and pass it with --spec",
            }
        )
    return sorted(
        out, key=lambda f: (_SEVERITY[f["severity"]], f["rule"], f["operation"], f["evidence"])
    )


_SEVERITY = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "api"


def model_contract(rows: list[dict[str, Any]], inv: dict[str, Any], name: str) -> dict[str, Any]:
    """The BMAD model contract (``contracts/model.py`` shape) for these operations."""
    rows = copy.deepcopy(rows)
    schemas: list[dict[str, Any]] = []
    for schema_name, schema in inv["schemas"].items():
        entry = {
            "name": schema_name,
            "type": schema.get("type") or "object",
            "description": schema.get("description") or "",
            "properties": schema.get("properties") or {},
            "required": schema.get("required") or [],
            **({"enum": schema["enum"]} if "enum" in schema else {}),
            "evidence": schema.get("evidence") or "",
            "status": schema.get("status") or "observed",
        }
        schemas.append(entry)
    operations = []
    error_codes: set[str] = set()
    for op in rows:
        opid = op.get("operation_id") or _slug(f"{op['method']}_{op['path']}")
        request_name = None
        body = op["request"].get("body_schema")
        if body is not None:
            if isinstance(body["value"], str):
                request_name = body["value"]
            else:
                request_name = f"{opid}_request"
                schemas.append(
                    {
                        "name": request_name,
                        "type": "object",
                        "description": f"{op['method']} {op['path']} body",
                        **_object(body["value"]),
                        "evidence": body["evidence"],
                        "status": body["status"],
                    }
                )
        elif op["request"].get("body"):
            request_name = f"{opid}_request"
            fields = op["request"]["body"]
            schemas.append(
                {
                    "name": request_name,
                    "type": "object",
                    "description": f"{op['method']} {op['path']} body",
                    "properties": {
                        k: {kk: vv for kk, vv in v["value"].items() if kk != "required"}
                        for k, v in sorted(fields.items())
                    },
                    "required": sorted(k for k, v in fields.items() if v["value"].get("required")),
                    "evidence": next(iter(fields.values()))["evidence"],
                    "status": "inferred"
                    if any(v["status"] == "inferred" for v in fields.values())
                    else "observed",
                }
            )
        responses: dict[str, str] = {}
        for code, resp in op["responses"].items():
            if code.startswith(("4", "5")):
                error_codes.add(code)
                responses[code] = "api_error"
            elif isinstance(resp["value"], str):
                responses[code] = resp["value"]
            elif resp["value"]:
                rname = f"{opid}_response_{code}"
                schemas.append(
                    {
                        "name": rname,
                        "type": "object",
                        **_object(resp["value"]),
                        "description": f"{op['method']} {op['path']} {code}",
                        "evidence": resp["evidence"],
                        "status": resp["status"],
                    }
                )
                responses[code] = rname
            else:
                responses[code] = ""
        params = {
            place: sorted(op["request"][place])
            for place in ("path", "query", "header", "cookie")
            if op["request"].get(place)
        }
        operations.append(
            {
                "operation_id": opid,
                "method": op["method"],
                "path": op["path"],
                "source": op["source"],
                "auth": sorted({str(a["value"]) for a in op["auth"]}),
                "parameters": params,
                "request": request_name,
                "responses": responses,
                "evidence": op["evidence"],
            }
        )
    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "contract_id": f"model-api-{_slug(name)}",
        "title": name,
        "schemas": sorted(schemas, key=lambda s: s["name"]),
        "compatibility_policy": "backward_compatible",
        "boundary_types": ["api_request", "api_response"],
        "error_schemas": [
            {
                "name": "api_error",
                "type": "object",
                "statuses": sorted(error_codes),
                "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
            }
        ],
        "operations": operations,
    }
    contract["digest"] = stable_digest(contract)
    return contract


def _object(schema: dict[str, Any]) -> dict[str, Any]:
    return (
        {"properties": schema.get("properties") or {}, "required": schema.get("required") or []}
        if (schema.get("properties") or schema.get("type") == "object")
        else {"properties": {"value": schema}, "required": []}
    )


def build(
    root: Path,
    api: dict[str, Any],
    files: list[str],
    specs: list[Path] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """The whole report: operations, findings, the model contract and a stable digest."""
    files = project_files(root, files)
    if specs is None:
        specs = [root / rel for rel in find_specs(root, files)]
        usable = []
        for spec in specs:
            try:
                load_spec(spec)
                usable.append(spec)
            except ApiContractError:
                continue
        specs = usable
    inv = inventory(root, api, files, specs)
    rows = merge(inv)
    if not rows:
        raise ApiContractError(
            f"no routes or OpenAPI operations were found under {root}; point --project at the service's folder or "
            "pass an OpenAPI file with --spec"
        )
    title = name or next((s["title"] for s in inv["specs"] if s["title"]), "") or root.name
    found = findings(root, rows, inv, files)
    report = {
        "schema": SCHEMA,
        "project": root.name,
        "summary": {
            "operations": len(rows),
            "from_code": sum(1 for r in rows if r["source"] == "code"),
            "from_openapi": sum(1 for r in rows if r["source"] == "openapi"),
            "both": sum(1 for r in rows if r["source"] == "openapi+code"),
            "secured": sum(1 for r in rows if r["auth"]),
            "schemas": len(inv["schemas"]),
            "findings": len(found),
        },
        "operations": [{k: v for k, v in r.items() if k != "spec"} for r in rows],
        "schemas": inv["schemas"],
        "specs": inv["specs"],
        "findings": found,
        "unsupported_frameworks": inv["unsupported_frameworks"],
        "model_contract": model_contract(rows, inv, title),
        "limits": [
            *inv["limits"],
            "Auth is recognised by name (Depends(get_current_user), @login_required, requireAuth middleware); "
            "auth applied to a whole app or router elsewhere isn't seen.",
            "Inferred fields come from how the handler reads its input; a field read in another function isn't seen.",
            "Recommended remedies are suggestions; nothing was run or written.",
        ],
    }
    report["digest"] = stable_digest(report)
    return report


def to_yaml(contract: dict[str, Any]) -> str:
    """The model contract as YAML with no anchors, so every schema reads on its own."""
    import yaml

    class _Plain(yaml.SafeDumper):  # type: ignore[misc]
        def ignore_aliases(self, data: Any) -> bool:
            return True

    return str(yaml.dump(contract, Dumper=_Plain, sort_keys=False, allow_unicode=True))


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# API contract: {report['model_contract']['title']}",
        "",
        f"{s['operations']} operations ({s['from_code']} from code only, {s['from_openapi']} from OpenAPI only, "
        f"{s['both']} in both), {s['secured']} secured, {s['schemas']} schemas, {s['findings']} findings.",
        "",
        "| Operation | Auth | Request | Responses | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for op in report["operations"]:
        req = op["request"]
        parts = []
        if req.get("body_schema"):
            value = req["body_schema"]["value"]
            parts.append(f"body {value if isinstance(value, str) else 'inline'}")
        elif req.get("body"):
            parts.append("body " + ", ".join(sorted(req["body"])))
        for place in ("path", "query", "header"):
            if req.get(place):
                parts.append(f"{place} " + ", ".join(sorted(req[place])))
        auth = ", ".join(sorted({str(a["value"]) for a in op["auth"]})) or "none seen"
        lines.append(
            f"| `{op['method']} {op['path']}` | {auth} | {'; '.join(parts) or '-'} | "
            f"{', '.join(op['responses']) or '-'} | `{op['evidence']}` |"
        )
    if report["findings"]:
        lines += ["", "## Findings", ""]
        for f in report["findings"]:
            target = f" `{f['operation']}`" if f["operation"] else ""
            lines.append(
                f"- **{f['severity']}** {f['rule']}{target}: {f['message']} ({f['status']}, `{f['evidence']}`). "
                f"Fix: {f['remediation']}."
            )
    lines += [
        "",
        "## Limits",
        "",
        *(f"- {x}" for x in report["limits"]),
        "",
        f"Digest: `{report['digest']}`",
        "",
    ]
    return "\n".join(lines)

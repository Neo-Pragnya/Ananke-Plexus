"""Languages, size, source roots and components, with the reason for each component's kind.

A component comes from a signal in the repository: a compose service, a Dockerfile, a manifest in a folder, a Python package, a
notebooks or tests folder, SQL migrations. Each has an ``evidence`` field saying which. See docs/reference/repo-intelligence.md.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ananke.plexus.intel import dependencies
from ananke.plexus.intel._common import (
    TEST_DIRS,
    count_loc,
    deepest_folder,
    is_fixture,
    is_test_file,
    language_of,
    read_text,
    some,
)
from ananke.plexus.intel.context import RepoContext

_COMPOSE_NAMES = ("docker-compose", "compose")
_MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "setup.py"}
_DATABASE_IMAGES = {
    "postgres", "postgresql", "postgis", "pgvector", "timescaledb", "mysql", "mariadb", "percona", "mongo", "mongodb",
    "redis", "valkey", "memcached", "cassandra", "scylla", "elasticsearch", "opensearch", "clickhouse", "neo4j",
    "cockroachdb", "cockroach", "qdrant", "weaviate", "milvus", "chroma", "chromadb", "minio", "dynamodb", "couchdb",
    "influxdb", "sqlserver", "mssql", "oracle",
}  # fmt: skip
# ``redis-commander`` is a tool for a database, not a database.
_TOOL_WORDS = {"commander", "insight", "exporter", "ui", "admin", "client", "dashboard", "cli"}
_WORKER_NAMES = {
    "worker",
    "workers",
    "jobs",
    "job",
    "celery",
    "consumer",
    "consumers",
    "scheduler",
    "cron",
}
_FRONTEND_NAMES = {"frontend", "front", "web", "webapp", "ui", "client", "website", "site", "spa"}
_WORKER_PACKAGES = {"celery", "rq", "dramatiq", "huey", "arq", "bullmq", "bull", "taskiq"}
_FRONTEND_PACKAGES = {
    "react", "react-dom", "vue", "svelte", "@angular/core", "next", "nuxt", "astro", "solid-js", "preact", "vite", "gatsby",
    "@sveltejs/kit", "@remix-run/react",
}  # fmt: skip
_SERVER_PACKAGES = {
    "fastapi", "flask", "django", "starlette", "aiohttp", "tornado", "sanic", "litestar", "falcon", "bottle", "uvicorn",
    "gunicorn", "express", "fastify", "koa", "@nestjs/core", "@hapi/hapi",
}  # fmt: skip
_NOT_COMPONENTS = {
    "docs",
    "doc",
    "examples",
    "example",
    "samples",
    "sample",
    "templates",
    "template",
}
_DATABASE_FOLDERS = {"db", "database", "databases", "migrations", "sql", "schema", "schemas"}
_ROOT_MANIFESTS = ("pyproject.toml", "package.json", "setup.py", "requirements.txt")
_NOT_BUILT = {"tests", "notebooks", "database"}
_MAX_MANIFEST_DEPTH = 2
_SEPARATE_PROJECTS = 3


@dataclass
class ComposeService:
    file: str
    name: str
    line: int
    path: str | None
    image: str | None
    command: str
    depends_on: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class _Component:
    name: str
    path: str
    kind: str
    evidence: str
    language: str | None = None
    files: int = 0
    loc: int = 0
    keep_when_empty: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": f"component:{self.name}",
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "language": self.language,
            "files": self.files,
            "loc": self.loc,
            "evidence": self.evidence,
        }


def _words(name: str) -> set[str]:
    return set(name.lower().replace("_", "-").replace(".", "-").split("-"))


def is_database_image(image: str | None) -> bool:
    if not image:
        return False
    base = image.rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0]
    words = base.lower().replace("_", "-").split("-")
    return words[0] in _DATABASE_IMAGES and not (set(words) & _TOOL_WORDS)


def _line_of(lines: list[str], text: str, start: int = 0) -> int:
    """The 1-based line holding ``text`` as a word at or after ``start``. Falls back to ``start + 1``."""
    for index in range(start, len(lines)):
        line = lines[index]
        at = line.find(text)
        while at >= 0:
            before = line[at - 1] if at else " "
            after_at = at + len(text)
            after = line[after_at] if after_at < len(line) else " "
            if not (before.isalnum() or before in "-_") and not (after.isalnum() or after in "-_"):
                return index + 1
            at = line.find(text, at + 1)
    return start + 1


def _key_line(lines: list[str], key: str) -> int:
    """The 1-based line where a mapping key is declared, quoted or not. ``1`` if it can't be seen."""
    forms = (f"{key}:", f'"{key}":', f"'{key}':")
    return next((i for i, line in enumerate(lines, start=1) if line.strip().startswith(forms)), 1)


def _depends_line(lines: list[str], service_line: int) -> int:
    """Where to look for a service's dependencies from: its ``depends_on``, not its environment."""
    for index in range(service_line, len(lines)):
        if lines[index].strip().startswith("depends_on"):
            return index
    return service_line


def _build_path(file: str, spec: dict[str, Any], limits: list[str]) -> str | None:
    """The folder a compose service builds, relative to the repository root."""
    build = spec.get("build")
    context: object = build
    dockerfile: object = None
    if isinstance(build, dict):
        context, dockerfile = build.get("context", "."), build.get("dockerfile")
    if not isinstance(context, str):
        return None
    base = file.rsplit("/", 1)[0] if "/" in file else "."
    path = posixpath.normpath(posixpath.join(base, context))
    if path.startswith("..") or posixpath.isabs(path) or "://" in context:
        limits.append(f"{file} builds from {context}, outside the repository")
        return None
    # ``context: .`` with ``dockerfile: backend/Dockerfile`` builds the backend.
    if isinstance(dockerfile, str) and "/" in dockerfile:
        nested = posixpath.normpath(posixpath.join(path, dockerfile.rsplit("/", 1)[0]))
        if not nested.startswith(".."):
            return nested
    return path


def compose_services(ctx: RepoContext) -> tuple[list[ComposeService], list[str]]:
    """The services every compose file declares. Only ``safe_load``, and only named fields are read."""
    import yaml

    limits: list[str] = []
    services: list[ComposeService] = []
    files = [
        p
        for p in ctx.files
        if p.rsplit("/", 1)[-1].startswith(_COMPOSE_NAMES)
        and p.endswith((".yml", ".yaml"))
        and not is_fixture(p)
        and not _unusual_folder(p)
    ]
    # The main file first, so its names win over a dev override's.
    files.sort(key=lambda p: (p.count("/"), len(p.rsplit("/", 1)[-1]), p))
    for file in files:
        text = read_text(ctx, file)
        if text is None:
            limits.append(f"{file} couldn't be read: too large, or outside the repository")
            continue
        try:
            data = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError, ValueError) as exc:
            limits.append(f"{file} couldn't be read: {type(exc).__name__}")
            continue
        declared = data.get("services") if isinstance(data, dict) else None
        if not isinstance(declared, dict):
            continue
        lines = text.splitlines()
        for name, spec in declared.items():
            if not isinstance(spec, dict):
                continue
            name = str(name)
            line = _key_line(lines, name)
            raw_deps = spec.get("depends_on")
            deps = list(raw_deps) if isinstance(raw_deps, (list, dict)) else []
            image, command = spec.get("image"), spec.get("command")
            # Only strings are kept. YAML aliases can make a small file's other values enormous as text.
            words = (
                [c for c in command if isinstance(c, str)]
                if isinstance(command, list)
                else [command]
            )
            services.append(
                ComposeService(
                    file=file,
                    name=name,
                    line=line,
                    path=_build_path(file, spec, limits) if "build" in spec else None,
                    image=image if isinstance(image, str) else None,
                    command=" ".join(w for w in words if isinstance(w, str)),
                    depends_on=[
                        (d, _line_of(lines, d, _depends_line(lines, line)))
                        for d in deps
                        if isinstance(d, str)
                    ],
                )
            )
    return services, limits


def _unusual_folder(path: str) -> bool:
    parts = path.split("/")[:-1]
    return any(p in _NOT_COMPONENTS or p in TEST_DIRS or p.startswith(".") for p in parts)


def _is_worker_name(name: str) -> bool:
    """``worker`` or ``email-worker``, but not ``job-board``."""
    words = name.lower().replace("_", "-").split("-")
    return words[-1] in _WORKER_NAMES or words[0] in {"worker", "workers", "celery"}


def _kind(
    ctx: RepoContext, name: str, folder: str, *, runs: str | None, command: str = ""
) -> tuple[str, str]:
    """The kind and why. ``runs`` says what shows it runs on its own (compose, a Dockerfile)."""
    declared, _ = dependencies.declared_in(ctx, folder)
    packages = {p.name.lower(): p for p in declared}
    prefix = "" if folder == "." else f"{folder}/"
    has_node = f"{prefix}package.json" in ctx.files
    # At the root, the name is the project's, and says nothing about what the code does.
    named = [name, folder.rsplit("/", 1)[-1]] if folder != "." else []
    words = set().union(*(_words(n) for n in named)) if named else set()

    worker = next((n for n in named if _is_worker_name(n)), None)
    if worker:
        return "worker", f"named {worker}"
    if "celery" in command:
        return "worker", f"its command runs celery: {runs}"
    hit = sorted(set(packages) & _FRONTEND_PACKAGES)
    if hit:
        return "frontend", f"depends on {hit[0]}: {packages[hit[0]].manifest}"
    hit = sorted(words & _FRONTEND_NAMES)
    if hit and has_node:
        return "frontend", f"named {hit[0]}, with {prefix}package.json"
    hit = sorted(set(packages) & _SERVER_PACKAGES)
    if hit:
        return "service", f"depends on {hit[0]}: {packages[hit[0]].manifest}"
    hit = sorted(set(packages) & _WORKER_PACKAGES)
    if hit:
        return "worker", f"depends on {hit[0]}: {packages[hit[0]].manifest}"
    if runs:
        return "service", runs
    if packages:
        return (
            "library",
            f"a manifest with no server framework: {next(iter(packages.values())).manifest}",
        )
    return "library", f"source files with no sign that it runs on its own: {folder}"


def _python_roots(files: list[str]) -> list[str]:
    """Folders Python imports resolve from: above the topmost package, or ``src`` in a src layout."""
    packages = {p.rsplit("/", 1)[0] for p in files if p.endswith("/__init__.py")}
    roots = set()
    for folder in packages:
        top = folder
        while "/" in top and top.rsplit("/", 1)[0] in packages:
            top = top.rsplit("/", 1)[0]
        root = top.rsplit("/", 1)[0] if "/" in top else "."
        parts = root.split("/")
        if "src" in parts:
            root = "/".join(parts[: parts.index("src") + 1])
        if root == "." or root.count("/") < _MAX_MANIFEST_DEPTH or root.endswith("/src"):
            roots.add(root)
    return sorted(roots)


def _source_roots(sources: list[str]) -> list[str]:
    python = [p for p in sources if p.endswith(".py") and not is_test_file(p)]
    roots = _python_roots(python)
    if roots:
        return roots
    if any("/" not in p for p in python):
        return ["."]
    if python:
        return sorted({p.split("/", 1)[0] for p in python if p.count("/") == 1})
    # With no Python, ``src`` folders are where JavaScript and TypeScript start from.
    scripts = [p for p in sources if language_of(p) in {"typescript", "javascript"}]
    return sorted(
        {p.split("/src/", 1)[0] + "/src" for p in scripts if "/src/" in p}
        | ({"src"} if any(p.startswith("src/") for p in scripts) else set())
    )


def _unique(name: str, path: str, taken: dict[str, _Component]) -> str:
    return name if name not in taken else path.replace("/", "-")


def _notebook(ctx: RepoContext, path: str, limits: list[str]) -> tuple[str, int]:
    """A notebook's language and lines of code, from the document itself."""
    text = read_text(ctx, path)
    if text is None:
        return "python", 0
    try:
        data = json.loads(text)
        meta = data["metadata"]
        language = (
            meta.get("kernelspec", {}).get("language")
            or meta.get("language_info", {}).get("name")
            or "python"
        )
        code = 0
        for cell in data.get("cells", []):
            if cell.get("cell_type") == "code":
                source = cell.get("source", "")
                joined = "".join(source) if isinstance(source, list) else str(source)
                code += count_loc(joined)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        limits.append(f"{path} couldn't be read: {type(exc).__name__}")
        return "python", 0
    return str(language).lower(), code


def analyze(ctx: RepoContext) -> dict[str, Any]:
    components, source_roots, separate, files, loc, languages, limits = _survey(ctx)
    status = "found" if files else "none"
    return {
        "status": status,
        "languages": languages,
        "files": files,
        "loc": loc,
        "source_roots": source_roots,
        "components": [c.as_dict() for c in components],
        "separate_projects": separate,
        "limits": limits,
    }


def _survey(
    ctx: RepoContext,
) -> tuple[list[_Component], list[str], list[str], int, int, dict[str, int], list[str]]:
    limits: list[str] = []
    measured = [p for p in ctx.files if not is_fixture(p)]
    if len(measured) < len(ctx.files):
        limits.append(f"{len(ctx.files) - len(measured)} files in fixture folders aren't counted")

    languages: dict[str, int] = {}
    sizes: dict[str, tuple[str, int]] = {}
    for path in measured:
        language = language_of(path)
        if language is None:
            continue
        if path.endswith(".ipynb"):
            language, size = _notebook(ctx, path, limits)
        else:
            text = read_text(ctx, path)
            size = count_loc(text) if text is not None else 0
        sizes[path] = (language, size)
        languages[language] = languages.get(language, 0) + 1
    sources = sorted(sizes)
    source_roots = _source_roots(sources)

    services, compose_limits = compose_services(ctx)
    limits += compose_limits
    components = _find_components(ctx, measured, sources, source_roots, services, limits)

    # Give each source file to its deepest component, then count.
    by_path: dict[str, list[_Component]] = {}
    for component in components:
        by_path.setdefault(component.path, []).append(component)
    counted: dict[str, dict[str, float]] = {}
    unowned: list[str] = []
    totals = {path: [0, 0] for path in by_path}
    separate = _separate_projects(measured, components)
    for path in sources:
        if path.split("/", 1)[0] in separate:
            unowned.append(path)
            continue
        owner = deepest_folder(path, by_path)
        if owner is None:
            unowned.append(path)
            continue
        language, size = sizes[path]
        totals[owner][0] += 1
        totals[owner][1] += size
        tally = counted.setdefault(owner, {})
        # What a component is written in: its code, not its test scripts, markup or styles.
        weight = 0.001 if is_test_file(path) or language in {"html", "css"} else 1.0
        tally[language] = tally.get(language, 0) + weight

    kept = []
    for component in components:
        tally = counted.get(component.path, {})
        component.files, component.loc = (
            (totals[component.path][0], totals[component.path][1]) if component.path else (0, 0)
        )
        component.language = (
            sorted(tally.items(), key=lambda item: (-item[1], item[0]))[0][0] if tally else None
        )
        if component.files or component.keep_when_empty:
            kept.append(component)

    if separate:
        limits.append(
            f"{some(sorted(separate))} holds many separate projects, each with its own manifest, so "
            f"it isn't treated as a component"
        )
    if unowned and not separate:
        limits.append(f"{len(unowned)} source files belong to no component: {some(unowned)}")
    kept.sort(key=lambda c: (c.path == "", c.path, c.name))
    return (
        kept,
        source_roots,
        sorted(separate),
        len(measured),
        sum(size for _, size in sizes.values()),
        dict(sorted(languages.items())),
        limits,
    )


def _separate_projects(files: list[str], components: list[_Component]) -> set[str]:
    """Top-level folders holding several manifests too deep to be components: vendored or collected projects."""
    counts: dict[str, int] = {}
    known = {c.path.split("/", 1)[0] for c in components if c.path not in {"", "."}}
    for path in files:
        parts = path.split("/")
        if parts[-1] in _MANIFESTS and len(parts) - 1 > _MAX_MANIFEST_DEPTH:
            counts[parts[0]] = counts.get(parts[0], 0) + 1
    return {
        top for top, count in counts.items() if count >= _SEPARATE_PROJECTS and top not in known
    }


def _find_components(
    ctx: RepoContext,
    measured: list[str],
    sources: list[str],
    source_roots: list[str],
    services: list[ComposeService],
    limits: list[str],
) -> list[_Component]:
    named: dict[str, _Component] = {}
    taken_paths: set[str] = set()

    # Compose first: it says outright what runs.
    for service in services:
        where = f"{service.file}:{service.line}"
        existing = named.get(service.name)
        if service.path is not None:
            if existing is not None:
                continue
            sharing = [c for c in named.values() if c.path == service.path]
            if sharing and sharing[0].name not in {
                s.name for s in services if s.file == service.file
            }:
                continue  # the same service under another name in a dev override
            kind, why = _kind(
                ctx,
                service.name,
                service.path,
                runs=f"a compose service that builds this folder: {where}",
                command=service.command,
            )
            named[service.name] = _Component(
                service.name, service.path, kind, why, keep_when_empty=True
            )
            taken_paths.add(service.path)
        elif existing is None:
            if is_database_image(service.image):
                kind, why = "database", f"compose image {service.image}: {where}"
            else:
                kind, why = "service", f"compose image {service.image}: {where}"
            named[service.name] = _Component(service.name, "", kind, why, keep_when_empty=True)

    # Manifests and Dockerfiles in folders.
    folders: dict[str, str | None] = {}
    for path in measured:
        folder, name = path.rsplit("/", 1) if "/" in path else (".", path)
        if name == "Dockerfile" or name.startswith("Dockerfile."):
            folders[folder] = folders.get(folder) or path
        elif name in _MANIFESTS:
            folders.setdefault(folder, None)
    for folder, docker in sorted(folders.items()):
        if (
            folder == "."
            or folder in taken_paths
            or folder.count("/") >= _MAX_MANIFEST_DEPTH
            or _unusual_folder(f"{folder}/x")
        ):
            continue
        base = folder.rsplit("/", 1)[-1]
        runs = f"a Dockerfile: {docker}" if docker else None
        kind, why = _kind(ctx, base, folder, runs=runs)
        name = _unique(base, folder, named)
        named[name] = _Component(name, folder, kind, why)
        taken_paths.add(folder)

    # Folders with a role of their own, where nothing above already covers them.
    within = sorted(p for p in taken_paths if p != ".")

    def covered(folder: str) -> bool:
        return any(folder == p or folder.startswith(p + "/") for p in within)

    _add_role_folders(named, sources, covered, taken_paths)

    # Python packages in folders with no manifest.
    for root in source_roots:
        if root in {".", "src"} or root in taken_paths or covered(root) or root.endswith("/src"):
            continue
        if _unusual_folder(f"{root}/x"):
            continue
        base = root.rsplit("/", 1)[-1]
        kind, why = _kind(ctx, base, root, runs=None)
        name = _unique(base, root, named)
        named[name] = _Component(name, root, kind, why)
        taken_paths.add(root)

    _add_root(ctx, named, sources, measured)
    return list(named.values())


def _add_role_folders(
    named: dict[str, _Component],
    sources: list[str],
    covered: Callable[[str], bool],
    taken_paths: set[str],
) -> None:
    candidates: dict[str, list[str]] = {}
    for path in sources:
        parts = path.split("/")
        for depth in range(1, min(len(parts), _MAX_MANIFEST_DEPTH + 1)):
            candidates.setdefault("/".join(parts[:depth]), []).append(path)

    for folder, held in sorted(candidates.items()):
        if covered(folder) or folder in taken_paths or _hidden(folder) or _unusual_folder(folder):
            continue
        base = folder.rsplit("/", 1)[-1]
        sql = [p for p in held if p.endswith(".sql")]
        notebooks = [p for p in held if p.endswith(".ipynb")]
        if base in TEST_DIRS and not _under_taken(folder, taken_paths):
            kind, why = "tests", f"a folder of tests: {folder}"
        elif base in {"notebooks", "notebook"} and notebooks:
            kind, why = "notebooks", f"notebooks: {notebooks[0]}"
        elif "/" not in folder and notebooks and len(notebooks) * 2 > len(held):
            kind, why = "notebooks", f"mostly notebooks: {notebooks[0]}"
        elif (
            sql
            and len(sql) * 2 > len(held)
            and (base in _DATABASE_FOLDERS or "/migrations/" in sql[0])
        ):
            kind, why = "database", f"SQL schema or migrations: {sql[0]}"
        else:
            continue
        if _under_taken(folder, taken_paths):
            continue
        if kind == "database":
            target = _database_for(named, base)
            if target is not None:
                target.path = folder
                taken_paths.add(folder)
                continue
        name = _unique(base, folder, named)
        named[name] = _Component(name, folder, kind, why)
        taken_paths.add(folder)


def _hidden(folder: str) -> bool:
    return any(part.startswith(".") for part in folder.split("/"))


def _under_taken(folder: str, taken_paths: set[str]) -> bool:
    return any(folder.startswith(p + "/") for p in taken_paths if p != ".")


def _database_for(named: dict[str, _Component], base: str) -> _Component | None:
    """The compose database a schema folder belongs to: the same name, or the only one there is."""
    databases = [c for c in named.values() if c.kind == "database" and not c.path]
    same = [c for c in databases if c.name == base]
    if same:
        return same[0]
    return databases[0] if len(databases) == 1 else None


def _add_root(
    ctx: RepoContext, named: dict[str, _Component], sources: list[str], measured: list[str]
) -> None:
    """The component for what's left at the root: named by its manifest, or after the repository."""
    if any(c.path == "." for c in named.values()):
        return
    present = set(measured)
    manifest = next((m for m in _ROOT_MANIFESTS if m in present), None)
    docker = "Dockerfile" in present
    # With no manifest, and folders of their own, loose scripts don't make a component.
    built = [c for c in named.values() if c.path and c.kind not in _NOT_BUILT]
    if not sources or (manifest is None and not docker and built):
        return
    base = _root_name(ctx, manifest)
    name = _unique(base, "root", named)
    held = [
        p for p in sources if not any(c.path and p.startswith(c.path + "/") for c in named.values())
    ]
    if held and all(p.endswith(".ipynb") for p in held):
        kind, why = "notebooks", f"notebooks: {held[0]}"
    else:
        kind, why = _kind(ctx, base, ".", runs="a Dockerfile: Dockerfile" if docker else None)
    named[name] = _Component(name, ".", kind, why)


def _root_name(ctx: RepoContext, manifest: str | None) -> str:
    """A root component's name: the manifest's, or the repository folder's."""
    import tomllib

    text = read_text(ctx, manifest) if manifest in {"pyproject.toml", "package.json"} else None
    name: object = None
    if text is not None:
        try:
            data = tomllib.loads(text) if manifest == "pyproject.toml" else json.loads(text)
            if manifest == "pyproject.toml":
                name = data.get("project", {}).get("name") or data.get("tool", {}).get(
                    "poetry", {}
                ).get("name")
            elif isinstance(data, dict):
                name = data.get("name")
        except (ValueError, AttributeError, RecursionError):
            name = None
    return name if isinstance(name, str) and name.strip() else (ctx.root.name or "repository")

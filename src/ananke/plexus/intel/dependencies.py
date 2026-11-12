"""The package inventory, read from manifests and lockfiles, and a CVE lookup against OSV when asked.

Lock data beats a range, because a range doesn't say what's installed. A package with no pinned version has ``version: null``
and can't be checked. ``vulnerabilities`` is ``null`` unless a scan happened, so neither a skipped nor a failed scan reads as
clean. See docs/reference/repo-intelligence.md.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from typing import Any

from ananke.plexus.intel._common import is_fixture, read_text, some
from ananke.plexus.intel.context import RepoContext

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/"
USER_AGENT = "ananke-repo-intel/1"
OSV_TIMEOUT = 20
OSV_BATCH_SIZE = 500
OSV_MAX_RESPONSE = 32_000_000
OSV_MAX_PAGES = 10
OSV_MAX_DETAILS = 2000
OSV_WORKERS = 8
MAX_LOCK_BYTES = 64_000_000

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_NORMAL = re.compile(r"[-_.]+")
_NPM_EXACT = re.compile(r"v?=?(\d+\.\d+\.\d+[0-9A-Za-z.+-]*)")
_POETRY_EXACT = re.compile(r"=?=?(\d[0-9A-Za-z.!+-]*)")
_VERSION_TOKEN = re.compile(r"\d+|[a-z]+")
_DEV_WORDS = {"dev", "test", "tests", "testing", "docs", "doc", "lint", "ci", "typing", "build"}
_UNREAD_MANIFESTS = {
    "go.mod", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "composer.lock",
}  # fmt: skip
_SEVERITY_WORDS = {
    "critical": "critical", "high": "high", "moderate": "medium", "medium": "medium", "low": "low",
}  # fmt: skip


@dataclass
class _Package:
    ecosystem: str
    name: str
    version: str | None
    manifest: str
    direct: bool
    dev: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "name": self.name,
            "version": self.version,
            "manifest": self.manifest,
            "direct": self.direct,
            "dev": self.dev,
        }


def _pypi_name(name: str) -> str:
    return _NORMAL.sub("-", name.strip()).lower()


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _is_dev_group(name: str) -> bool:
    return any(word in _DEV_WORDS for word in re.split(r"[-_.\s]", name.lower()))


def parse_requirement(line: str) -> tuple[str, str | None] | None:
    """``(name, exact version or None)`` for one PEP 508 line. String steps, not one big pattern."""
    line = line.split(";", 1)[0].split(" #", 1)[0].split(" --", 1)[0].strip()
    match = _NAME.match(line)
    if not match:
        return None
    name, rest = match.group(), line[match.end() :].lstrip()
    if rest.startswith("["):
        close = rest.find("]")
        rest = rest[close + 1 :].lstrip() if close >= 0 else ""
    if rest.startswith("@"):
        return _pypi_name(name), None
    if rest and rest[0] not in "=<>!~(":
        return None  # a path or a URL, not a package
    specs = [s.strip() for s in rest.strip("() ").split(",") if s.strip()]
    if len(specs) == 1 and specs[0].startswith("=="):
        version = specs[0].lstrip("=").strip()
        if version and "*" not in version:
            return _pypi_name(name), version
    return _pypi_name(name), None


def _read_requirements(text: str, path: str, limits: list[str]) -> list[_Package]:
    stem = PurePosixPath(path).stem
    dev = _is_dev_group(stem) or _is_dev_group(_folder(path).rsplit("/", 1)[-1])
    found = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.strip()
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            included = line.split(maxsplit=1)[1].rsplit("/", 1)[-1]
            if not included.startswith("requirements"):
                limits.append(f"{path} includes {included}, which isn't read")
            continue
        if not line or line.startswith(("#", "-")) or line.startswith(("http:", "https:", "git+")):
            continue
        parsed = parse_requirement(line)
        if parsed:
            found.append(_Package("PyPI", parsed[0], parsed[1], path, True, dev))
    return found


def _toml(text: str, path: str, limits: list[str]) -> dict[str, Any] | None:
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError) as exc:
        limits.append(f"{path} couldn't be read: {type(exc).__name__}")
        return None


def _json(text: str, path: str, limits: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError) as exc:
        limits.append(f"{path} couldn't be read: {type(exc).__name__}")
        return None
    if not isinstance(payload, dict):
        limits.append(f"{path} couldn't be read: not an object")
        return None
    return payload


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _poetry_version(spec: object) -> str | None:
    if isinstance(spec, dict):
        spec = spec.get("version")
    if not isinstance(spec, str):
        return None
    match = _POETRY_EXACT.fullmatch(spec.strip())
    return match.group(1) if match else None


def _read_pyproject(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _toml(text, path, limits)
    if data is None:
        return []
    found: list[_Package] = []

    def add_lines(lines: object, dev: bool) -> None:
        for line in _strings(lines):
            parsed = parse_requirement(line)
            if parsed:
                found.append(_Package("PyPI", parsed[0], parsed[1], path, True, dev))

    project = _dict(data.get("project"))
    own_name = _pypi_name(str(project.get("name", "")))
    add_lines(project.get("dependencies"), False)
    for group, lines in _dict(project.get("optional-dependencies")).items():
        add_lines(lines, _is_dev_group(group))
    for lines in _dict(data.get("dependency-groups")).values():
        add_lines(lines, True)
    tool = _dict(data.get("tool"))
    add_lines(_dict(tool.get("uv")).get("dev-dependencies"), True)

    poetry = _dict(tool.get("poetry"))

    def add_poetry(table: object, dev: bool) -> None:
        for name, spec in _dict(table).items():
            if name.lower() != "python":
                found.append(
                    _Package("PyPI", _pypi_name(name), _poetry_version(spec), path, True, dev)
                )

    add_poetry(poetry.get("dependencies"), False)
    add_poetry(poetry.get("dev-dependencies"), True)
    for group, table in _dict(poetry.get("group")).items():
        add_poetry(_dict(table).get("dependencies"), group != "main")
    # ``all = ["me[a,b]"]`` is how an extra pulls in others, and isn't a dependency on itself.
    return [p for p in found if p.name != own_name]


def _read_pipfile(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _toml(text, path, limits)
    if data is None:
        return []
    found = []
    for table, dev in (("packages", False), ("dev-packages", True)):
        for name, spec in _dict(data.get(table)).items():
            version = spec.get("version") if isinstance(spec, dict) else spec
            parsed = parse_requirement(f"{name}{version}") if isinstance(version, str) else None
            pin = parsed[1] if parsed else None
            found.append(_Package("PyPI", _pypi_name(name), pin, path, True, dev))
    return found


def _npm_version(spec: object) -> str | None:
    if not isinstance(spec, str):
        return None
    match = _NPM_EXACT.fullmatch(spec.strip())
    return match.group(1) if match else None


def _read_package_json(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _json(text, path, limits)
    if data is None:
        return []
    found = []
    for table, dev in (
        ("dependencies", False),
        ("optionalDependencies", False),
        ("devDependencies", True),
    ):
        for name, spec in _dict(data.get(table)).items():
            if isinstance(spec, str) and spec.startswith(("workspace:", "file:", "link:")):
                continue
            found.append(_Package("npm", name, _npm_version(spec), path, True, dev))
    return found


def _reachable(start: Iterable[str], edges: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(edges.get(name, ()))
    return seen


def _read_uv_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _toml(text, path, limits)
    if data is None:
        return []
    entries = (
        [p for p in data.get("package", []) if isinstance(p, dict)]
        if isinstance(data.get("package"), list)
        else []
    )
    edges: dict[str, set[str]] = {}
    runtime: set[str] = set()
    direct: set[str] = set()
    members: set[str] = set()

    def names(items: object) -> set[str]:
        listed = items if isinstance(items, list) else []
        return {_pypi_name(str(i["name"])) for i in listed if isinstance(i, dict) and "name" in i}

    for entry in entries:
        name = _pypi_name(str(entry.get("name", "")))
        deps = names(entry.get("dependencies"))
        extras = _dict(entry.get("optional-dependencies"))
        groups = _dict(entry.get("dev-dependencies"))
        edges.setdefault(name, set()).update(deps)
        for items in extras.values():
            edges[name].update(names(items))
        source = _dict(entry.get("source"))
        if "virtual" in source or "editable" in source:
            members.add(name)
            runtime |= deps
            direct |= deps
            for group, items in extras.items():
                direct |= names(items)
                if not _is_dev_group(group):
                    runtime |= names(items)
            for items in groups.values():
                direct |= names(items)
    runtime = _reachable(runtime, edges)
    found = []
    for entry in entries:
        name = _pypi_name(str(entry.get("name", "")))
        source = _dict(entry.get("source"))
        if name in members or "path" in source or "directory" in source or not name:
            continue
        version = entry.get("version")
        found.append(
            _Package(
                "PyPI",
                name,
                version if isinstance(version, str) else None,
                path,
                name in direct,
                name not in runtime,
            )
        )
    return found


def _read_poetry_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _toml(text, path, limits)
    if data is None:
        return []
    found = []
    entries = data.get("package")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        if _dict(entry.get("source")).get("type") in {"directory", "file"}:
            continue
        groups = _strings(entry.get("groups"))
        dev = entry.get("category") == "dev" or (bool(groups) and "main" not in groups)
        version = entry.get("version")
        found.append(
            _Package(
                "PyPI",
                _pypi_name(entry["name"]),
                version if isinstance(version, str) else None,
                path,
                False,
                dev,
            )
        )
    return found


def _read_pipfile_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _json(text, path, limits)
    if data is None:
        return []
    found: dict[str, _Package] = {}
    for table, dev in (("default", False), ("develop", True)):
        for name, entry in _dict(data.get(table)).items():
            raw = _dict(entry).get("version")
            version = raw.lstrip("=") if isinstance(raw, str) else None
            key = _pypi_name(name)
            if key not in found:
                found[key] = _Package("PyPI", key, version, path, False, dev)
    return list(found.values())


def _read_package_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    data = _json(text, path, limits)
    if data is None:
        return []
    found = []
    packages = _dict(data.get("packages"))
    if packages:
        root = _dict(packages.get(""))
        direct = set(_dict(root.get("dependencies"))) | set(_dict(root.get("devDependencies")))
        direct |= set(_dict(root.get("optionalDependencies")))
        for key, entry in packages.items():
            marker = "node_modules/"
            at = key.rfind(marker)
            info = _dict(entry)
            if at < 0 or info.get("link"):
                continue
            name = (
                info.get("name") if isinstance(info.get("name"), str) else key[at + len(marker) :]
            )
            version = info.get("version")
            found.append(
                _Package(
                    "npm",
                    str(name),
                    version if isinstance(version, str) else None,
                    path,
                    key == marker + str(name) and str(name) in direct,
                    bool(info.get("dev") or info.get("devOptional")),
                )
            )
        return found
    # lockfileVersion 1: a tree, walked without recursion because it's untrusted.
    stack = [(name, entry, True) for name, entry in _dict(data.get("dependencies")).items()]
    while stack:
        name, entry, top = stack.pop()
        info = _dict(entry)
        version = info.get("version")
        pinned = version if isinstance(version, str) and _npm_version(version) else None
        found.append(_Package("npm", name, pinned, path, top, bool(info.get("dev"))))
        stack.extend((n, e, False) for n, e in _dict(info.get("dependencies")).items())
    return found


def _yarn_names(header: str) -> set[str]:
    names = set()
    for spec in header.rstrip(":").split(","):
        spec = spec.strip().strip('"')
        at = spec.find("@", 1)
        if at > 0:
            names.add(spec[:at])
    return names


def _read_yarn_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    if "__metadata:" in text:
        limits.append(f"{path} is a Yarn Berry lockfile, and only Yarn v1 lockfiles are read")
        return []
    found = []
    current: set[str] = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            current = _yarn_names(line.strip())
        elif line.startswith("  version ") and current:
            version = line.split(None, 1)[1].strip().strip('"')
            found += [_Package("npm", n, version, path, False, False) for n in sorted(current)]
            current = set()
    if text.strip() and not found:
        limits.append(f"{path} couldn't be read: no package entries")
    return found


def _pnpm_key(key: str) -> tuple[str, str] | None:
    """``(name, version)`` from ``/a@1.0.0``, ``a@1.0.0(peer@2)``, ``@s/a@1.0.0`` or the older ``/a/1.0.0``."""
    key = key.lstrip("/").split("(", 1)[0]
    at = key.rfind("@")
    if at > 0:
        return key[:at], key[at + 1 :]
    if "/" in key:
        name, version = key.rsplit("/", 1)
        return name, version
    return None


def _read_pnpm_lock(text: str, path: str, limits: list[str]) -> list[_Package]:
    import yaml

    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        limits.append(f"{path} couldn't be read: {type(exc).__name__}")
        return []
    data = _dict(data)
    direct: set[str] = set()
    dev_direct: set[str] = set()
    importers = _dict(data.get("importers")) or {".": data}
    for importer in importers.values():
        info = _dict(importer)
        direct |= set(_dict(info.get("dependencies"))) | set(
            _dict(info.get("optionalDependencies"))
        )
        dev_direct |= set(_dict(info.get("devDependencies")))
    found = []
    for key, entry in _dict(data.get("packages")).items():
        parsed = _pnpm_key(str(key))
        if parsed is None:
            continue
        name, version = parsed
        dev = bool(_dict(entry).get("dev")) or (name in dev_direct and name not in direct)
        found.append(_Package("npm", name, version, path, name in direct | dev_direct, dev))
    return found


_DECLARED = {
    "pyproject.toml": _read_pyproject,
    "package.json": _read_package_json,
    "Pipfile": _read_pipfile,
}
_LOCKS = {
    # In order of preference when a folder has more than one.
    "PyPI": {
        "uv.lock": _read_uv_lock,
        "poetry.lock": _read_poetry_lock,
        "Pipfile.lock": _read_pipfile_lock,
    },
    "npm": {
        "package-lock.json": _read_package_lock,
        "pnpm-lock.yaml": _read_pnpm_lock,
        "yarn.lock": _read_yarn_lock,
    },
}


def _is_requirements(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if not name.endswith(".txt"):
        return False
    return name.startswith("requirements") or _folder(path).rsplit("/", 1)[-1] == "requirements"


def declared_in(ctx: RepoContext, folder: str) -> tuple[list[_Package], list[str]]:
    """What the manifests directly in ``folder`` declare, for telling a web service from a library."""
    limits: list[str] = []
    found: list[_Package] = []
    for path in ctx.files:
        if _folder(path) != folder:
            continue
        name = path.rsplit("/", 1)[-1]
        reader = _DECLARED.get(name) or (_read_requirements if _is_requirements(path) else None)
        if reader is None:
            continue
        text = read_text(ctx, path)
        if text is not None:
            found += reader(text, path, limits)
    return found, limits


def _load_locks(ctx: RepoContext, limits: list[str]) -> dict[tuple[str, str], list[_Package]]:
    """(ecosystem, folder) to what that folder's preferred lockfile says."""
    locks: dict[tuple[str, str], list[_Package]] = {}
    present = {p for p in ctx.files if not is_fixture(p)}
    folders = sorted({_folder(p) for p in present})
    for folder in folders:
        for ecosystem, readers in _LOCKS.items():
            for name, reader in readers.items():
                path = name if folder == "." else f"{folder}/{name}"
                if path not in present:
                    continue
                text = read_text(ctx, path, MAX_LOCK_BYTES)
                if text is None:
                    limits.append(f"{path} couldn't be read: too large, or outside the repository")
                    continue
                parsed = reader(text, path, limits)
                if parsed:
                    locks[(ecosystem, folder)] = parsed
                    break
    return locks


def _ancestors(folder: str) -> list[str]:
    if folder == ".":
        return ["."]
    parts = folder.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)] + ["."]


def inventory(ctx: RepoContext) -> tuple[list[_Package], list[str]]:
    """Every package the manifests name, with lock data taking the place of a range."""
    limits: list[str] = []
    locks = _load_locks(ctx, limits)
    declared: list[_Package] = []
    for path in ctx.files:
        if is_fixture(path):
            continue
        name = path.rsplit("/", 1)[-1]
        reader = _DECLARED.get(name) or (_read_requirements if _is_requirements(path) else None)
        if reader is None:
            continue
        text = read_text(ctx, path)
        if text is None:
            limits.append(f"{path} couldn't be read: too large, or outside the repository")
            continue
        declared += reader(text, path, limits)

    indexes: dict[tuple[str, str], dict[str, list[_Package]]] = {}
    for key, packages in locks.items():
        by_name = indexes.setdefault(key, {})
        for locked_package in packages:
            by_name.setdefault(locked_package.name, []).append(locked_package)
    kept: list[_Package] = []
    for package in declared:
        folder = _folder(package.manifest)
        # A requirements file belongs to its own folder. A workspace member's lock can sit further up.
        searched = [folder] if package.manifest.endswith(".txt") else _ancestors(folder)
        locked: list[_Package] = []
        for candidate in searched:
            locked = indexes.get((package.ecosystem, candidate), {}).get(package.name, [])
            if locked:
                break
        if not locked:
            kept.append(package)
            continue
        for entry in locked:
            entry.direct = True
            if not package.dev:
                entry.dev = False
        if package.version and all(entry.version != package.version for entry in locked):
            kept.append(package)  # pinned here, and locked differently there: both are in play
    for packages in locks.values():
        kept += packages

    unique: dict[tuple[str, str, str | None, str], _Package] = {}
    for package in kept:
        identity = (package.ecosystem, package.name, package.version, package.manifest)
        if identity in unique:
            unique[identity].direct = unique[identity].direct or package.direct
            unique[identity].dev = unique[identity].dev and package.dev
        else:
            unique[identity] = package
    ordered = sorted(
        unique.values(), key=lambda p: (p.ecosystem, p.name, p.version or "", p.manifest)
    )

    unpinned = sorted({p.name for p in ordered if p.version is None})
    if unpinned:
        limits.append(
            f"packages with no pinned or locked version can't be checked for vulnerabilities "
            f"({len(unpinned)}): {some(unpinned)}"
        )
    other = sorted(
        p for p in ctx.files if p.rsplit("/", 1)[-1] in _UNREAD_MANIFESTS and not is_fixture(p)
    )
    if other:
        limits.append(f"these manifests aren't read: {some(other)}")
    return ordered, limits


# OSV


def _request(url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One bounded HTTPS call. The only network use in repo intelligence."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - fixed https URL
    # always https://api.osv.dev; repository content never chooses the host
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=OSV_TIMEOUT) as response:  # noqa: S310 - fixed https URL
        raw = response.read(OSV_MAX_RESPONSE + 1)
    if len(raw) > OSV_MAX_RESPONSE:
        raise ValueError("response too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("response isn't an object")
    return payload


_NETWORK_ERRORS = (OSError, ValueError, HTTPException, RecursionError)


def _query_ids(queries: list[dict[str, Any]]) -> list[list[str]]:
    """The advisory ids for each query, following OSV's per-query pages so that nothing is cut short."""
    ids: list[list[str]] = [[] for _ in queries]
    for start in range(0, len(queries), OSV_BATCH_SIZE):
        pending = [
            (start + i, dict(q)) for i, q in enumerate(queries[start : start + OSV_BATCH_SIZE])
        ]
        for _ in range(OSV_MAX_PAGES):
            if not pending:
                break
            results = _request(OSV_BATCH_URL, {"queries": [q for _, q in pending]}).get("results")
            if not isinstance(results, list) or len(results) != len(pending):
                raise ValueError("response doesn't match the queries")
            following = []
            for (index, query), result in zip(pending, results, strict=True):
                info = _dict(result)
                vulns = info.get("vulns")
                for vuln in vulns if isinstance(vulns, list) else []:
                    vuln_id = _dict(vuln).get("id")
                    if isinstance(vuln_id, str):
                        ids[index].append(vuln_id)
                token = info.get("next_page_token")
                if isinstance(token, str) and token:
                    following.append((index, {**query, "page_token": token}))
            pending = following
        if pending:
            raise ValueError("too many pages of results")
    return ids


def _version_key(version: str) -> list[tuple[Any, ...]]:
    """Compares versions well enough to choose a fix, without a dependency. ``1.0a1 < 1.0 < 1.0.1``."""
    tokens = _VERSION_TOKEN.findall(version.lower()[:128])
    if tokens and not tokens[0].isdigit():
        tokens = tokens[1:]  # the v in v1.2
    size = next((i for i, token in enumerate(tokens) if not token.isdigit()), len(tokens))
    release = [int(token) for token in tokens[:size]]
    while release and release[-1] == 0:
        release.pop()
    key: list[tuple[Any, ...]] = [(1, number) for number in release]
    for token in tokens[size:]:
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((2, token) if token in {"post", "rev", "r"} else (0, token))
    key.append((0.5,))
    return key


def _fixed_in(detail: dict[str, Any], ecosystem: str, name: str, version: str) -> str | None:
    """The lowest fix that's newer than the installed version, or the lowest fix if none is."""
    fixes: list[str] = []
    affected = detail.get("affected")
    for entry in affected if isinstance(affected, list) else []:
        info = _dict(entry)
        package = _dict(info.get("package"))
        if package.get("ecosystem") != ecosystem:
            continue
        listed = package.get("name")
        same = _pypi_name(str(listed)) == name if ecosystem == "PyPI" else listed == name
        if not same:
            continue
        ranges = info.get("ranges")
        for span in ranges if isinstance(ranges, list) else []:
            if _dict(span).get("type") == "GIT":
                continue
            events = _dict(span).get("events")
            for event in events if isinstance(events, list) else []:
                fixed = _dict(event).get("fixed")
                if isinstance(fixed, str) and fixed:
                    fixes.append(fixed)
    if not fixes:
        return None
    installed = _version_key(version)
    newer = [f for f in fixes if _version_key(f) > installed]
    return min(newer or fixes, key=_version_key)


def _cvss3_score(vector: str) -> float | None:
    """The CVSS 3.x base score, from the specification's formula."""
    if not vector.startswith("CVSS:3"):
        return None
    metrics = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    try:
        changed = metrics["S"] == "C"
        access = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[metrics["AV"]]
        complexity = {"L": 0.77, "H": 0.44}[metrics["AC"]]
        privileges = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[
            metrics["PR"]
        ]
        interaction = {"N": 0.85, "R": 0.62}[metrics["UI"]]
        weight = {"H": 0.56, "L": 0.22, "N": 0.0}
        scope_impact = 1 - (1 - weight[metrics["C"]]) * (1 - weight[metrics["I"]]) * (
            1 - weight[metrics["A"]]
        )
    except KeyError:
        return None
    impact = (
        7.52 * (scope_impact - 0.029) - 3.25 * (scope_impact - 0.02) ** 15
        if changed
        else 6.42 * scope_impact
    )
    if impact <= 0:
        return 0.0
    exploitability = 8.22 * access * complexity * privileges * interaction
    raw = min((1.08 if changed else 1.0) * (impact + exploitability), 10.0)
    scaled = round(raw * 100000)
    return scaled / 100000 if scaled % 10000 == 0 else (scaled // 10000 + 1) / 10.0


def severity_of(detail: dict[str, Any]) -> str:
    """``critical``, ``high``, ``medium``, ``low`` or ``unknown``: the database's word first, then the CVSS 3 vector."""
    word = _dict(detail.get("database_specific")).get("severity")
    if isinstance(word, str) and word.lower() in _SEVERITY_WORDS:
        return _SEVERITY_WORDS[word.lower()]
    scores = detail.get("severity")
    for entry in scores if isinstance(scores, list) else []:
        vector = _dict(entry).get("score")
        score = _cvss3_score(vector) if isinstance(vector, str) else None
        if score is not None:
            return (
                "critical"
                if score >= 9
                else "high"
                if score >= 7
                else "medium"
                if score >= 4
                else "low"
            )
    return "unknown"


_SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _record(package: tuple[str, str, str], vuln_id: str, detail: dict[str, Any]) -> dict[str, Any]:
    ecosystem, name, version = package
    aliases = _strings(detail.get("aliases"))
    summary = detail.get("summary") or detail.get("details") or ""
    return {
        "ecosystem": ecosystem,
        "package": name,
        "version": version,
        "id": vuln_id,
        "aliases": sorted(set(aliases)),
        "severity": severity_of(detail),
        "fixed_in": _fixed_in(detail, ecosystem, name, version),
        "summary": str(summary).strip()[:500],
    }


def _merge_aliases(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One flaw often has several records (PYSEC, GHSA). Count it once, keeping what each knows."""
    merged: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda r: (not r["id"].startswith("GHSA"), r["id"])):
        names = {record["id"], *record["aliases"]}
        twin = next(
            (
                m
                for m in merged
                if (m["ecosystem"], m["package"], m["version"])
                == (record["ecosystem"], record["package"], record["version"])
                and names & {m["id"], *m["aliases"]}
            ),
            None,
        )
        if twin is None:
            merged.append(record)
            continue
        twin["aliases"] = sorted((names | set(twin["aliases"])) - {twin["id"]})
        if _SEVERITY_RANK[record["severity"]] > _SEVERITY_RANK[twin["severity"]]:
            twin["severity"] = record["severity"]
        twin["fixed_in"] = twin["fixed_in"] or record["fixed_in"]
        twin["summary"] = twin["summary"] or record["summary"]
    return sorted(merged, key=lambda r: (r["ecosystem"], r["package"], r["version"], r["id"]))


def _scan(packages: list[_Package], limits: list[str]) -> tuple[str, list[dict[str, Any]] | None]:
    wanted = sorted({(p.ecosystem, p.name, p.version) for p in packages if p.version}, key=str)
    checkable = [(e, n, v or "") for e, n, v in wanted]
    queries = [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v in checkable]
    try:
        ids = _query_ids(queries)
    except _NETWORK_ERRORS as exc:
        limits.append(f"the vulnerability scan failed, so nothing is known: {type(exc).__name__}")
        return "failed", None

    unique = sorted({i for found in ids for i in found})
    fetched = unique[:OSV_MAX_DETAILS]
    if len(unique) > len(fetched):
        limits.append(f"details were fetched for only {len(fetched)} of {len(unique)} advisories")

    def fetch(vuln_id: str) -> tuple[str, dict[str, Any] | None]:
        try:
            return vuln_id, _request(OSV_VULN_URL + urllib.parse.quote(vuln_id, safe=""))
        except _NETWORK_ERRORS:
            return vuln_id, None

    with ThreadPoolExecutor(max_workers=OSV_WORKERS) as pool:
        details = dict(pool.map(fetch, fetched))
    missing = sorted(i for i in unique if details.get(i) is None)
    if missing:
        limits.append(
            f"advisories whose details couldn't be fetched, so their severity is unknown "
            f"({len(missing)}): {some(missing)}"
        )

    records = []
    for package, found in zip(checkable, ids, strict=True):
        for vuln_id in sorted(set(found)):
            detail = details.get(vuln_id) or {}
            if not detail.get("withdrawn"):
                records.append(_record(package, vuln_id, detail))
    return "scanned", _merge_aliases(records)


def _scan_offline(
    packages: list[_Package], limits: list[str], db: Path
) -> tuple[str, list[dict[str, Any]] | None]:
    """The same answer from a local OSV copy (``ananke cve import``): nothing leaves the machine."""
    from ananke.plexus.intel.osv_db import query, status

    info = status(db)
    covered = set(str(info.get("ecosystems") or "").split(",")) - {""}
    wanted = sorted(
        {(p.ecosystem, p.name, p.version or "") for p in packages if p.version}, key=str
    )
    uncovered = sorted({e for e, _, _ in wanted if e not in covered})
    if uncovered:
        limits.append(
            f"{', '.join(uncovered)} packages weren't checked: the offline copy holds no export for them "
            "(import that ecosystem's all.zip too)"
        )
    wanted = [w for w in wanted if w[0] in covered]
    try:
        ids, details = query(db, wanted)
    except Exception as exc:
        limits.append(
            f"the offline vulnerability database couldn't be read, so nothing is known: {exc}"
        )
        return "failed", None
    limits.append(
        f"checked offline against an OSV copy imported {info.get('imported_at', 'at an unknown time')} "
        f"({info.get('ecosystems', '?')}); advisories published since then aren't known"
    )
    records = [
        _record(package, vid, details.get(vid) or {})
        for package, found in zip(wanted, ids, strict=True)
        for vid in found
    ]
    return "scanned", _merge_aliases(records)


def analyze(ctx: RepoContext) -> dict[str, Any]:
    packages, limits = inventory(ctx)
    if not packages:
        return {
            "status": "none",
            "limits": limits,
            "scanner": None,
            "packages": [],
            "vulnerabilities": None,
        }
    status, vulnerabilities = "not_scanned", None
    offline = os.environ.get("ANANKE_OSV_DB")
    scanner = None
    if ctx.check_vulnerabilities and offline and Path(offline).is_file():
        status, vulnerabilities = _scan_offline(packages, limits, Path(offline))
        scanner = "osv-offline"
    elif ctx.check_vulnerabilities:
        status, vulnerabilities = _scan(packages, limits)
        scanner = "osv"
    else:
        limits.append(
            "vulnerabilities weren't checked: this needs the network and has to be asked for"
        )
    return {
        "status": status,
        "limits": limits,
        "scanner": scanner,
        "packages": [p.as_dict() for p in packages],
        "vulnerabilities": vulnerabilities,
    }

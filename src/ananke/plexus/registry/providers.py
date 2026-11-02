"""Source-neutral capability provider port (three-repo plan: AN-SOURCE-PORT, AN-INTERNAL-SKILLS, AN-CAPABILITY-LOCKS).

Ananke accepts capability *sources*, not a mandatory catalog vendor. A provider normalises discovery and retrieval;
the harness owns resolution policy, activation and execution.

========== ===================================== =============================================
operation  result                                must not
========== ===================================== =============================================
discover   stable descriptors + continuation     execute instructions or tools
resolve    one immutable descriptor + digest     guess between namespace collisions
fetch      bytes + verified digest               activate anything by downloading it
refresh    a new index snapshot                  rewrite an existing source lock
publish    a release receipt (writable only)     bypass review, or write to a read-only source
========== ===================================== =============================================

Identity is ``source_id/namespace/name`` (plus a version or digest), never a display name. Providers:

* :class:`LocalDirectoryProvider`: an approved folder of skills (``SKILL.md``, ``AGENT.md`` or ``ananke.yaml``).
  Bytes are read, never executed. Paths can't escape the root, including through symlinks. Missing metadata is
  reported as ``unknown``, never invented.
* :class:`TroveProvider`: an installed trove such as Dex Trove, read as data (``catalog.json``, ``releases.json``)
  from a root folder. :func:`from_project` asks a trove's ``ananke.troves`` entry point for that root only when the
  trove is enabled, so a project without troves never imports one.

A :class:`ProviderSet` combines providers in priority order and refuses an ambiguous reference instead of
picking one. :class:`SourceLock` pins resolved references; a changed digest is rejected, not followed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ananke.plexus.registry.errors import RegistryError

SIDECAR_SCHEMA = "ananke/source-sidecar@1"
LOCK_SCHEMA = "ananke/source-lock@1"
UNKNOWN = "unknown"
_MARKERS = ("ananke.yaml", "SKILL.md", "AGENT.md")
_SKIP = {".git", "__pycache__", "node_modules", ".venv"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SourceError(RegistryError):
    """Every provider failure carries a stable code; callers branch on ``code``, never on text."""

    code = "SOURCE_ERROR"


def _err(code: str, message: str) -> SourceError:
    return SourceError(message, code=code)


@dataclass(frozen=True)
class CapabilityRef:
    name: str
    namespace: str | None = None
    source_id: str | None = None
    version: str | None = None  # an exact version, or ``sha256:<digest>``

    @classmethod
    def parse(cls, text: str) -> CapabilityRef:
        """``[source:]namespace/name[@version|@sha256:…]`` or a bare ``name``."""
        body, _, version = text.strip().partition("@")
        source, sep, rest = body.partition(":")
        if not sep:
            source, rest = "", body
        ns, slash, name = rest.rpartition("/")
        if not name or not _NAME.match(name) or (slash and not ns):
            raise _err(
                "INVALID_REF",
                f"invalid capability reference {text!r}: use [source:]namespace/name[@version]",
            )
        return cls(
            name=name, namespace=ns or None, source_id=source or None, version=version or None
        )

    def __str__(self) -> str:
        head = f"{self.source_id}:" if self.source_id else ""
        mid = f"{self.namespace}/" if self.namespace else ""
        return f"{head}{mid}{self.name}" + (f"@{self.version}" if self.version else "")


@dataclass(frozen=True)
class Descriptor:
    source_id: str
    namespace: str
    name: str
    kind: str
    version: str
    digest: str
    summary: str = ""
    path: str = ""
    licence: str = UNKNOWN
    requirements: tuple[str, ...] = ()
    facets: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    unknown: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        return f"{self.source_id}:{self.namespace}/{self.name}"

    @property
    def ref(self) -> str:
        return f"{self.identity}@{self.version}"

    def sidecar(self) -> dict[str, Any]:
        """Normalised metadata kept *beside* the source bytes (never written into the source)."""
        return {
            "schema": SIDECAR_SCHEMA,
            "source": self.source_id,
            "namespace": self.namespace,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "path": self.path,
            "digest": self.digest,
            "licence": self.licence,
            "requirements": list(self.requirements),
            "unknown": list(self.unknown),
        }


@dataclass(frozen=True)
class Fetched:
    descriptor: Descriptor
    files: dict[str, bytes]


@dataclass(frozen=True)
class Page:
    items: list[Descriptor]
    cursor: str | None


@runtime_checkable
class CapabilityProvider(Protocol):
    source_id: str
    writable: bool

    def discover(self, query: str = "", cursor: str | None = None, limit: int = 50) -> Page: ...

    def resolve(self, ref: CapabilityRef) -> Descriptor: ...

    def fetch(self, descriptor: Descriptor) -> Fetched: ...

    def refresh(self) -> str: ...

    def publish(self, candidate: Path, *, approved_by: str) -> dict[str, Any]: ...


def digest_files(files: dict[str, bytes]) -> str:
    """SHA-256 over the sorted map of per-file SHA-256s (the scheme Dex Trove's ``content_hash`` uses)."""
    hashes = {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(files.items())}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    out: dict[str, str] = {}
    for line in text[3 : end if end > 0 else 0].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            out[key.strip()] = value.strip().strip("'\"")
    return out


def _page(items: list[Descriptor], query: str, cursor: str | None, limit: int) -> Page:
    words = [w for w in query.lower().split() if w]
    hits = [
        d for d in items if all(w in f"{d.namespace} {d.name} {d.summary}".lower() for w in words)
    ]
    hits.sort(key=lambda d: d.identity)
    start = int(cursor or 0)
    chunk = hits[start : start + limit]
    return Page(chunk, str(start + limit) if start + limit < len(hits) else None)


def _match(items: list[Descriptor], ref: CapabilityRef, source_id: str) -> Descriptor:
    if ref.source_id and ref.source_id != source_id:
        raise _err("NOT_FOUND", f"{ref} names source {ref.source_id!r}, not {source_id!r}")
    hits = [
        d
        for d in items
        if d.name == ref.name and (ref.namespace is None or d.namespace == ref.namespace)
    ]
    if not hits:
        raise _err("NOT_FOUND", f"{source_id} has no capability {ref}")
    if len({d.namespace for d in hits}) > 1:
        raise _err(
            "AMBIGUOUS",
            f"{ref.name!r} exists in namespaces {sorted({d.namespace for d in hits})} of {source_id}; name one",
        )
    if ref.version:
        want = ref.version.removeprefix("sha256:")
        exact = [d for d in hits if d.version == ref.version or d.digest == want]
        if not exact:
            have = ", ".join(sorted({d.version for d in hits}))
            raise _err(
                "VERSION_NOT_FOUND",
                f"{source_id} has {ref.namespace or hits[0].namespace}/{ref.name} at {have}, not {ref.version}",
            )
        return exact[0]
    return hits[0]


class LocalDirectoryProvider:
    """An approved folder of capabilities. Read-only unless ``writable=True`` (then publish needs an approver)."""

    def __init__(
        self,
        source_id: str,
        root: Path,
        *,
        namespace: str,
        writable: bool = False,
        visibility: str = "internal",
    ) -> None:
        if not _NAME.match(source_id) or not _NAME.match(namespace):
            raise _err(
                "INVALID_SOURCE",
                f"source id and namespace must be lowercase names: {source_id!r}, {namespace!r}",
            )
        self.source_id, self.namespace, self.writable = source_id, namespace, writable
        self.visibility = visibility  # "public" sources never receive internal-only lessons
        self.root = root.expanduser().resolve()
        self._index: list[Descriptor] | None = None

    def _inside(self, path: Path) -> Path:
        real = path.resolve()
        if real != self.root and self.root not in real.parents:
            raise _err("PATH_ESCAPE", f"{path} resolves outside the approved root {self.root}")
        return real

    def _read(self, folder: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for p in sorted(folder.rglob("*")):
            if any(part in _SKIP for part in p.relative_to(folder).parts) or not (
                p.is_file() or p.is_symlink()
            ):
                continue
            self._inside(p)  # a symlink pointing outside the root is refused, not followed
            if p.is_file():
                files[p.relative_to(folder).as_posix()] = p.read_bytes()
        return files

    def _describe(self, folder: Path) -> Descriptor:
        files = self._read(folder)
        marker = next(m for m in _MARKERS if m in files)
        meta: dict[str, str] = {}
        if marker == "ananke.yaml":
            for line in files[marker].decode("utf-8", "replace").splitlines():
                key, sep, value = line.partition(":")
                if sep and not line.startswith((" ", "\t")):
                    meta[key.strip()] = value.strip().strip("'\"")
        else:
            meta = _frontmatter(files[marker].decode("utf-8", "replace"))
        unknown = [
            k
            for k in ("version", "license", "description")
            if not meta.get(k) and not meta.get("summary" if k == "description" else k)
        ]
        name = folder.name.lower()
        if not _NAME.match(name):
            raise _err("INVALID_NAME", f"{folder.name!r} isn't a valid capability name")
        kind = meta.get("kind") or ("agent" if marker == "AGENT.md" else "skill")
        return Descriptor(
            source_id=self.source_id,
            namespace=self.namespace,
            name=name,
            kind=kind,
            version=meta.get("version") or UNKNOWN,
            digest=digest_files(files),
            summary=(meta.get("description") or meta.get("summary") or "")[:400],
            path=folder.relative_to(self.root).as_posix(),
            licence=meta.get("license") or UNKNOWN,
            requirements=tuple(
                t.strip() for t in meta.get("allowed-tools", "").split(",") if t.strip()
            ),
            unknown=tuple(unknown),
        )

    def _scan(self) -> list[Descriptor]:
        if not self.root.is_dir():
            raise _err(
                "SOURCE_UNAVAILABLE", f"source {self.source_id}: {self.root} is not a folder"
            )
        out: dict[str, Descriptor] = {}
        for marker_file in sorted(p for m in _MARKERS for p in self.root.rglob(m)):
            if any(part in _SKIP for part in marker_file.relative_to(self.root).parts):
                continue
            folder = self._inside(marker_file).parent
            d = self._describe(folder)
            if d.name in out and out[d.name].path != d.path:
                raise _err(
                    "NAMESPACE_COLLISION",
                    f"{self.source_id}: two capabilities are named {d.name!r} ({out[d.name].path}, {d.path})",
                )
            out[d.name] = d
        return list(out.values())

    def _items(self) -> list[Descriptor]:
        if self._index is None:
            self._index = self._scan()
        return self._index

    def discover(self, query: str = "", cursor: str | None = None, limit: int = 50) -> Page:
        return _page(self._items(), query, cursor, limit)

    def resolve(self, ref: CapabilityRef) -> Descriptor:
        return _match(self._items(), ref, self.source_id)

    def fetch(self, descriptor: Descriptor) -> Fetched:
        files = self._read(self._inside(self.root / descriptor.path))
        if digest_files(files) != descriptor.digest:
            raise _err(
                "DIGEST_MISMATCH",
                f"{descriptor.ref} changed on disk since it was resolved; resolve again or keep the lock",
            )
        return Fetched(descriptor, files)

    def refresh(self) -> str:
        self._index = self._scan()
        return hashlib.sha256(
            json.dumps([d.sidecar() for d in self._index], sort_keys=True).encode()
        ).hexdigest()

    def publish(self, candidate: Path, *, approved_by: str) -> dict[str, Any]:
        if not self.writable:
            raise _err("READ_ONLY", f"source {self.source_id} is read-only")
        if not approved_by.strip():
            raise _err("NOT_APPROVED", "publishing needs a named approver")
        src = candidate.resolve()
        if not any((src / m).is_file() for m in _MARKERS):
            raise _err("INVALID_CANDIDATE", f"{candidate} has no SKILL.md, AGENT.md or ananke.yaml")
        dest = self._inside(self.root / src.name)
        if dest.exists():
            raise _err(
                "VERSION_CONTENT_CONFLICT",
                f"{dest.name} already exists; publish a new name or version through review",
            )
        files = {
            rel: data
            for rel, data in (
                (p.relative_to(src).as_posix(), p.read_bytes())
                for p in sorted(src.rglob("*"))
                if p.is_file()
            )
        }
        for rel, data in files.items():
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        self._index = None
        return {
            "source": self.source_id,
            "name": dest.name,
            "digest": digest_files(files),
            "approved_by": approved_by,
        }


class GitProvider(LocalDirectoryProvider):
    """A private Git repository pinned to a commit, cached for offline use.

    * The ref must be a full 40-character commit: a branch or tag could move under a lock.
    * Credentials are named, never stored: ``credential_env`` is the name of an environment variable holding a
      token, read at fetch time and passed to Git through the environment only. It never appears in a sidecar,
      lock or log.
    * Git hooks, LFS smudge filters and submodules never run during a fetch.
    * After the first fetch the commit is served from ``cache_dir`` with no network. If neither the cache nor the
      remote is available, the error is ``SOURCE_UNAVAILABLE``; there is no fallback to another source.
    """

    def __init__(
        self,
        source_id: str,
        url: str,
        commit: str,
        *,
        namespace: str,
        cache_dir: Path,
        subdir: str = "",
        credential_env: str = "",
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise _err(
                "UNPINNED_SOURCE", f"source {source_id}: pin a full commit sha, not {commit!r}"
            )
        if re.search(r"://[^/@]*:[^/@]*@", url):
            raise _err(
                "CREDENTIAL_IN_URL",
                f"source {source_id}: put the token in an environment variable named by credential_env, not in the URL",
            )
        self.url, self.commit, self.credential_env = url, commit, credential_env
        self.checkout = cache_dir.expanduser().resolve() / source_id / commit
        super().__init__(
            source_id, self.checkout / subdir if subdir else self.checkout, namespace=namespace
        )

    def _git_env(self) -> dict[str, str]:
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            {"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_CONFIG_NOSYSTEM": "1"}
        )
        if self.credential_env:
            token = os.environ.get(self.credential_env)
            if not token:
                raise _err(
                    "CREDENTIAL_MISSING",
                    f"source {self.source_id}: environment variable {self.credential_env} is not set",
                )
            # an http header from the environment: never on the command line, in the URL or on disk
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
                }
            )
        return env

    def materialize(self) -> Path:
        import shutil
        import subprocess

        if (self.checkout / ".ananke-fetched").is_file():
            return self.checkout  # offline: the pinned commit is already here
        tmp = self.checkout.with_name(self.commit + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        safe = [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "submodule.recurse=false",
            "-c",
            "protocol.file.allow=user",
        ]
        try:
            for args in (
                ["init", "-q"],
                ["remote", "add", "origin", self.url],
                ["fetch", "-q", "--depth", "1", "origin", self.commit],
                ["checkout", "-q", "FETCH_HEAD"],
            ):
                subprocess.run(  # noqa: S603 - fixed git arguments; the URL and commit come from project config
                    ["git", *safe, *args],  # noqa: S607 - git from PATH, like every other git call in Ananke
                    cwd=tmp,
                    env=self._git_env(),
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise _err(
                "SOURCE_UNAVAILABLE",
                f"source {self.source_id}: cannot fetch {self.commit[:12]} and no cached copy exists",
            ) from exc
        shutil.rmtree(tmp / ".git", ignore_errors=True)
        (tmp / ".ananke-fetched").write_text(self.commit + "\n")
        shutil.rmtree(self.checkout, ignore_errors=True)
        tmp.rename(self.checkout)
        return self.checkout

    def _scan(self) -> list[Descriptor]:
        self.materialize()
        return super()._scan()


class TroveProvider:
    """An installed trove (Dex Trove's layout), read as data. Read-only: troves publish through their own review."""

    writable = False

    def __init__(self, source_id: str, root: Path) -> None:
        self.source_id = source_id
        self.root = root.resolve()
        self._items_cache: list[Descriptor] | None = None
        self._ledger: dict[str, Any] = {}
        self._files: dict[str, list[str]] = {}  # the upstream files each item's digest covers

    def _load(self) -> list[Descriptor]:
        cat = self.root / "catalog.json"
        if not cat.is_file():
            raise _err(
                "SOURCE_UNAVAILABLE", f"trove {self.source_id}: no catalog.json at {self.root}"
            )
        items = json.loads(cat.read_text(encoding="utf-8")).get("items", [])
        led = self.root / "releases.json"
        self._ledger = (
            json.loads(led.read_text(encoding="utf-8")).get("items", {}) if led.is_file() else {}
        )
        facets_path = self.root / "facets.json"
        facets = (
            json.loads(facets_path.read_text(encoding="utf-8")).get("items", {})
            if facets_path.is_file()
            else {}
        )
        out = []
        for it in items:
            key = f"{it['kind']}:{it['id']}"
            self._files[it["dir"]] = list(it.get("files", []))
            out.append(
                Descriptor(
                    source_id=self.source_id,
                    namespace=it["namespace"],
                    name=it["name"],
                    kind=it["kind"],
                    version=it.get("version") or UNKNOWN,
                    digest=it.get("content_hash", ""),
                    summary=(it.get("description") or "")[:400],
                    path=it["dir"],
                    licence=(it.get("license") or {}).get("spdx") or UNKNOWN,
                    facets=facets.get(key, {}).get("facets", {}),
                    unknown=() if it.get("version") else ("version",),
                )
            )
        return out

    def _items(self) -> list[Descriptor]:
        if self._items_cache is None:
            self._items_cache = self._load()
        return self._items_cache

    def discover(self, query: str = "", cursor: str | None = None, limit: int = 50) -> Page:
        return _page(self._items(), query, cursor, limit)

    def resolve(self, ref: CapabilityRef) -> Descriptor:
        try:
            return _match(self._items(), ref, self.source_id)
        except SourceError as exc:
            if exc.code != "VERSION_NOT_FOUND":
                raise
            current = _match(
                self._items(), CapabilityRef(ref.name, ref.namespace, ref.source_id), self.source_id
            )
            key = f"{current.kind}:{current.namespace}/{current.name}"
            want = (ref.version or "").removeprefix("sha256:")
            older = [
                r
                for r in self._ledger.get(key, {}).get("releases", [])
                if want in (r["version"], r["content_hash"])
            ]
            if not older:
                raise
            r = older[-1]
            return Descriptor(
                self.source_id,
                current.namespace,
                current.name,
                current.kind,
                r["version"],
                r["content_hash"],
                current.summary,
                current.path,
                current.licence,
                facets=current.facets,
            )

    def fetch(self, descriptor: Descriptor) -> Fetched:
        folder = (self.root / descriptor.path).resolve()
        if self.root not in folder.parents:
            raise _err("PATH_ESCAPE", f"{descriptor.path} resolves outside the trove")
        self._items()
        listed = self._files.get(descriptor.path) or [
            p.relative_to(
                folder
            ).as_posix()  # a trove without file lists: everything but its own sidecars
            for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name not in ("dex.json", "ananke.yaml")
        ]
        files = {rel: (folder / rel).read_bytes() for rel in listed if (folder / rel).is_file()}
        if digest_files(files) != descriptor.digest:
            raise _err(
                "NOT_CACHED"
                if any(
                    d.digest != descriptor.digest
                    for d in self._items()
                    if d.name == descriptor.name
                )
                else "DIGEST_MISMATCH",
                f"{descriptor.ref}: the installed trove holds other bytes; install the trove release that has this version, "
                "or fetch it from its recorded upstream commit (no fallback to another version)",
            )
        return Fetched(descriptor, files)

    def refresh(self) -> str:
        self._items_cache = None
        items = self._items()
        return hashlib.sha256(
            json.dumps(sorted(d.ref + d.digest for d in items)).encode()
        ).hexdigest()

    def publish(self, candidate: Path, *, approved_by: str) -> dict[str, Any]:
        raise _err(
            "READ_ONLY",
            f"trove {self.source_id} is read-only; publish through the trove's own reviewed process",
        )


class ProviderSet:
    """Providers in priority order. Exact identity always wins; an ambiguous bare name is refused."""

    def __init__(self, providers: list[CapabilityProvider]) -> None:
        ids = [p.source_id for p in providers]
        if len(set(ids)) != len(ids):
            raise _err("DUPLICATE_SOURCE", f"source ids must be unique: {ids}")
        self.providers = {p.source_id: p for p in providers}

    def discover(self, query: str = "", limit: int = 50) -> list[Descriptor]:
        out: list[Descriptor] = []
        for p in self.providers.values():
            out += p.discover(query, None, limit).items
        return out

    def resolve(self, ref: CapabilityRef | str) -> Descriptor:
        ref = CapabilityRef.parse(ref) if isinstance(ref, str) else ref
        if ref.source_id:
            if ref.source_id not in self.providers:
                raise _err(
                    "SOURCE_UNAVAILABLE",
                    f"no source {ref.source_id!r}; configured: {sorted(self.providers)}",
                )
            return self.providers[ref.source_id].resolve(ref)
        found: list[Descriptor] = []
        for p in self.providers.values():
            try:
                found.append(p.resolve(ref))
            except SourceError as exc:
                if exc.code not in ("NOT_FOUND",):
                    raise
        if not found:
            raise _err("NOT_FOUND", f"no source has {ref}")
        if len(found) > 1:
            options = ", ".join(d.identity for d in found)
            raise _err(
                "AMBIGUOUS",
                f"{ref} exists in several sources ({options}); name the source, e.g. {found[0].identity}",
            )
        return found[0]

    def fetch(self, d: Descriptor) -> Fetched:
        return self.providers[d.source_id].fetch(d)


@dataclass
class SourceLock:
    """Pins resolved capabilities by exact identity, version and digest. Refreshing a source never changes it."""

    entries: dict[str, Descriptor] = field(default_factory=dict)

    def add(self, d: Descriptor) -> None:
        held = self.entries.get(d.identity)
        if held and (held.version, held.digest) != (d.version, d.digest):
            raise _err(
                "VERSION_CONFLICT",
                f"{d.identity} is already locked at {held.version} ({held.digest[:12]}); requested {d.version} "
                f"({d.digest[:12]}). Pick one: update the lock explicitly, or change the requirement.",
            )
        self.entries[d.identity] = d

    def check(self, providers: ProviderSet) -> list[str]:
        """Problems that would stop a locked run. A digest that moved is a problem, never an update."""
        problems = []
        for ident, d in sorted(self.entries.items()):
            try:
                providers.fetch(d)
            except SourceError as exc:
                problems.append(f"{ident}@{d.version}: {exc}")
        return problems

    def dump(self) -> str:
        body = {
            "schema": LOCK_SCHEMA,
            "entries": [d.sidecar() for _, d in sorted(self.entries.items())],
        }
        return json.dumps(body, indent=2, sort_keys=True) + "\n"

    @classmethod
    def load(cls, text: str) -> SourceLock:
        data = json.loads(text)
        if data.get("schema") != LOCK_SCHEMA:
            raise _err("INVALID_LOCK", f"not a source lock (schema {data.get('schema')!r})")
        lock = cls()
        for e in data["entries"]:
            d = Descriptor(
                e["source"],
                e["namespace"],
                e["name"],
                e["kind"],
                e["version"],
                e["digest"],
                path=e["path"],
                licence=e["licence"],
                requirements=tuple(e["requirements"]),
                unknown=tuple(e["unknown"]),
            )
            lock.entries[d.identity] = d
        return lock


def from_project(project: Path) -> ProviderSet:
    """Providers configured for a project: ``[capability_sources.*]`` folders first, then enabled troves.

    Order is priority. Troves are read as data from the root their entry point reports; nothing is imported from
    the trove's package beyond that entry point.
    """
    from ananke.plexus.config.loader import load_config
    from ananke.plexus.registry import troves

    cfg = load_config(project)
    providers: list[CapabilityProvider] = []
    for sid, src in sorted(cfg.capability_sources.items()):
        if not src.enabled:
            continue
        if src.type != "local":
            raise _err(
                "UNSUPPORTED_SOURCE",
                f"source {sid}: type {src.type!r} isn't supported yet (use local)",
            )
        root = Path(src.root)
        providers.append(
            LocalDirectoryProvider(
                sid,
                root if root.is_absolute() else project / root,
                namespace=src.namespace,
                writable=src.writable,
                visibility=src.visibility,
            )
        )
    enabled = {name for name, t in cfg.troves.items() if t.enabled}
    # loading an entry point imports its package: only for troves the project switched on
    for info in troves.discover() if enabled else []:
        if info.name in enabled and info.root is not None:
            providers.append(TroveProvider(info.name, info.root))
    return ProviderSet(providers)


__all__ = [
    "CapabilityProvider",
    "CapabilityRef",
    "Descriptor",
    "Fetched",
    "GitProvider",
    "LocalDirectoryProvider",
    "Page",
    "ProviderSet",
    "SourceError",
    "SourceLock",
    "TroveProvider",
    "digest_files",
    "from_project",
]

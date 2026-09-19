"""Git and archive importers (spec §34, §130).

Both fetch content into a throw-away directory and delegate to the path importers, then
stamp the right provenance. Git remotes are network egress and are policy-gated; archives
are extracted with the same traversal/symlink/size checks as registry payloads.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

from ananke.plexus.registry.errors import ImporterError, PayloadError, PolicyViolationError
from ananke.plexus.registry.importers.base import (
    Candidate,
    ImporterPermissions,
    InspectionContext,
    ProbeResult,
    Source,
)
from ananke.plexus.registry.models import GitInfo, ImporterInfo
from ananke.plexus.registry.payload import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, safe_relpath

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,100}$")
_URL_RE = re.compile(r"^(https?|ssh|git)://[^\s]+$|^git@[\w.\-]+:[^\s]+$")


def _strip_credentials(url: str) -> str:
    if "://" not in url:
        return url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _delegate(path: Path, ctx: InspectionContext) -> list[Candidate]:
    from ananke.plexus.registry.importers.registry import plan_path

    out: list[Candidate] = []
    for importer, src in plan_path(path, ctx):
        out.extend(importer.inspect(src, ctx))
    if not out:
        raise ImporterError(f"nothing importable found in {path}")
    return out


class GitImporter:
    id: ClassVar[str] = "git"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions(network=True)

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme == "git":
            return ProbeResult(ok=True, confidence=1.0, reason="git: source")
        return ProbeResult(ok=False, reason="not a git source")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        target, _, ref = source.target.partition("#")
        local = Path(target).expanduser()
        is_local = local.exists()
        if not is_local:
            if not _URL_RE.match(target) or target.startswith("-"):
                raise ImporterError(f"unsupported or unsafe git URL: {target!r}")
            if not ctx.policy.network_allowed("git", ctx.allow_network):
                raise PolicyViolationError(
                    "cloning a remote repository needs explicit network approval "
                    "(remote_sources.allow_git_remote or --allow-network)",
                    code="NETWORK_NOT_APPROVED",
                )
        if ref and not _REF_RE.match(ref):
            raise ImporterError(f"invalid git ref: {ref!r}")
        git = shutil.which("git")
        if git is None:
            raise ImporterError("git executable not found on PATH")
        with tempfile.TemporaryDirectory(prefix="ananke-git-") as tmp:
            dest = Path(tmp) / "repo"
            cmd = [git, "-c", "protocol.ext.allow=never", "clone", "--depth", "1", "--no-tags"]
            if ref:
                cmd += ["--branch", ref]
            cmd += ["--", str(local) if is_local else target, str(dest)]
            proc = subprocess.run(  # noqa: S603 - argv list, shell=False, validated inputs
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                env={
                    "PATH": _path_env(),
                    "HOME": tmp,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ALLOW_PROTOCOL": "https:ssh:git:file",
                },
            )
            if proc.returncode != 0:
                raise ImporterError(
                    f"git clone failed: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else proc.returncode}"
                )
            head = subprocess.run(  # noqa: S603
                [git, "-C", str(dest), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                env={"PATH": _path_env(), "HOME": tmp},
            )
            commit = head.stdout.strip() or None
            shutil.rmtree(dest / ".git", ignore_errors=True)
            candidates = _delegate(dest, ctx)
        for cand in candidates:
            cand.provenance = cand.provenance.model_copy(
                update={
                    "source_type": "git",
                    "source_name": _strip_credentials(target) if not is_local else local.name,
                    "source_url": None if is_local else _strip_credentials(target),
                    "git": GitInfo(
                        repository=None if is_local else _strip_credentials(target), commit=commit
                    ),
                    "importer": ImporterInfo(id=self.id, version=self.version),
                    "fingerprint": f"git:{commit}" if commit else cand.provenance.fingerprint,
                }
            )
        return candidates


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


class ArchiveImporter:
    id: ClassVar[str] = "archive"
    version: ClassVar[str] = "1"
    static: ClassVar[bool] = True
    permissions: ClassVar[ImporterPermissions] = ImporterPermissions()

    def probe(self, source: Source, ctx: InspectionContext) -> ProbeResult:
        if source.scheme == "archive" or (
            source.scheme == "path"
            and source.path.is_file()
            and source.path.name.lower().endswith((".tar.gz", ".tgz", ".tar", ".zip"))
        ):
            return ProbeResult(ok=True, confidence=0.9, reason="archive file")
        return ProbeResult(ok=False, reason="not an archive")

    def inspect(self, source: Source, ctx: InspectionContext) -> list[Candidate]:
        path = source.path
        if not path.is_file():
            raise ImporterError(f"archive not found: {path}")
        data = path.read_bytes()
        if len(data) > MAX_TOTAL_BYTES:
            raise ImporterError("archive exceeds size limits")
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory(prefix="ananke-arch-") as tmp:
            root = Path(tmp) / "src"
            try:
                _extract_safely(data, path.name.lower(), root)
            except PayloadError as exc:
                raise ImporterError(str(exc)) from exc
            children = [p for p in root.iterdir()]
            base = children[0] if len(children) == 1 and children[0].is_dir() else root
            candidates = _delegate(base, ctx)
        for cand in candidates:
            cand.provenance = cand.provenance.model_copy(
                update={
                    "source_type": "archive",
                    "source_name": path.name,
                    "importer": ImporterInfo(id=self.id, version=self.version),
                    "fingerprint": f"sha256:{digest}",
                }
            )
        return candidates


def _extract_safely(data: bytes, name: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    total = 0
    count = 0

    def write(rel: str, content: bytes) -> None:
        nonlocal total, count
        clean = safe_relpath(rel)
        total += len(content)
        count += 1
        if count > MAX_FILES or total > MAX_TOTAL_BYTES or len(content) > MAX_FILE_BYTES:
            raise PayloadError("archive exceeds size limits")
        target = (root / clean).resolve()
        if root != target and root not in target.parents:
            raise PayloadError(f"archive path escapes destination: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise PayloadError(f"symlink in archive rejected: {info.filename!r}")
                    write(info.filename, zf.read(info))
        except zipfile.BadZipFile as exc:
            raise PayloadError(f"corrupt zip archive: {exc}") from exc
        return
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                if not member.isreg():
                    raise PayloadError(f"non-regular archive member rejected: {member.name!r}")
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover
                    raise PayloadError(f"unreadable member: {member.name!r}")
                write(member.name, handle.read())
    except tarfile.TarError as exc:
        raise PayloadError(f"corrupt tar archive: {exc}") from exc

"""Advanced, evidence-first views built on the repository analysis (three-repo plan: ADV01..ADV17).

Each module is deterministic: the same inputs give the same result and digest, nothing is run unless a command says
so, and every claim names the evidence it comes from and whether it was observed or is a hypothesis.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class InsightError(ValueError):
    """Input that can't be used; the message says what to do instead."""


def digest(obj: Any) -> str:
    """sha256 of canonical JSON, leaving out ``generated_at`` and ``digest`` wherever they appear."""

    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in {"generated_at", "digest"}}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    body = json.dumps(
        strip(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def git(root: Path, *args: str, timeout: int = 60) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), *args],  # noqa: S607 - git from PATH, like the rest of the harness
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if done.returncode != 0:
        msg = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise InsightError(f"git {' '.join(args[:2])} failed: {msg[0] if msg else 'no message'}")
    return done.stdout.decode("utf-8", "replace")


def resolve_ref(root: Path, ref: str) -> str:
    if ref.startswith("-"):
        raise InsightError(f"{ref!r} isn't a revision")
    try:
        return git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except InsightError as exc:
        raise InsightError(
            f"{ref!r} isn't a commit in {root}; check the name with `git log --oneline`"
        ) from exc


@contextmanager
def snapshot(root: Path, ref: str) -> Iterator[Path]:
    """The tree at ``ref`` in a throwaway folder: read with ``git archive``, so the working tree is never touched."""
    commit = resolve_ref(root, ref)
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(root), "archive", "--format=tar", commit],  # noqa: S607
        capture_output=True,
        timeout=300,
        check=False,
    )
    if done.returncode != 0:
        raise InsightError(
            f"git archive {ref} failed: {done.stderr.decode('utf-8', 'replace')[:200]}"
        )
    with tempfile.TemporaryDirectory(prefix="ananke-snapshot-") as tmp:
        _extract(done.stdout, Path(tmp))
        yield Path(tmp)


def _extract(archive: bytes, dest: Path) -> None:
    """Unpack a ``git archive`` tar member by member: only regular files and folders, each path checked to stay
    inside ``dest``, then extracted with the ``data`` filter as well (no links, devices or absolute paths)."""
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                continue  # symlinks and the like aren't source; a code graph doesn't need them
            target = (root / member.name).resolve()
            if target != root and root not in target.parents:
                raise InsightError(
                    f"the archive names {member.name!r}, outside the snapshot folder"
                )
            tar.extract(member, root, filter="data")


def write_or_print(text: str, output: Path | None) -> str | None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        return f"wrote {output}"
    return None

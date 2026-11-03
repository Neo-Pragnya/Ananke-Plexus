"""The narrow, versioned app facade for products built on Ananke (three-repo plan: AN-APP-FACADE, AN-RUN-EVENTS,
AN-EXTERNAL-RENDERER).

A product such as Dev Guru talks to Ananke through these functions only:

``negotiate`` · ``capabilities`` · ``resolve_capabilities`` · ``prepare_run`` · ``submit_run`` · ``get_run`` ·
``cancel_run`` · ``stream_events`` · ``list_artifacts`` · ``read_artifact`` · ``register_artifact`` ·
``register_failure``

Each maps onto the real harness. Run kinds call the existing :class:`ananke.plexus.api.Ananke` operations, and
capability references go through the provider port. Nothing here re-implements analysis or execution.

Contract (``ananke-app/1``, matching the three-repo ``run-request`` and ``run-result`` schemas):

* States: queued → running → succeeded | failed | cancelled. ``complete`` says whether every artifact was produced.
* Idempotency: the same key and the same canonical request return the same run; the same key with a different
  request is rejected (``IDEMPOTENCY_CONFLICT``).
* A prepared plan is bound to the source snapshot. If the workspace changed before submission, it must be prepared
  again (``STALE_PLAN``).
* Events have a per-run, gap-free, increasing ``seq``. Reading after a sequence that retention dropped returns the
  missed interval and the authoritative run state, never a silent skip.
* Cancellation is cooperative. A finished run stays finished, and completed side effects aren't rolled back.
* External producers (renderers) register bytes, a digest and the inputs they used. A failed producer is visible in
  the run's warnings and events.

State lives under ``.ananke/app/`` in the workspace. It is plain JSON, so a run outlives the process and a
disconnected client.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROTOCOL = "ananke-app/1"
SUPPORTED = (PROTOCOL,)
EVENT_SCHEMA = "ananke-app/event@1"
RESULT_SCHEMA = "1.0.0"  # run-result.schema.json schema_version
TERMINAL = {"succeeded", "failed", "cancelled"}
EVENT_RETENTION = 1000
MAX_SOURCE_LINES = 400


class AppError(Exception):
    """A structured failure: stable ``code``, a message for people, and whether retrying could help."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"[{code}] {message}")
        self.code, self.message, self.retryable = code, message, retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


@dataclass(frozen=True)
class PreparedRun:
    kind: str
    parameters: dict[str, Any]
    capability_refs: tuple[dict[str, str], ...]
    source_snapshot: str
    locked_plan_digest: str

    def request(self, workspace_id: str, idempotency_key: str) -> dict[str, Any]:
        """The canonical ``run-request`` body."""
        return {
            "schema_version": RESULT_SCHEMA,
            "workspace_id": workspace_id,
            "source_snapshot": self.source_snapshot,
            "locked_plan_digest": self.locked_plan_digest,
            "idempotency_key": idempotency_key,
            "capability_refs": list(self.capability_refs),
            "parameters": {"kind": self.kind, **self.parameters},
        }


RunKind = Callable[["AppFacade", str, dict[str, Any]], None]


class AppFacade:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.state = self.workspace / ".ananke" / "app"
        self.kinds: dict[str, RunKind] = {
            "graph.build": _graph_build,
            "graph.impact": _graph_impact,
        }

    # ------------------------------------------------------------------ negotiation and discovery
    @staticmethod
    def negotiate(client_protocols: list[str]) -> str:
        for p in client_protocols:
            if p in SUPPORTED:
                return p
        raise AppError(
            "UNSUPPORTED_VERSION",
            f"this Ananke speaks {', '.join(SUPPORTED)}; the client offered {', '.join(client_protocols) or 'nothing'}. "
            "Upgrade the older side; nothing is installed automatically.",
        )

    def capabilities(self) -> dict[str, Any]:
        from ananke.plexus.version import __version__

        return {
            "protocol": PROTOCOL,
            "ananke": __version__,
            "operations": [
                "resolve_capabilities",
                "prepare_run",
                "submit_run",
                "get_run",
                "cancel_run",
                "stream_events",
                "list_artifacts",
                "read_artifact",
                "register_artifact",
                "register_failure",
                "validate_workflow",
                "preview_workflow",
                "compile_workflow",
                "suggest_capabilities",
                "code_view",
                "read_source",
                "architecture_view",
                "evidence_view",
            ],
            "run_kinds": sorted(self.kinds),
        }

    def workspace_id(self) -> str:
        return "ws-" + _sha(str(self.workspace))[:16]

    def _git(self, *args: str) -> str:
        cmd = ["git", *args]  # fixed read-only git queries
        res = subprocess.run(cmd, cwd=self.workspace, capture_output=True, text=True, check=True)  # noqa: S603
        return res.stdout

    def source_snapshot(self) -> str:
        """Git commit plus a digest of uncommitted changes, or a digest of the file listing outside Git."""
        try:
            head = self._git("rev-parse", "HEAD").strip()
            dirty = self._git("status", "--porcelain")
            diff = self._git("diff", "HEAD")
            return f"git:{head}" + (f"+dirty:{_sha(dirty + diff)[:16]}" if dirty.strip() else "")
        except (OSError, subprocess.CalledProcessError):
            listing = sorted(
                f"{p.relative_to(self.workspace).as_posix()}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                for p in self.workspace.rglob("*")
                if p.is_file() and ".ananke" not in p.parts and ".git" not in p.parts
            )
            return "files:" + _sha("\n".join(listing))[:32]

    def resolve_capabilities(self, refs: list[str]) -> list[dict[str, str]]:
        from ananke.plexus.registry.providers import SourceError, from_project

        try:
            providers = from_project(self.workspace)
            out = []
            for ref in refs:
                d = providers.resolve(ref)
                out.append({"identity": d.identity, "version": d.version, "digest": d.digest})
            return out
        except SourceError as exc:
            raise AppError(
                exc.code, exc.message, retryable=exc.code == "SOURCE_UNAVAILABLE"
            ) from exc

    # ------------------------------------------------------------------ runs
    def prepare_run(
        self,
        kind: str,
        parameters: dict[str, Any] | None = None,
        capability_refs: list[str] | None = None,
    ) -> PreparedRun:
        if kind not in self.kinds:
            raise AppError(
                "UNKNOWN_RUN_KIND",
                f"no run kind {kind!r}; this Ananke offers {', '.join(sorted(self.kinds))}",
            )
        params = dict(parameters or {})
        caps = tuple(self.resolve_capabilities(capability_refs or []))
        snapshot = self.source_snapshot()
        digest = _sha(
            _canonical(
                {
                    "kind": kind,
                    "parameters": params,
                    "capabilities": caps,
                    "source_snapshot": snapshot,
                }
            )
        )
        return PreparedRun(kind, params, caps, snapshot, digest)

    def submit_run(
        self, prepared: PreparedRun, idempotency_key: str, *, start: bool = True
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise AppError("INVALID_REQUEST", "an idempotency key is required")
        request = prepared.request(self.workspace_id(), idempotency_key)
        index_path = self.state / "idempotency.json"
        index = json.loads(index_path.read_text()) if index_path.is_file() else {}
        seen = index.get(idempotency_key)
        if seen:
            if seen["request_sha256"] != _sha(_canonical(request)):
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    f"key {idempotency_key!r} was used for a different request (run {seen['run_id']})",
                )
            return self.get_run(seen["run_id"])
        if self.source_snapshot() != prepared.source_snapshot:
            raise AppError(
                "STALE_PLAN",
                "the workspace changed after the plan was prepared; prepare it again",
                retryable=True,
            )
        run_id = "run-" + uuid.uuid4().hex[:16]
        run = {
            "schema_version": RESULT_SCHEMA,
            "run_id": run_id,
            "kind": prepared.kind,
            "state": "queued",
            "source_snapshot": prepared.source_snapshot,
            "locked_plan_digest": prepared.locked_plan_digest,
            "workspace_id": request["workspace_id"],
            "evidence_refs": [],
            "artifact_refs": [],
            "complete": False,
            "warnings": [],
            "request": request,
            "created_at": _now(),
        }
        self._save(run)
        index[idempotency_key] = {"run_id": run_id, "request_sha256": _sha(_canonical(request))}
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True))
        self._event(run_id, "run.queued", {"kind": prepared.kind})
        return self.drive(run_id) if start else self.get_run(run_id)

    def drive(self, run_id: str) -> dict[str, Any]:
        """Execute a queued run to a terminal state (in-process transport: synchronous)."""
        run = self.get_run(run_id)
        if run["state"] != "queued":
            return run
        self._transition(run, "running")
        try:
            self.kinds[run["kind"]](self, run_id, run["request"]["parameters"])
        except AppError as exc:
            run = self.get_run(run_id)
            run["warnings"].append(exc.as_dict())
            return self._transition(run, "failed")
        except Exception as exc:
            run = self.get_run(run_id)
            run["warnings"].append(
                {"code": "HARNESS_FAILED", "message": str(exc), "retryable": False}
            )
            return self._transition(run, "failed")
        run = self.get_run(run_id)
        run["complete"] = not any(w.get("code") == "ARTIFACT_FAILED" for w in run["warnings"])
        return self._transition(run, "succeeded")

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = self.state / "runs" / run_id / "run.json"
        if not path.is_file():
            raise AppError("RUN_NOT_FOUND", f"no run {run_id!r} in this workspace")
        run: dict[str, Any] = json.loads(path.read_text())
        return run

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["state"] in TERMINAL:
            run["warnings"].append(
                {
                    "code": "ALREADY_FINISHED",
                    "message": f"run is {run['state']}; completed side effects are not rolled back",
                    "retryable": False,
                }
            )
            self._save(run)
            return run
        return self._transition(run, "cancelled")

    # ------------------------------------------------------------------ events
    def stream_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        """Events with ``seq > after``. If retention dropped some of them, say which and include the run state."""
        self.get_run(run_id)
        path = self.state / "runs" / run_id / "events.jsonl"
        events = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        )
        first = events[0]["seq"] if events else 1
        out: dict[str, Any] = {"run_id": run_id, "events": [e for e in events if e["seq"] > after]}
        if after + 1 < first:
            out["gap"] = {"from": after + 1, "to": first - 1}
            out["run"] = self.get_run(run_id)
        return out

    # ------------------------------------------------------------------ artifacts
    def register_artifact(
        self,
        run_id: str,
        name: str,
        data: bytes,
        *,
        media_type: str,
        producer: str,
        input_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Store bytes an internal or external producer made for a run, with its digest and the inputs it used."""
        if not name or "/" in name or name.startswith("."):
            raise AppError("INVALID_ARTIFACT", f"artifact names are plain file names, not {name!r}")
        run = self.get_run(run_id)
        folder = self.state / "runs" / run_id / "artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        ref = {
            "name": name,
            "media_type": media_type,
            "sha256": _sha(data),
            "size": len(data),
            "producer": producer,
            "input_refs": list(input_refs or []),
            "registered_at": _now(),
        }
        run["artifact_refs"] = [a for a in run["artifact_refs"] if a["name"] != name] + [ref]
        self._save(run)
        self._event(
            run_id, "artifact.registered", {k: ref[k] for k in ("name", "sha256", "producer")}
        )
        return ref

    def register_failure(self, run_id: str, producer: str, message: str) -> dict[str, Any]:
        """A producer that couldn't make its artifact says so; the run shows it and is marked incomplete."""
        run = self.get_run(run_id)
        run["warnings"].append(
            {"code": "ARTIFACT_FAILED", "message": f"{producer}: {message}", "retryable": True}
        )
        run["complete"] = False
        self._save(run)
        self._event(run_id, "artifact.failed", {"producer": producer, "message": message})
        return run

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = self.get_run(run_id)["artifact_refs"]
        return artifacts

    def read_artifact(self, run_id: str, name: str) -> bytes:
        ref = next((a for a in self.list_artifacts(run_id) if a["name"] == name), None)
        if ref is None:
            raise AppError("ARTIFACT_NOT_FOUND", f"run {run_id} has no artifact {name!r}")
        data = (self.state / "runs" / run_id / "artifacts" / name).read_bytes()
        if _sha(data) != ref["sha256"]:
            raise AppError(
                "DIGEST_MISMATCH",
                f"artifact {name} of {run_id} changed on disk after it was registered",
            )
        return data

    # ------------------------------------------------------------------ workflows (nothing here runs anything)
    def _registry(self) -> Any:
        from ananke.plexus.registry.registry import Registry

        return Registry.for_project(
            self.workspace,
            create=not (self.workspace / ".ananke" / "registry" / "registry.db").exists(),
        )

    def validate_workflow(self, draft: dict[str, Any]) -> dict[str, Any]:
        from ananke.plexus.assembly import validate

        return validate(draft, self._registry()).as_dict()

    def preview_workflow(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Order, locked versions, permissions and approvals. No tool runs and nothing is written."""
        from ananke.plexus.assembly import preview

        return preview(draft, self._registry())

    def compile_workflow(self, draft: dict[str, Any]) -> dict[str, Any]:
        """The ``ExecutionPlan`` a draft compiles to, with its plan hash."""
        from ananke.plexus.assembly import CompileError, compile_workflow

        try:
            done = compile_workflow(draft, self._registry())
        except CompileError as exc:
            first = exc.validation.issues[0]
            raise AppError(first.code, str(exc)) from exc
        return {
            "plan_hash": done.plan_hash,
            "registry_lock_digest": done.registry_lock_digest,
            "locked": done.locked,
            "plan": done.plan.model_dump(mode="json"),
        }

    def suggest_capabilities(
        self, draft: dict[str, Any], node: str, port: str, candidates: list[str]
    ) -> dict[str, Any]:
        """Which candidates fit an input port, why others don't, and which are authorized. Activates nothing."""
        from ananke.plexus.assembly import suggest

        return suggest(draft, node, port, candidates, self._registry())

    # ------------------------------------------------------------------ read-only projections for products
    def code_view(
        self,
        *,
        path_prefix: str = "",
        kinds: list[str] | None = None,
        query: str = "",
        limit: int = 2000,
    ) -> dict[str, Any]:
        """A filtered slice of the latest code graph. Inferred edges stay marked inferred, with their confidence."""
        from ananke.plexus.graph.service import load_graph

        snapshot = load_graph(self.workspace)
        if snapshot is None:
            raise AppError(
                "GRAPH_MISSING",
                "no code graph in this workspace yet; submit a graph.build run first",
                retryable=True,
            )
        words = query.lower().split()
        wanted = set(kinds or [])
        nodes = [
            n
            for n in snapshot.nodes
            if n.path.startswith(path_prefix)
            and (not wanted or n.kind in wanted)
            and all(w in f"{n.name} {n.path}".lower() for w in words)
        ]
        truncated = len(nodes) > limit
        nodes = nodes[:limit]
        keep = {n.node_id for n in nodes}
        return {
            "snapshot_id": snapshot.snapshot_id,
            "provider": snapshot.provider,
            "source_snapshot": self.source_snapshot(),
            "truncated": truncated,
            "nodes": [n.model_dump(mode="json") for n in nodes],
            "edges": [
                e.model_dump(mode="json")
                for e in snapshot.edges
                if e.source in keep and e.target in keep
            ],
        }

    def read_source(self, path: str, *, start: int = 1, end: int | None = None) -> dict[str, Any]:
        """Lines of one source file, read-only, inside the workspace. Evidence for a claim, with its digest."""
        target = (self.workspace / path).resolve()
        if self.workspace not in target.parents:
            raise AppError("PATH_ESCAPE", f"{path} is outside the workspace")
        parts = target.relative_to(self.workspace).parts
        if parts[0] in (".ananke", ".git") or target.name.startswith(".env"):
            raise AppError(
                "PATH_DENIED",
                f"{path} isn't source: harness state, Git data and environment files aren't served",
            )
        if not target.is_file():
            raise AppError("SOURCE_NOT_FOUND", f"{path} isn't a file in this workspace")
        data = target.read_bytes()
        lines = data.decode("utf-8", "replace").splitlines()
        first = max(1, start)
        last = min(
            len(lines),
            end if end is not None else first + MAX_SOURCE_LINES - 1,
            first + MAX_SOURCE_LINES - 1,
        )
        return {
            "path": path,
            "start": first,
            "end": last,
            "total_lines": len(lines),
            "sha256": _sha(data),
            "source_snapshot": self.source_snapshot(),
            "lines": lines[first - 1 : last],
        }

    def architecture_view(self) -> dict[str, Any]:
        """The declared architecture and how it differs from the code graph, both read-only."""
        from ananke.plexus.architecture.calm import diff_calm, load_calm, validate_calm

        # those functions create a default model when none exists; a read-only view must not
        if not (self.workspace / ".ananke" / "architecture" / "system.calm.json").is_file():
            raise AppError(
                "ARCHITECTURE_MISSING",
                "no architecture model here: run `ananke arch init`",
                retryable=True,
            )
        return {
            "model": load_calm(self.workspace),
            "validation": validate_calm(self.workspace),
            "drift": diff_calm(self.workspace),
        }

    def evidence_view(self, *, limit: int = 100) -> dict[str, Any]:
        """Recent runs in this workspace: state, completeness, plan digest and artifacts."""
        root = self.state / "runs"
        runs = (
            [
                json.loads((d / "run.json").read_text())
                for d in root.iterdir()
                if (d / "run.json").is_file()
            ]
            if root.is_dir()
            else []
        )
        runs.sort(key=lambda r: (r.get("created_at", ""), r["run_id"]), reverse=True)
        keys = (
            "run_id",
            "kind",
            "state",
            "complete",
            "source_snapshot",
            "locked_plan_digest",
            "created_at",
            "artifact_refs",
            "warnings",
        )
        return {"total": len(runs), "runs": [{k: r.get(k) for k in keys} for r in runs[:limit]]}

    # ------------------------------------------------------------------ internals
    def _save(self, run: dict[str, Any]) -> None:
        folder = self.state / "runs" / run["run_id"]
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "run.json.tmp"
        tmp.write_text(json.dumps(run, indent=2, sort_keys=True))
        tmp.replace(folder / "run.json")

    def _transition(self, run: dict[str, Any], state: str) -> dict[str, Any]:
        run["state"] = state
        self._save(run)
        self._event(run["run_id"], f"run.{state}", {"complete": run["complete"]})
        return run

    def _event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        path = self.state / "runs" / run_id / "events.jsonl"
        lines = path.read_text().splitlines() if path.is_file() else []
        seq = json.loads(lines[-1])["seq"] + 1 if lines else 1
        lines.append(
            _canonical(
                {
                    "schema": EVENT_SCHEMA,
                    "run_id": run_id,
                    "seq": seq,
                    "type": kind,
                    "at": _now(),
                    "payload": payload,
                }
            )
        )
        path.write_text("\n".join(lines[-EVENT_RETENTION:]) + "\n")


# ---------------------------------------------------------------------- run kinds: thin calls into the harness
def _graph_build(app: AppFacade, run_id: str, params: dict[str, Any]) -> None:
    from ananke.plexus.api import Ananke

    result = Ananke.open(app.workspace).graph_build()
    if not result.ok:
        raise AppError("HARNESS_FAILED", result.summary)
    snapshot = Path(str(result.details["path"]))
    app.register_artifact(
        run_id,
        "graph-snapshot.json",
        snapshot.read_bytes(),
        media_type="application/json",
        producer="ananke.graph.native",
        input_refs=[f"source:{app.source_snapshot()}"],
    )


def _graph_impact(app: AppFacade, run_id: str, params: dict[str, Any]) -> None:
    from ananke.plexus.api import Ananke

    changed = params.get("changed_files")
    if not isinstance(changed, list) or not changed:
        raise AppError(
            "INVALID_REQUEST",
            "graph.impact needs parameters.changed_files: a non-empty list of paths",
        )
    result = Ananke.open(app.workspace).graph_impact([str(c) for c in changed])
    app.register_artifact(
        run_id,
        "impact.json",
        _canonical(result.model_dump()).encode(),
        media_type="application/json",
        producer="ananke.graph.impact",
        input_refs=[f"file:{c}" for c in changed],
    )


__all__ = ["PROTOCOL", "SUPPORTED", "AppError", "AppFacade", "PreparedRun"]

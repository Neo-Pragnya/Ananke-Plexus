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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

PROTOCOL = "ananke-app/1"
SUPPORTED = (PROTOCOL,)
EVENT_SCHEMA = "ananke-app/event@1"
RESULT_SCHEMA = "1.0.0"  # run-result.schema.json schema_version
TERMINAL = {"succeeded", "failed", "cancelled"}
EVENT_RETENTION = 1000
MAX_GRAPH_BUDGET = 20_000
MAX_EDGE_LOCATIONS = 5
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


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Exclusive across threads and processes: a CLI and a gateway can share a workspace. The OS releases it if
    the holder dies, so there is nothing stale to clean up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            import msvcrt  # type: ignore[unreachable,unused-ignore]

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined,unused-ignore]
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0)  # type: ignore[unreachable,unused-ignore]
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined,unused-ignore]


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
            "workflow.run": _workflow_run,
            "repo.analyze": _repo_analyze,
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
                "context_map",
                "context_status",
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
            # Ananke's own state isn't source: a run must not change the snapshot of the next one
            source_only = ("--", ".", ":(exclude).ananke")
            dirty = self._git("status", "--porcelain", *source_only)
            diff = self._git("diff", "HEAD", *source_only)
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
        self.state.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.state / "idempotency.lock"):
            run_id, created = self._claim_key(prepared, request, idempotency_key, index_path)
        if not created:
            return self.get_run(run_id)
        self._event(run_id, "run.queued", {"kind": prepared.kind})
        return self.drive(run_id) if start else self.get_run(run_id)

    def _claim_key(
        self,
        prepared: PreparedRun,
        request: dict[str, Any],
        idempotency_key: str,
        index_path: Path,
    ) -> tuple[str, bool]:
        """The run for this key: the existing one, or a new one. Two callers with one key get one run."""
        index = json.loads(index_path.read_text()) if index_path.is_file() else {}
        seen = index.get(idempotency_key)
        if seen:
            if seen["request_sha256"] != _sha(_canonical(request)):
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    f"key {idempotency_key!r} was used for a different request (run {seen['run_id']})",
                )
            return str(seen["run_id"]), False
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
        (self._run_dir(run_id)).mkdir(parents=True, exist_ok=True)
        self._save(run)
        index[idempotency_key] = {"run_id": run_id, "request_sha256": _sha(_canonical(request))}
        tmp = index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True))
        tmp.replace(index_path)
        return run_id, True

    def drive(self, run_id: str) -> dict[str, Any]:
        """Execute a queued run to a terminal state (in-process transport: synchronous)."""
        # claim it under the lock: of two callers, one runs it and the other gets the run as it stands
        with _file_lock(self._run_dir(run_id) / "run.lock"):
            run = self.get_run(run_id)
            if run["state"] != "queued":
                return run
            run["state"] = "running"
            self._write(run)
            self._event(run_id, "run.running", {"complete": run["complete"]})
        try:
            self.kinds[run["kind"]](self, run_id, run["request"]["parameters"])
        except AppError as exc:
            run = self.get_run(run_id)
            if run["state"] == "cancelled":
                return run
            run["warnings"].append(exc.as_dict())
            return self._transition(run, "failed")
        except Exception as exc:
            run = self.get_run(run_id)
            if run["state"] == "cancelled":
                return run
            run["warnings"].append(
                {"code": "HARNESS_FAILED", "message": str(exc), "retryable": False}
            )
            return self._transition(run, "failed")
        run = self.get_run(run_id)
        # cancelled while it ran: that stands, whatever finished first
        if run["state"] == "cancelled":
            return run
        incomplete = {"ARTIFACT_FAILED", "SIMULATED_BACKEND"}
        run["complete"] = not any(w.get("code") in incomplete for w in run["warnings"])
        return self._transition(run, "succeeded")

    def _run_dir(self, run_id: str) -> Path:
        from ananke.plexus.core.paths import UnsafeIdentifierError, safe_child

        try:
            return safe_child(self.state / "runs", run_id)
        except UnsafeIdentifierError as exc:
            raise AppError("RUN_NOT_FOUND", f"no run {run_id!r} in this workspace") from exc

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "run.json"
        if not path.is_file():
            raise AppError("RUN_NOT_FOUND", f"no run {run_id!r} in this workspace")
        run: dict[str, Any] = json.loads(path.read_text())
        return run

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        self.get_run(run_id)
        with _file_lock(self._run_dir(run_id) / "run.lock"):
            run = self.get_run(run_id)
            if run["state"] in TERMINAL:
                run["warnings"].append(
                    {
                        "code": "ALREADY_FINISHED",
                        "message": f"run is {run['state']}; completed side effects are not rolled back",
                        "retryable": False,
                    }
                )
                self._write(run)
                return run
            run["state"] = "cancelled"
            self._write(run)
            self._event(run_id, "run.cancelled", {"complete": run["complete"]})
            return run

    # ------------------------------------------------------------------ events
    def stream_events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        """Events with ``seq > after``. If retention dropped some of them, say which and include the run state."""
        self.get_run(run_id)
        path = self._run_dir(run_id) / "events.jsonl"
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
        folder = self._run_dir(run_id) / "artifacts"
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
        data = (self._run_dir(run_id) / "artifacts" / name).read_bytes()
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
        budget: int = 500,
        collapse: str = "none",
        depth: int = 1,
    ) -> dict[str, Any]:
        """A slice of the latest code graph, read-only, within a node budget.

        Over budget, the most connected nodes stay. ``collapse`` groups nodes by ``file`` or ``directory`` (to
        ``depth`` levels) and sums the edges between groups, so nothing vanishes silently: ``edge_accounting`` says
        where every edge touching the selection went. Inferred edges stay marked inferred, with their confidence.
        """
        from ananke.plexus.graph.service import graph_digest, load_graph

        if not 1 <= budget <= MAX_GRAPH_BUDGET:
            raise AppError(
                "INVALID_REQUEST", f"budget must be 1 to {MAX_GRAPH_BUDGET}, not {budget}"
            )
        if collapse not in ("none", "file", "directory"):
            raise AppError(
                "INVALID_REQUEST", f"collapse must be none, file or directory, not {collapse}"
            )
        if depth < 1:
            raise AppError("INVALID_REQUEST", f"depth must be at least 1, not {depth}")
        snapshot = load_graph(self.workspace)
        if snapshot is None:
            raise AppError(
                "GRAPH_MISSING",
                "no code graph in this workspace yet; submit a graph.build run first",
                retryable=True,
            )
        words = query.lower().split()
        wanted = set(kinds or [])
        selected = [
            n
            for n in snapshot.nodes
            if n.path.startswith(path_prefix)
            and (not wanted or n.kind in wanted)
            and all(w in f"{n.name} {n.path}".lower() for w in words)
        ]
        # An import names a module; inside this repository, that is a file. A snapshot built before imports were
        # resolved only has the module name to go on.
        legacy = not any(e.resolved_target for e in snapshot.edges)
        modules: dict[str, str] = {}
        for n in snapshot.nodes if legacy else ():
            module = n.metadata.get("module")
            if n.kind == "file" and isinstance(module, str):
                modules[f"module:{module}"] = n.node_id
                modules[f"module:{module.removeprefix('src.')}"] = n.node_id

        def group_of(node: Any) -> str:
            if collapse == "none":
                return str(node.node_id)
            if collapse == "file":
                return f"file:{node.path}" if node.kind != "data" else str(node.node_id)
            parents = node.path.split("/")[:-1][:depth]
            return f"dir:{'/'.join(parents) or '.'}"

        all_groups = {n.node_id: group_of(n) for n in snapshot.nodes}
        members: dict[str, list[Any]] = {}
        for n in selected:
            members.setdefault(all_groups[n.node_id], []).append(n)
        picked = {n.node_id for n in selected}
        touching = []
        external = 0
        for edge in snapshot.edges:
            target = edge.resolved_target or edge.target
            if target.startswith("module:") and edge.source.endswith((".py", ".ipynb")):
                target = modules.get(target, target)
            if edge.source not in picked and target not in picked:
                continue
            if target not in all_groups:
                external += 1  # a third-party import: true, but nothing here to draw it to
                continue
            touching.append((edge, target))
        degree: dict[str, int] = {}
        for edge, target in touching:
            for end in {all_groups[edge.source], all_groups[target]}:
                degree[end] = degree.get(end, 0) + 1
        ordered = sorted(members, key=lambda g: (-degree.get(g, 0), g))
        keep = set(ordered[:budget])
        boundary = internal = 0
        plain: list[dict[str, Any]] = []
        summed: dict[tuple[str, str], dict[str, Any]] = {}
        for edge, target in touching:
            source_group, target_group = all_groups[edge.source], all_groups[target]
            if source_group not in keep or target_group not in keep:
                boundary += 1
            elif collapse == "none":
                row = edge.model_dump(mode="json")
                if target != edge.target:
                    row["target"], row["via_module"] = target, edge.target.removeprefix("module:")
                plain.append(row)
            elif source_group == target_group:
                internal += 1
            else:
                slot = summed.setdefault(
                    (source_group, target_group),
                    {
                        "source": source_group,
                        "target": target_group,
                        "count": 0,
                        "kinds": {},
                        "origins": {},
                        "locations": [],
                    },
                )
                slot["count"] += 1
                slot["kinds"][edge.kind] = slot["kinds"].get(edge.kind, 0) + 1
                slot["origins"][edge.origin] = slot["origins"].get(edge.origin, 0) + 1
                if edge.source_location:
                    slot["locations"].append(edge.source_location)
        for slot in summed.values():
            slot["locations"] = sorted(set(slot["locations"]))[:MAX_EDGE_LOCATIONS]

        def located(node: Any) -> dict[str, Any]:
            row: dict[str, Any] = node.model_dump(mode="json")
            line, cell = node.metadata.get("line"), node.metadata.get("cell")
            row["source_location"] = (
                f"{node.path}#cell={cell}:{line}"
                if cell
                else f"{node.path}:{line}"
                if line
                else node.path
            )
            return row

        def grouped(group: str) -> dict[str, Any]:
            rows = members[group]
            counts: dict[str, int] = {}
            for n in rows:
                counts[n.kind] = counts.get(n.kind, 0) + 1
            path = group.partition(":")[2]
            kind = (
                "directory"
                if group.startswith("dir:")
                else "data"
                if group.startswith("data:")
                else "file"
            )
            return {
                "node_id": group,
                "kind": kind,
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "members": len(rows),
                "member_kinds": dict(sorted(counts.items())),
            }

        shown = sorted(keep)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "content_digest": graph_digest(snapshot),
            "provider": snapshot.provider,
            "source_snapshot": self.source_snapshot(),
            "collapse": collapse,
            "budget": budget,
            "total_nodes": len(ordered),
            "truncated": len(ordered) > budget,
            "nodes": (
                [located(members[g][0]) for g in shown]
                if collapse == "none"
                else [grouped(g) for g in shown]
            ),
            "edges": plain if collapse == "none" else [summed[k] for k in sorted(summed)],
            "edge_accounting": {
                "shown": len(plain) + sum(v["count"] for v in summed.values()),
                "inside_a_group": internal,
                "to_nodes_not_shown": boundary,
                "external_imports": external,
            },
        }

    def context_map(self, *, budget: int = 1500, refresh: bool = False) -> dict[str, Any]:
        """A small picture of the repository, rebuilt only when its sources change."""
        from ananke.plexus.context import ContextError, repo_map

        try:
            return repo_map(self.workspace, budget=budget, refresh=refresh)
        except ContextError as exc:
            raise AppError(exc.code, exc.message) from exc

    def context_status(self) -> dict[str, Any]:
        """Which tools for saving tokens are ready, what each is for, and how to get the others. Runs none of them."""
        from ananke.plexus.context import status

        return status(self.workspace)

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
    def _write(self, run: dict[str, Any]) -> None:
        """Replace run.json atomically, so a reader never sees half of it. Call with the run's lock held."""
        folder = self.state / "runs" / run["run_id"]
        tmp = folder / "run.json.tmp"
        tmp.write_text(json.dumps(run, indent=2, sort_keys=True))
        tmp.replace(folder / "run.json")

    def _settled_state(self, run_id: str) -> str | None:
        """The state on disk if it is final. A final state stands, whatever another thread is still writing."""
        path = self._run_dir(run_id) / "run.json"
        state = json.loads(path.read_text())["state"] if path.is_file() else None
        return state if state in TERMINAL else None

    def _save(self, run: dict[str, Any]) -> None:
        with _file_lock(self.state / "runs" / run["run_id"] / "run.lock"):
            settled = self._settled_state(run["run_id"])
            if settled:
                run["state"] = settled
            self._write(run)

    def _transition(self, run: dict[str, Any], state: str) -> dict[str, Any]:
        with _file_lock(self.state / "runs" / run["run_id"] / "run.lock"):
            settled = self._settled_state(run["run_id"])
            if settled:
                run["state"] = settled
                self._write(run)
                return run
            run["state"] = state
            self._write(run)
            self._event(run["run_id"], f"run.{state}", {"complete": run["complete"]})
        return run

    def _event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        folder = self._run_dir(run_id)
        path = folder / "events.jsonl"
        with _file_lock(folder / "events.lock"):
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
            tmp = folder / "events.jsonl.tmp"
            tmp.write_text("\n".join(lines[-EVENT_RETENTION:]) + "\n")
            tmp.replace(path)


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


def _repo_analyze(app: AppFacade, run_id: str, params: dict[str, Any]) -> None:
    from ananke.plexus.intel import analyze

    report = analyze(app.workspace, check_vulnerabilities=bool(params.get("check_vulnerabilities")))
    report["source_snapshot"] = app.source_snapshot()
    app.register_artifact(
        run_id,
        "repo-intel.json",
        json.dumps(report, indent=1, ensure_ascii=False).encode(),
        media_type="application/json",
        producer="ananke.intel",
        input_refs=[f"source:{report['source_snapshot']}"],
    )


def _workflow_run(app: AppFacade, run_id: str, params: dict[str, Any]) -> None:
    from ananke.plexus.workflow_run import execute

    execute(app, run_id, params)


__all__ = ["PROTOCOL", "SUPPORTED", "AppError", "AppFacade", "PreparedRun"]

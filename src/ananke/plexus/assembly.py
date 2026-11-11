"""Typed workflow assembly over the registry and ``ExecutionPlan`` (three-repo plan: ASM-A01..A04).

A workflow draft names capabilities and connects their typed ports. This module validates it and compiles it to the
existing :class:`~ananke.plexus.execution.plan.ExecutionPlan`, which the existing scheduler runs. There is no second
scheduler, registry or policy engine.

    draft → structure → resolve exact versions → ports and edges → DAG → policy → lock + hash → preview → compile

Rules:

* Identity, trust, lifecycle and permissions come from the registry record (:class:`CapabilityDescriptor`). A draft
  can't raise them: nothing it says about itself is trusted.
* Every reference must pin an exact version. A pinned digest must match.
* An edge connects an output port to an input port with the *same* ``schema_ref``. Markdown never silently satisfies
  a SARIF input, and an adapter must be an explicit node.
* ``permission_profile`` is an upper bound: a capability needing a permission outside it is rejected, and listing a
  permission never grants one the capability didn't declare.
* A compatible capability may still be unauthorized; the two answers are always reported separately.
* Validating, previewing, suggesting and compiling run nothing, install nothing and write nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ananke.plexus.execution.approvals import ApprovalClass
from ananke.plexus.execution.plan import ApprovalRequirement, ExecutionPlan, ExecutionStep
from ananke.plexus.execution.scheduler import RUN_CAPABILITY
from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import LifecycleStatus, TrustStatus, VersionRecord
from ananke.plexus.registry.registry import Registry

SCHEMA_VERSION = "1.0.0"
COMPILER_VERSION = "1"
STEP_TYPE = RUN_CAPABILITY
# What the draft schema allows. Anything else would be silently ignored, so it's rejected.
_DRAFT_FIELDS = frozenset(
    {"edges", "methodology", "nodes", "schema_version", "title", "ui", "workflow_id"}
)
_NODE_FIELDS = frozenset(
    {
        "approval_class",
        "capability_ref",
        "config",
        "id",
        "inputs",
        "kind",
        "outputs",
        "permission_profile",
        "retries",
    }
)
_EDGE_FIELDS = frozenset({"id", "source", "source_port", "target", "target_port"})
_PORT_FIELDS = frozenset({"multiple", "name", "required", "schema_ref"})
_KINDS = frozenset(
    {
        "skill",
        "agent",
        "instruction",
        "workflow",
        "plugin",
        "hook",
        "tool",
        "policy",
        "template",
        "evaluator",
    }
)
APPROVALS = set(ApprovalClass.__args__)  # type: ignore[attr-defined]
TRUST_ORDER = [
    TrustStatus.QUARANTINED,
    TrustStatus.RESTRICTED,
    TrustStatus.UNKNOWN,
    TrustStatus.DISCOVERED,
    TrustStatus.VERIFIED,
    TrustStatus.APPROVED,
]
# Draft node kinds → registry kinds (no blind cast: anything else is only informational).
KIND_MAP = {"instruction": "prompt", "plugin": "bundle", "hook": "tool"}
# Friendly names a profile may use → the permission categories the registry records.
PERMISSION_NAMES = {
    "filesystem-read": "filesystem.read",
    "filesystem-write": "filesystem.write",
    "shell": "shell.allow",
    "shell-execute": "shell.allow",
    "network": "network",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    node: str = ""
    remediation: str = ""


@dataclass(frozen=True)
class CapabilityDescriptor:
    """What the registry says about one exact version. The only source of trust and permissions."""

    ref: str
    uri: str
    kind: str
    version: str
    digest: str
    trust: str
    lifecycle: str
    permissions: tuple[str, ...]
    inputs: tuple[dict[str, Any], ...] | None  # None: the capability declares no ports
    outputs: tuple[dict[str, Any], ...] | None
    capabilities: tuple[str, ...] = ()


def _ports(contract: Any) -> tuple[dict[str, Any], ...] | None:
    ports = (contract.native or {}).get("ports") if contract is not None else None
    return tuple(ports) if isinstance(ports, list) else None


def describe(record: VersionRecord) -> CapabilityDescriptor:
    m = record.manifest
    return CapabilityDescriptor(
        ref=f"{record.namespace}/{record.name}@{record.version}",
        uri=record.uri,
        kind=record.kind.value,
        version=record.version,
        digest=record.digest_sha256,
        trust=record.trust.value,
        lifecycle=record.lifecycle.value,
        permissions=tuple(sorted({cat for cat, _ in m.permissions.flatten()})),
        inputs=_ports(m.inputs),
        outputs=_ports(m.outputs),
        capabilities=tuple(m.capabilities),
    )


def authorization_issues(
    d: CapabilityDescriptor, registry: Registry, node: str = ""
) -> list[Issue]:
    """Registry policy for one capability. Separate from compatibility, and never a score."""
    out = []
    policy = registry.policy.resolve
    trust = TrustStatus(d.trust)
    if d.lifecycle in (
        LifecycleStatus.QUARANTINED.value,
        LifecycleStatus.YANKED.value,
        LifecycleStatus.ARCHIVED.value,
    ):
        out.append(
            Issue(
                "LIFECYCLE_BLOCKED",
                f"{d.ref} is {d.lifecycle} and can't run",
                node,
                "choose another version",
            )
        )
    if d.lifecycle == LifecycleStatus.DEPRECATED.value and not policy.allow_deprecated:
        out.append(
            Issue(
                "LIFECYCLE_BLOCKED",
                f"{d.ref} is deprecated",
                node,
                "choose its replacement, or allow deprecated in policy",
            )
        )
    if trust is TrustStatus.QUARANTINED:
        out.append(
            Issue(
                "UNTRUSTED",
                f"{d.ref} is quarantined",
                node,
                "release it from quarantine after review",
            )
        )
    elif trust is TrustStatus.RESTRICTED and not policy.allow_restricted:
        out.append(
            Issue(
                "UNTRUSTED",
                f"{d.ref} is restricted",
                node,
                "promote it, or allow restricted in policy",
            )
        )
    elif trust is not TrustStatus.RESTRICTED and TRUST_ORDER.index(trust) < TRUST_ORDER.index(
        policy.minimum_trust
    ):
        out.append(
            Issue(
                "UNTRUSTED",
                f"{d.ref} has trust {d.trust}; policy needs {policy.minimum_trust.value}",
                node,
                f"ananke registry promote {d.ref}",
            )
        )
    return out


# ------------------------------------------------------------------------------------------ validation
@dataclass
class Validation:
    workflow_id: str
    issues: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    workflow_inputs: list[str] = field(default_factory=list)
    descriptors: dict[str, CapabilityDescriptor] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "ok": self.ok,
            "issues": [asdict(i) for i in self.issues],
            "warnings": [asdict(i) for i in self.warnings],
            "order": self.order,
            "workflow_inputs": self.workflow_inputs,
        }


def _topological(ids: list[str], edges: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """``(order, nodes on a cycle)``. Deterministic: ties break by id."""
    deps: dict[str, set[str]] = {i: set() for i in ids}
    for e in edges:
        if e.get("source") in deps and e.get("target") in deps:
            deps[e["target"]].add(e["source"])
    order: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(n for n, d in remaining.items() if not d & set(remaining))
        if not ready:
            return order, sorted(remaining)
        order.extend(ready)
        for n in ready:
            del remaining[n]
    return order, []


def validate(draft: dict[str, Any], registry: Registry) -> Validation:
    """Every problem at once, each with a stable code and a next step. Runs nothing."""
    v = Validation(str(draft.get("workflow_id") or ""))
    if draft.get("schema_version") != SCHEMA_VERSION:
        v.issues.append(
            Issue(
                "UNSUPPORTED_SCHEMA",
                f"schema_version must be {SCHEMA_VERSION}, not {draft.get('schema_version')!r}",
            )
        )
        return v
    nodes, edges = draft.get("nodes"), draft.get("edges")
    if not v.workflow_id or not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        v.issues.append(
            Issue(
                "INVALID_DRAFT",
                "a draft needs workflow_id, a non-empty nodes list and an edges list",
            )
        )
        return v
    _check_fields(draft, v)
    if v.issues:
        return v
    ids = [str(n.get("id") or "") for n in nodes]
    for dup in sorted({i for i in ids if ids.count(i) > 1 or not i}):
        v.issues.append(
            Issue("DUPLICATE_ID", f"node id {dup!r} is empty or used more than once", dup)
        )
    by_id = {str(n.get("id")): n for n in nodes}
    edge_ids = [e.get("id") for e in edges]
    for dup in sorted({str(i) for i in edge_ids if edge_ids.count(i) > 1}):
        v.issues.append(Issue("DUPLICATE_ID", f"edge id {dup!r} is used more than once"))

    for nid, n in by_id.items():
        _check_node(nid, n, registry, v)

    bound: dict[tuple[str, str], int] = {}
    for e in edges:
        _check_edge(e, by_id, v, bound)
    for nid, n in by_id.items():
        for port in n.get("inputs", []):
            count = bound.get((nid, port["name"]), 0)
            # an input of a node nothing feeds is supplied when the workflow runs, unless it says it must be bound
            is_entry = not any(e.get("target") == nid for e in edges)
            required = port.get("required")
            if count == 0 and is_entry and required is not True:
                v.workflow_inputs.append(f"{nid}.{port['name']}")
            elif count == 0 and required is not False:
                v.issues.append(
                    Issue(
                        "UNBOUND_INPUT",
                        f"{nid}.{port['name']} ({port['schema_ref']}) has no upstream output",
                        nid,
                        f"connect a node that outputs {port['schema_ref']}, or add an explicit adapter node",
                    )
                )
            if count > 1 and not port.get("multiple"):
                v.issues.append(
                    Issue(
                        "DUPLICATE_BINDING",
                        f"{nid}.{port['name']} is bound {count} times and doesn't accept a list",
                        nid,
                    )
                )

    v.order, cycle = _topological(sorted(by_id), edges)
    if cycle:
        v.issues.append(
            Issue(
                "CYCLE",
                f"the graph has a cycle through {', '.join(cycle)}",
                cycle[0],
                "retries are a node setting (retries), not a graph loop",
            )
        )
    return v


def _check_fields(draft: dict[str, Any], v: Validation) -> None:
    """Refuse what the compiler would otherwise drop without saying so."""

    def unknown(where: str, obj: Any, allowed: frozenset[str], subject: str = "") -> None:
        if not isinstance(obj, dict):
            v.issues.append(Issue("INVALID_DRAFT", f"{where} must be an object", subject))
            return
        for name in sorted(set(obj) - allowed):
            v.issues.append(
                Issue(
                    "INVALID_DRAFT",
                    f"{where}: unknown field {name!r}",
                    subject,
                    f"allowed: {', '.join(sorted(allowed))}",
                )
            )

    unknown("draft", draft, _DRAFT_FIELDS)
    for n in draft["nodes"]:
        unknown("node", n, _NODE_FIELDS)
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")
        if n.get("kind") not in _KINDS:
            v.issues.append(
                Issue(
                    "INVALID_DRAFT",
                    f"{nid}: unsupported kind {n.get('kind')!r}",
                    nid,
                    f"use one of: {', '.join(sorted(_KINDS))}",
                )
            )
        for side in ("inputs", "outputs"):
            ports = n.get(side)
            if not isinstance(ports, list):
                v.issues.append(Issue("INVALID_DRAFT", f"{nid}: {side} must be a list", nid))
                continue
            for port in ports:
                unknown(f"{nid} {side}", port, _PORT_FIELDS, nid)
                if isinstance(port, dict) and not (port.get("name") and port.get("schema_ref")):
                    v.issues.append(
                        Issue(
                            "INVALID_DRAFT", f"{nid} {side}: a port needs name and schema_ref", nid
                        )
                    )
    for e in draft["edges"]:
        unknown("edge", e, _EDGE_FIELDS)


def _check_node(nid: str, n: dict[str, Any], registry: Registry, v: Validation) -> None:
    ref = str(n.get("capability_ref") or "")
    if "@" not in ref:
        v.issues.append(
            Issue(
                "UNPINNED_REF",
                f"{nid}: {ref!r} must pin an exact version",
                nid,
                "write namespace/name@1.2.3",
            )
        )
        return
    base, _, pin = ref.partition("#sha256:")
    try:
        record = registry.exact_version(base)
    except RegistryError as exc:
        code = "UNPINNED_REF" if "exact version" in str(exc) else "UNRESOLVED_REF"
        v.issues.append(
            Issue(
                code,
                f"{nid}: {exc.message}",
                nid,
                "learn it into the registry, or fix the reference",
            )
        )
        return
    d = describe(record)
    v.descriptors[nid] = d
    if pin and pin != d.digest:
        v.issues.append(
            Issue(
                "DIGEST_MISMATCH", f"{nid}: {base} has digest {d.digest[:12]}, not {pin[:12]}", nid
            )
        )
    expected = KIND_MAP.get(str(n.get("kind")), str(n.get("kind")))
    if expected != d.kind:
        v.warnings.append(
            Issue(
                "KIND_DIFFERS",
                f"{nid}: the draft says {n.get('kind')}, the registry says {d.kind}",
                nid,
            )
        )
    v.issues.extend(authorization_issues(d, registry, nid))
    profile = {PERMISSION_NAMES.get(p, p) for p in n.get("permission_profile", [])}
    for missing in sorted(set(d.permissions) - profile):
        v.issues.append(
            Issue(
                "PERMISSION_NOT_GRANTED",
                f"{nid}: {d.ref} needs {missing}, which permission_profile doesn't allow",
                nid,
                f"add {missing} to permission_profile after reviewing it, or choose another capability",
            )
        )
    approval = n.get("approval_class", "NONE")
    if approval not in APPROVALS:
        v.issues.append(
            Issue(
                "UNKNOWN_APPROVAL",
                f"{nid}: approval_class {approval!r} isn't one of {', '.join(sorted(APPROVALS))}",
                nid,
            )
        )
    if not isinstance(n.get("retries", 0), int) or not 0 <= n.get("retries", 0) <= 10:
        v.issues.append(Issue("INVALID_RETRIES", f"{nid}: retries must be 0 to 10", nid))
    # Draft ports must agree with what the capability declares; undeclared ports are the draft's claim alone.
    for side, declared in (("inputs", d.inputs), ("outputs", d.outputs)):
        claimed = {(p["name"], p["schema_ref"]) for p in n.get(side, [])}
        if declared is None:
            if claimed:
                v.warnings.append(
                    Issue(
                        "PORTS_UNVERIFIED",
                        f"{nid}: {d.ref} declares no {side}; the draft's ports are unverified",
                        nid,
                    )
                )
        elif claimed != {(p["name"], p["schema_ref"]) for p in declared}:
            v.issues.append(
                Issue(
                    "PORT_CONTRACT_MISMATCH",
                    f"{nid}: the draft's {side} differ from those {d.ref} declares",
                    nid,
                )
            )


def _check_edge(
    e: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    v: Validation,
    bound: dict[tuple[str, str], int],
) -> None:
    src, tgt = by_id.get(str(e.get("source"))), by_id.get(str(e.get("target")))
    if src is None or tgt is None:
        v.issues.append(
            Issue(
                "UNKNOWN_NODE",
                f"edge {e.get('id')}: {e.get('source')} → {e.get('target')} names a node that doesn't exist",
            )
        )
        return
    out = next((p for p in src.get("outputs", []) if p["name"] == e.get("source_port")), None)
    inp = next((p for p in tgt.get("inputs", []) if p["name"] == e.get("target_port")), None)
    if out is None or inp is None:
        v.issues.append(
            Issue(
                "UNKNOWN_PORT",
                f"edge {e.get('id')}: {e.get('source')}.{e.get('source_port')} → {e.get('target')}.{e.get('target_port')} names a port that doesn't exist",
                str(e.get("target")),
            )
        )
        return
    bound[(str(e["target"]), inp["name"])] = bound.get((str(e["target"]), inp["name"]), 0) + 1
    if out["schema_ref"] != inp["schema_ref"]:
        v.issues.append(
            Issue(
                "PORT_MISMATCH",
                f"{tgt.get('capability_ref')} requires {inp['schema_ref']}, but {e.get('source')} provides {out['schema_ref']}",
                str(e.get("target")),
                f"add an explicit node that converts {out['schema_ref']} to {inp['schema_ref']}, or choose a compatible capability",
            )
        )


# ------------------------------------------------------------------------------------------ compile
@dataclass(frozen=True)
class CompiledWorkflow:
    plan: ExecutionPlan
    plan_hash: str
    registry_lock_digest: str
    locked: dict[str, dict[str, str]]


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_inputs(
    draft: dict[str, Any], v: Validation
) -> tuple[str, str, dict[str, dict[str, str]]]:
    locked = {
        nid: {"ref": d.ref, "uri": d.uri, "digest": d.digest}
        for nid, d in sorted(v.descriptors.items())
    }
    lock_digest = hashlib.sha256(_canonical(locked).encode()).hexdigest()
    nodes = [
        {
            k: n.get(k)
            for k in (
                "id",
                "kind",
                "inputs",
                "outputs",
                "config",
                "approval_class",
                "permission_profile",
                "retries",
            )
        }
        | {"digest": v.descriptors[str(n["id"])].digest}
        for n in sorted(draft["nodes"], key=lambda n: str(n["id"]))
    ]
    edges = sorted(
        (
            {k: e[k] for k in ("source", "source_port", "target", "target_port")}
            for e in draft["edges"]
        ),
        key=_canonical,
    )
    plan_hash = hashlib.sha256(
        _canonical(
            {
                "schema": SCHEMA_VERSION,
                "compiler": COMPILER_VERSION,
                "workflow_id": draft["workflow_id"],
                "nodes": nodes,
                "edges": edges,
            }
        ).encode()
    ).hexdigest()
    return plan_hash, lock_digest, locked


def _bindings(nid: str, edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in sorted(edges, key=_canonical):
        if e["target"] == nid:
            out.setdefault(e["target_port"], []).append(f"{e['source']}.{e['source_port']}")
    return out


def preview(draft: dict[str, Any], registry: Registry) -> dict[str, Any]:
    """What would run, in what order, with what permissions and approvals. Executes nothing."""
    v = validate(draft, registry)
    out = v.as_dict()
    if not v.ok:
        return out
    plan_hash, lock_digest, locked = _hash_inputs(draft, v)
    steps = []
    for nid in v.order:
        n = next(x for x in draft["nodes"] if x["id"] == nid)
        d = v.descriptors[nid]
        steps.append(
            {
                "node": nid,
                "capability": d.ref,
                "digest": d.digest,
                "trust": d.trust,
                "bindings": _bindings(nid, draft["edges"]),
                "permissions": list(d.permissions),
                "approval_class": n.get("approval_class", "NONE"),
                "retries": n.get("retries", 0),
            }
        )
    return out | {
        "plan_hash": plan_hash,
        "registry_lock_digest": lock_digest,
        "compiler_version": COMPILER_VERSION,
        "steps": steps,
        "locked": locked,
        "approvals_needed": sorted({s["approval_class"] for s in steps} - {"NONE"}),
    }


class CompileError(RuntimeError):
    def __init__(self, validation: Validation) -> None:
        super().__init__("; ".join(f"[{i.code}] {i.message}" for i in validation.issues))
        self.validation = validation


def compile_workflow(draft: dict[str, Any], registry: Registry) -> CompiledWorkflow:
    """The existing ``ExecutionPlan``, deterministically: same draft and same versions give the same plan and hash."""
    v = validate(draft, registry)
    if not v.ok:
        raise CompileError(v)
    plan_hash, lock_digest, locked = _hash_inputs(draft, v)
    by_id = {n["id"]: n for n in draft["nodes"]}
    steps, approvals = [], []
    for nid in v.order:
        n, d = by_id[nid], v.descriptors[nid]
        deps = sorted({e["source"] for e in draft["edges"] if e["target"] == nid})
        steps.append(
            ExecutionStep(
                step_id=nid,
                step_type=STEP_TYPE,
                depends_on=deps,
                params={
                    "capability_ref": d.ref,
                    "uri": d.uri,
                    "digest": d.digest,
                    "kind": d.kind,
                    "bindings": _bindings(nid, draft["edges"]),
                    "config": n.get("config", {}),
                    "retries": n.get("retries", 0),
                    "permissions": list(d.permissions),
                },
            )
        )
        approval = n.get("approval_class", "NONE")
        if approval != "NONE":
            approvals.append(
                ApprovalRequirement(
                    step_id=nid, approval_class=approval, description=f"Approve {d.ref}"
                )
            )
    plan = ExecutionPlan(
        plan_id=f"plan-{plan_hash[:16]}",
        spec_id=draft["workflow_id"],
        steps=steps,
        approvals=approvals,
    )
    return CompiledWorkflow(plan, plan_hash, lock_digest, locked)


# ------------------------------------------------------------------------------------------ suggestions
def suggest(
    draft: dict[str, Any], node_id: str, port: str, candidates: list[str], registry: Registry
) -> dict[str, Any]:
    """Which candidates could feed an input port, why the others can't, and which are authorized.

    Only explains. It never adds a node, activates anything or changes the draft.
    """
    node = next((n for n in draft.get("nodes", []) if n.get("id") == node_id), None)
    target = next((p for p in (node or {}).get("inputs", []) if p["name"] == port), None)
    if target is None:
        return {
            "error": {"code": "UNKNOWN_PORT", "message": f"{node_id} has no input port {port!r}"}
        }
    want = target["schema_ref"]
    rows = []
    for ref in candidates:
        row: dict[str, Any] = {
            "ref": ref,
            "compatible": False,
            "authorized": False,
            "reasons": [],
            "auto_activate": False,
        }
        try:
            d = describe(registry.exact_version(ref))
        except RegistryError as exc:
            row["reasons"].append(f"can't be resolved: {exc.message}")
            rows.append(row)
            continue
        if d.outputs is None:
            row["reasons"].append(f"declares no output ports, so nothing shows it provides {want}")
        else:
            match = next((p for p in d.outputs if p.get("schema_ref") == want), None)
            if match:
                row["compatible"], row["output_port"] = True, match.get("name")
                row["reasons"].append(f"output {match.get('name')} provides {want}")
            else:
                provided = (
                    ", ".join(sorted(str(p.get("schema_ref")) for p in d.outputs)) or "nothing"
                )
                row["reasons"].append(f"provides {provided}, not {want}")
        blockers = authorization_issues(d, registry)
        row["authorized"] = not blockers
        row["reasons"] += [i.message for i in blockers]
        rows.append(row)
    return {"node": node_id, "port": port, "requires": want, "candidates": rows}


def gaps(draft: dict[str, Any], registry: Registry) -> dict[str, Any]:
    """Every input that nothing feeds and the run won't supply, with the registered capabilities that could feed it.

    For each gap, the candidates are every registered version, explained by :func:`suggest`: compatible and
    authorized first, then compatible but not authorized. Like ``suggest``, it only explains: it adds no node,
    activates nothing and changes nothing (``auto_activate`` is always false).
    """
    v = validate(draft, registry)
    bound = {(e.get("target"), e.get("target_port")) for e in draft.get("edges", [])}
    supplied = set(v.workflow_inputs)
    refs = sorted(f"{r.namespace}/{r.name}@{r.version}" for r in registry.list_versions())
    out = []
    for node in draft.get("nodes", []):
        nid = node.get("id")
        own = node.get("capability_ref")
        for port in node.get("inputs", []):
            name = port.get("name")
            if (nid, name) in bound or f"{nid}.{name}" in supplied:
                continue
            answer = suggest(draft, str(nid), str(name), [r for r in refs if r != own], registry)
            rows = answer.get("candidates", [])
            fits = [r for r in rows if r["compatible"]]
            out.append(
                {
                    "node": nid,
                    "port": name,
                    "requires": port.get("schema_ref"),
                    "authorized": [r for r in fits if r["authorized"]],
                    "not_authorized": [r for r in fits if not r["authorized"]],
                    "incompatible": len(rows) - len(fits),
                    "fix": (
                        f"connect {fits[0]['ref']} ({fits[0].get('output_port')})"
                        if fits and fits[0]["authorized"]
                        else f"register or approve a capability that outputs {port.get('schema_ref')}, "
                        "or add an explicit adapter node"
                    ),
                }
            )
    return {
        "valid": v.ok,
        "gaps": out,
        "issues": [asdict(i) for i in v.issues if i.code != "UNBOUND_INPUT"],
        "auto_activate": False,
    }


def review_proposal(draft: dict[str, Any], registry: Registry, *, proposer: str) -> dict[str, Any]:
    """Check a workflow someone or something proposed: a person, a template, or a model a user chose to run.

    Ananke never calls a model itself. Whatever proposed the draft, it gets the same checks as a hand-written one
    (validation, policy, preview) and its gaps are named. A proposal is never compiled or run here: after a person
    reviews it, it goes through ``compile_workflow`` like any draft.
    """
    if not proposer.strip():
        raise ValueError("say what proposed this draft (a person, a template or a model)")
    v = validate(draft, registry)
    return {
        "status": "proposal",
        "proposer": proposer,
        "valid": v.ok,
        "issues": [asdict(i) for i in v.issues],
        "preview": preview(draft, registry) if v.ok else None,
        "gaps": gaps(draft, registry)["gaps"],
        "compiled": False,
        "next": "review it, then compile it with workflow-compile"
        if v.ok
        else "fix the issues, or reject the proposal",
    }


__all__ = [
    "SCHEMA_VERSION",
    "STEP_TYPE",
    "CapabilityDescriptor",
    "CompileError",
    "CompiledWorkflow",
    "Issue",
    "Validation",
    "authorization_issues",
    "compile_workflow",
    "describe",
    "gaps",
    "preview",
    "review_proposal",
    "suggest",
    "validate",
]

"""Policy-aware version resolver (spec §46-§54, §125, §126, §166).

Resolution is *not* "pick the highest semver". Each candidate version passes a pipeline of
rules (requirement, lifecycle, pre-release, channel, trust, license, security, quality,
compatibility). Accepted candidates are ranked; dependencies are solved together with
backtracking so a graph never contains two versions of one artifact. Every decision keeps
its reasons, which power ``--explain`` in the CLI and the docs.

Properties (verified by tests): same inputs -> same result; a locked version never silently
changes; quarantined versions are never selected; an exact pin selects only that version;
every dependency constraint is satisfied; the payload digest matches the lock.
"""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import (
    InvalidRequirementError,
    NotFoundError,
    RegistryError,
    ResolutionError,
)
from ananke.plexus.registry.models import (
    TRUST_RANK,
    ArtifactKind,
    DependencyType,
    LifecycleStatus,
    ResolutionMode,
    TrustStatus,
    VersionRecord,
    artifact_uri,
    parse_ref,
)
from ananke.plexus.registry.policy import RegistryPolicy
from ananke.plexus.registry.semver import Version, VersionReq, normalize_version
from ananke.plexus.version import __version__ as ANANKE_VERSION

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry

Key = tuple[str, str, str]
_STABLE_CHANNELS = {"stable", "approved"}


# --------------------------------------------------------------------------- models


def _detect_os() -> str:
    system = platform.system().lower()
    return {"darwin": "macos"}.get(system, system)


def _detect_arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)


class ResolutionEnvironment(BaseModel):
    ananke_version: str = ANANKE_VERSION
    python_version: str = Field(
        default_factory=lambda: (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
    )
    runtime: str | None = None
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    operating_system: str = Field(default_factory=_detect_os)
    architecture: str = Field(default_factory=_detect_arch)
    model_capabilities: list[str] | None = None
    mcp_protocol: str | None = None

    @classmethod
    def detect(cls, runtime: str | None = None, **overrides: Any) -> ResolutionEnvironment:
        return cls(runtime=runtime, **overrides)


class Requirement(BaseModel):
    kind: ArtifactKind | None = None
    namespace: str | None = None
    name: str
    req: str = "*"
    optional: bool = False
    requested_by: str | None = None

    @classmethod
    def parse(
        cls, text: str, kind: ArtifactKind | str | None = None, version: str | None = None
    ) -> Requirement:
        ref = parse_ref(text, kind)
        return cls(
            kind=ref.kind,
            namespace=ref.namespace,
            name=ref.name,
            req=ref.requirement or version or "*",
        )


class Reason(BaseModel):
    ok: bool
    text: str


class ArtifactVersionRef(BaseModel):
    uri: str
    version: str
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.uri}@{self.version}"


class CandidateDecision(BaseModel):
    version: str
    accepted: bool
    matches_requirement: bool = True
    reasons: list[Reason] = Field(default_factory=list)
    rank: int | None = None
    selected: bool = False


class ResolutionDecision(BaseModel):
    """Root-level decision (spec §126)."""

    requirement: str
    selected: ArtifactVersionRef | None = None
    candidates: list[CandidateDecision] = Field(default_factory=list)
    failure: str | None = None


class ResolvedNode(BaseModel):
    ref: ArtifactVersionRef
    kind: ArtifactKind
    root: bool = False
    requested_by: list[str] = Field(default_factory=list)
    dependencies: list[ArtifactVersionRef] = Field(default_factory=list)
    candidates: list[CandidateDecision] = Field(default_factory=list)
    source: str = "local-registry"
    dirty: bool = False
    override_path: str | None = None


class ResolutionResult(BaseModel):
    mode: ResolutionMode
    snapshot: str
    environment: ResolutionEnvironment
    decisions: list[ResolutionDecision] = Field(default_factory=list)
    nodes: list[ResolvedNode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    lock_preserved: bool | None = None

    @property
    def ok(self) -> bool:
        return all(d.failure is None for d in self.decisions)

    @property
    def selected(self) -> ArtifactVersionRef | None:
        return self.decisions[0].selected if self.decisions else None

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise ResolutionError(self.explain())

    def explain(self) -> str:
        """Human explanation in the layout of spec §54."""
        lines: list[str] = []
        for d in self.decisions:
            if d.selected is not None:
                sel = next((c for c in d.candidates if c.selected), None)
                lines.append(f"Selected {d.selected.version} of {d.selected.uri} because:")
                for r in sel.reasons if sel else []:
                    if r.ok:
                        lines.append(f"  ✓ {r.text}")
            else:
                lines.append(
                    f"Could not resolve {d.requirement}: {d.failure or 'no acceptable version'}"
                )
            for c in d.candidates:
                if c.selected or not c.matches_requirement:
                    continue
                bad = [r for r in c.reasons if not r.ok]
                if bad:
                    lines.append(f"Rejected {c.version}:")
                    lines += [f"  ✗ {r.text}" for r in bad]
            hidden = sum(1 for c in d.candidates if not c.matches_requirement)
            if hidden:
                lines.append(f"({hidden} other version(s) do not match the requirement)")
            lines.append("")
        deps = [n for n in self.nodes if not n.root]
        if deps:
            lines.append("Dependency graph:")
            for n in self.nodes:
                marker = "" if not n.root else " (root)"
                lines.append(f"  {n.ref.ref}{marker}")
                for dep in n.dependencies:
                    lines.append(f"    -> {dep.ref}")
        for w in self.warnings:
            lines.append(f"warning: {w}")
        return "\n".join(lines).rstrip()


class ResolutionOptions(BaseModel):
    mode: ResolutionMode | None = None
    allow_yanked: bool = False
    allow_deprecated: bool | None = None
    allow_prerelease: bool | None = None
    channels: list[str] | None = None
    minimum_trust: TrustStatus | None = None
    pins: dict[str, tuple[str, str]] = Field(default_factory=dict)  # uri -> (version, sha256)
    pin_mode: Literal["none", "prefer", "strict"] = "none"
    max_steps: int = 5000


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class RuleOutcome:
    kind: Literal["accept", "reject", "penalty", "preference"]
    reason: str
    amount: float = 0.0


def accept(reason: str) -> RuleOutcome:
    return RuleOutcome("accept", reason)


def reject(reason: str) -> RuleOutcome:
    return RuleOutcome("reject", reason)


@dataclass
class RuleContext:
    registry: Registry
    policy: RegistryPolicy
    env: ResolutionEnvironment
    mode: ResolutionMode
    requirement: Requirement
    version_req: VersionReq
    options: ResolutionOptions
    locked: bool
    explicit_pin: bool
    allowed_channels: list[str]
    min_trust: TrustStatus
    allow_prerelease: bool
    allow_deprecated: bool


class ResolverRule(Protocol):
    """Extension point (spec §125): rules emit accept/reject/penalty/preference."""

    id: str

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]: ...


class LifecycleRule:
    id = "lifecycle"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        status = rec.lifecycle
        if status is LifecycleStatus.QUARANTINED:
            return [reject("quarantined (never selected)")]
        if status is LifecycleStatus.ACTIVE:
            return [accept("active")]
        if status is LifecycleStatus.YANKED:
            if ctx.locked and ctx.policy.resolve.allow_yanked_when_locked:
                return [accept("yanked but pinned by lockfile")]
            if ctx.options.allow_yanked:
                return [accept("yanked (explicitly allowed)")]
            return [reject("yanked")]
        if status is LifecycleStatus.DEPRECATED:
            if ctx.allow_deprecated or ctx.locked or ctx.explicit_pin:
                return [accept("deprecated (allowed: pinned/locked/explicit)")]
            hint = f" — use {rec.replacement}" if rec.replacement else ""
            return [reject(f"deprecated{hint}")]
        if ctx.locked:
            return [accept("archived but pinned by lockfile")]
        return [reject("archived")]


class PrereleaseRule:
    id = "prerelease"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        if not rec.semver.is_prerelease:
            return [accept("release version")]
        if (
            ctx.allow_prerelease
            or ctx.explicit_pin
            or ctx.locked
            or ctx.version_req.allows_prerelease()
        ):
            return [accept("pre-release allowed")]
        return [reject("pre-release not allowed")]


class ChannelRule:
    id = "channel"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        allowed = ctx.allowed_channels
        if ctx.version_req.symbol == "stable" or ctx.mode is ResolutionMode.STABLE_ONLY:
            allowed = ["stable"]
        if rec.channel in allowed:
            return [accept(rec.channel)]
        if ctx.locked:
            return [accept(f"{rec.channel} channel (pinned by lockfile)")]
        if rec.channel == "deprecated" and (ctx.allow_deprecated or ctx.explicit_pin):
            return [accept("deprecated channel (allowed: explicit pin or policy)")]
        return [reject(f"{rec.channel} channel not allowed")]


class TrustRule:
    id = "trust"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        if rec.trust is TrustStatus.QUARANTINED:
            return [reject("trust status = quarantined")]
        floor = ctx.min_trust
        if ctx.version_req.symbol == "approved":
            floor = TrustStatus.APPROVED
        if rec.trust is TrustStatus.RESTRICTED and not ctx.policy.resolve.allow_restricted:
            return [reject("trust status = restricted")]
        if TRUST_RANK[rec.trust] < TRUST_RANK[floor]:
            return [reject(f"trust status = {rec.trust.value} (minimum: {floor.value})")]
        return [accept(rec.trust.value)]


class LicenseRule:
    id = "license"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        if rec.license.approval == "denied":
            return [reject(f"license {rec.license.expression} denied by policy")]
        if rec.license.approval == "unknown" and not ctx.policy.licenses.allow_unknown:
            return [reject("license unknown (policy requires a known license)")]
        if rec.license.approval == "unknown":
            return [accept("license unknown (allowed)")]
        return [accept(f"license {rec.license.expression} approved")]


class SecurityRule:
    id = "security"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        sec = rec.security
        if sec.critical_count > 0:
            return [reject(f"{sec.critical_count} critical security finding(s)")]
        if sec.status == "vulnerable":
            return [reject("known vulnerabilities")]
        if sec.findings_count > 0:
            return [
                RuleOutcome(
                    "penalty",
                    f"{sec.findings_count} non-critical finding(s)",
                    float(sec.findings_count),
                ),
                accept("no active security quarantine"),
            ]
        return [accept("no active security quarantine")]


class QualityRule:
    id = "quality"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        out: list[RuleOutcome] = []
        need = ctx.policy.resolve.require_quality
        if "tests" in need:
            out.append(
                accept("tests passed")
                if rec.quality.tests_status == "pass"
                else reject("tests have not passed")
            )
        if "evaluation" in need:
            ok = rec.quality.evaluation_score is not None
            out.append(
                accept("evaluation recorded") if ok else reject("no evaluation result recorded")
            )
        return out


def _check_range(value: str | None, spec: str | None, label: str) -> RuleOutcome | None:
    if not spec or value is None:
        return None
    try:
        req = VersionReq.parse(spec)
        ver = normalize_version(value)
    except RegistryError:
        return None
    if req.matches(ver):
        return accept(f"compatible with {label} {value}")
    return reject(f"requires {label} {spec} (have {value})")


class CompatibilityRule:
    id = "compatibility"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        c, env, out = rec.manifest.compatibility, ctx.env, []
        for outcome in (
            _check_range(env.ananke_version, c.ananke, "Ananke"),
            _check_range(env.python_version, c.python, "Python"),
            _check_range(env.mcp_protocol, c.mcp_protocol, "MCP protocol"),
        ):
            if outcome:
                out.append(outcome)
        if env.runtime:
            supported = set(rec.manifest.runtime.supported)
            if supported and env.runtime not in supported:
                out.append(
                    reject(
                        f"runtime {env.runtime} not supported (supports: {', '.join(sorted(supported))})"
                    )
                )
            else:
                out.append(
                    accept(
                        f"supports runtime {env.runtime}" if supported else "no runtime restriction"
                    )
                )
            rt = _check_range(
                env.runtime_versions.get(env.runtime), c.runtimes.get(env.runtime), env.runtime
            )
            if rt:
                out.append(rt)
        if c.operating_systems and env.operating_system not in c.operating_systems:
            out.append(reject(f"operating system {env.operating_system} not supported"))
        if c.architecture and env.architecture not in c.architecture:
            out.append(reject(f"architecture {env.architecture} not supported"))
        if env.model_capabilities is not None:
            wanted = set(c.model_capabilities) | {
                d.name or ""
                for d in rec.manifest.all_dependencies()
                if d.type is DependencyType.MODEL_CAPABILITY
            }
            wanted |= {k for k, v in rec.manifest.model_requirements.items() if v is True}
            missing = sorted(w for w in wanted if w and w not in env.model_capabilities)
            if missing:
                out.append(reject(f"model lacks required capability: {', '.join(missing)}"))
        return out


class EnterpriseApprovalRule:
    id = "enterprise-approval"

    def evaluate(self, rec: VersionRecord, ctx: RuleContext) -> list[RuleOutcome]:
        if ctx.policy.require_signature and not ctx.registry.is_signed(rec):
            return [reject("verified signature required by policy")]
        return []


def default_rules() -> list[ResolverRule]:
    return [
        LifecycleRule(),
        PrereleaseRule(),
        ChannelRule(),
        TrustRule(),
        LicenseRule(),
        SecurityRule(),
        QualityRule(),
        CompatibilityRule(),
        EnterpriseApprovalRule(),
    ]


def plugin_rules() -> list[ResolverRule]:
    """Rules contributed by installed packages (entry-point group ``ananke.registry.rules``)."""
    out: list[ResolverRule] = []
    for ep in importlib.metadata.entry_points().select(group="ananke.registry.rules"):
        try:
            obj = ep.load()
            out.append(obj() if isinstance(obj, type) else obj)
        except Exception:  # noqa: S112 - a broken plugin must not break resolution
            continue
    return out


# --------------------------------------------------------------------------- solver


@dataclass
class _Eval:
    accepted: bool
    reasons: list[Reason]
    adjust: float = 0.0


@dataclass
class _Cand:
    rec: VersionRecord
    ev: _Eval


class Resolver:
    def __init__(
        self,
        registry: Registry,
        *,
        env: ResolutionEnvironment | None = None,
        rules: list[ResolverRule] | None = None,
        options: ResolutionOptions | None = None,
        overrides: dict[str, VersionRecord] | None = None,
        load_plugins: bool = False,
        source_label: str = "local-registry",
    ) -> None:
        self.registry = registry
        self.source_label = source_label
        self.env = env or ResolutionEnvironment.detect()
        self.options = options or ResolutionOptions()
        base = rules if rules is not None else default_rules()
        self.rules: list[ResolverRule] = [*base, *(plugin_rules() if load_plugins else [])]
        self.overrides = overrides or {}
        policy = registry.policy
        self.policy = policy
        self.mode = self.options.mode or policy.resolve.mode
        self._eval_cache: dict[tuple[Any, ...], _Eval] = {}
        self._active_pins: dict[str, tuple[str, str]] = {}
        self._notes: dict[tuple[str, str], str] = {}
        self._steps = 0
        self._use_pins = self.options.pin_mode != "none" or self.mode is ResolutionMode.LOCKED

    # ---- public
    def resolve(
        self, ref: str | Requirement, *, kind: ArtifactKind | str | None = None
    ) -> ResolutionResult:
        return self.resolve_many([ref], kind=kind)

    def resolve_many(
        self, refs: list[str | Requirement], *, kind: ArtifactKind | str | None = None
    ) -> ResolutionResult:
        roots = [r if isinstance(r, Requirement) else Requirement.parse(r, kind) for r in refs]
        snapshot = self.registry.snapshot_id()
        strict = self.options.pin_mode == "strict" or self.mode is ResolutionMode.LOCKED
        pins_on = self._use_pins
        result = self._attempt(roots, snapshot, pins_on)
        if not result.ok and pins_on and not strict:
            retry = self._attempt(roots, snapshot, False)
            if retry.ok:
                retry.warnings.append("lockfile pins could not be preserved; re-resolved")
                retry.lock_preserved = False
                return retry
        if pins_on and result.ok:
            result.lock_preserved = True
        if result.ok:
            for node in result.nodes:
                if node.root:
                    rec = (
                        self.registry.exact_version(node.ref.ref)
                        if node.ref.uri not in self.overrides
                        else None
                    )
                    if rec is not None:
                        self.registry.usage("resolve", rec)
        return result

    # ---- internals
    def _attempt(self, roots: list[Requirement], snapshot: str, pins_on: bool) -> ResolutionResult:
        self._notes = {}
        self._steps = 0
        self._active_pins = self.options.pins if pins_on else {}
        result = ResolutionResult(mode=self.mode, snapshot=snapshot, environment=self.env)
        if self.mode is ResolutionMode.EXACT:
            for r in roots:
                if not VersionReq.parse(r.req).is_exact:
                    raise InvalidRequirementError(
                        f"mode 'exact' requires an exact version, got {r.req!r} for {r.name}"
                    )
        try:
            keyed = [(self._key(r), r) for r in roots]
        except NotFoundError as exc:
            for r in roots:
                result.decisions.append(
                    ResolutionDecision(requirement=f"{r.name}@{r.req}", failure=str(exc))
                )
            return result
        chosen = self._solve([r for _, r in keyed], {}, {})
        if chosen is None:
            for key, r in keyed:
                cands = self._root_decisions(key, r, None)
                failure = self._notes.get(
                    (key[1] + "/" + key[2], "root")
                ) or self._describe_failure(key, r, cands)
                result.decisions.append(
                    ResolutionDecision(
                        requirement=f"{key[1]}/{key[2]}@{r.req}", candidates=cands, failure=failure
                    )
                )
            return result
        nodes = self._build_nodes(chosen, keyed, result)
        result.nodes = nodes
        for key, r in keyed:
            rec_or_none = chosen.get(key)
            if rec_or_none is None:  # optional root that is not registered
                result.decisions.append(
                    ResolutionDecision(requirement=f"{key[1]}/{key[2]}@{r.req}")
                )
                continue
            rec = rec_or_none
            cands = self._root_decisions(key, r, rec)
            result.decisions.append(
                ResolutionDecision(
                    requirement=f"{key[1]}/{key[2]}@{r.req}",
                    selected=ArtifactVersionRef(
                        uri=rec.uri, version=rec.version, digest=rec.digest
                    ),
                    candidates=cands,
                )
            )
        self._add_environment_warnings(chosen, result)
        return result

    def _key(self, req: Requirement) -> Key:
        if self._is_override(req):
            kind, ns, name = self._override_key(req)
        else:
            ref = parse_ref(f"{req.namespace}/{req.name}" if req.namespace else req.name, req.kind)
            kind, ns, name = self.registry.locate(ref, req.kind)
        return (kind.value, ns, name)

    def _is_override(self, req: Requirement) -> bool:
        return any(self._matches_override(u, req) for u in self.overrides)

    def _matches_override(self, uri: str, req: Requirement) -> bool:
        ref = parse_ref(uri)
        return (
            ref.namespace == req.namespace
            and ref.name == req.name
            and (req.kind is None or req.kind == ref.kind)
        )

    def _override_key(self, req: Requirement) -> tuple[ArtifactKind, str, str]:
        for uri in self.overrides:
            if self._matches_override(uri, req):
                ref = parse_ref(uri)
                assert ref.kind is not None and ref.namespace is not None  # noqa: S101
                return ref.kind, ref.namespace, ref.name
        raise NotFoundError(req.name)  # pragma: no cover

    def _versions(self, key: Key) -> list[VersionRecord]:
        uri = artifact_uri(key[0], key[1], key[2])
        if uri in self.overrides:
            return [self.overrides[uri]]
        return self.registry.store.versions_of(key[0], key[1], key[2])

    def _ctx(self, rec: VersionRecord, req: Requirement, locked: bool) -> RuleContext:
        vr = VersionReq.parse(req.req)
        pol = self.policy.resolve
        min_trust = self.options.minimum_trust or pol.minimum_trust
        if (
            self.mode is ResolutionMode.HIGHEST_APPROVED
            and TRUST_RANK[min_trust] < TRUST_RANK[TrustStatus.APPROVED]
        ):
            min_trust = TrustStatus.APPROVED
        return RuleContext(
            registry=self.registry,
            policy=self.policy,
            env=self.env,
            mode=self.mode,
            requirement=req,
            version_req=vr,
            options=self.options,
            locked=locked,
            explicit_pin=vr.is_exact,
            allowed_channels=self.options.channels or pol.channel,
            min_trust=min_trust,
            allow_prerelease=(
                self.options.allow_prerelease
                if self.options.allow_prerelease is not None
                else pol.allow_prerelease
            ),
            allow_deprecated=(
                self.options.allow_deprecated
                if self.options.allow_deprecated is not None
                else pol.allow_deprecated
            ),
        )

    def _evaluate(self, rec: VersionRecord, req: Requirement, key: Key) -> _Eval:
        if self.overrides.get(rec.uri) is rec:  # explicit developer intent: skip policy rules
            return _Eval(True, [Reason(ok=True, text="path override (development)")])
        vr = VersionReq.parse(req.req)
        pin = self._active_pins.get(artifact_uri(*key))
        locked = pin is not None and pin[0] == rec.version
        sig = (rec.uri, rec.version, rec.revision, req.req, locked, self.mode.value)
        cached = self._eval_cache.get(sig)
        if cached is not None:
            return cached
        reasons: list[Reason] = []
        if vr.comparators and not vr.matches(rec.semver):
            ev = _Eval(False, [Reason(ok=False, text=f"does not match {vr.raw}")])
            self._eval_cache[sig] = ev
            return ev
        if vr.comparators:
            reasons.append(Reason(ok=True, text=f"matches {vr.raw}"))
        elif vr.symbol:
            reasons.append(Reason(ok=True, text=f"requirement {vr.symbol}"))
        accepted, adjust = True, 0.0
        if locked and pin is not None and pin[1] != rec.digest_sha256:
            accepted = False
            reasons.append(Reason(ok=False, text="payload digest differs from lockfile"))
        ctx = self._ctx(rec, req, locked)
        for rule in self.rules:
            for outcome in rule.evaluate(rec, ctx):
                if outcome.kind == "reject":
                    accepted = False
                    reasons.append(Reason(ok=False, text=outcome.reason))
                elif outcome.kind == "accept":
                    reasons.append(Reason(ok=True, text=outcome.reason))
                elif outcome.kind == "penalty":
                    adjust -= outcome.amount
                    reasons.append(Reason(ok=True, text=f"penalty: {outcome.reason}"))
                else:
                    adjust += outcome.amount
                    reasons.append(Reason(ok=True, text=f"preferred: {outcome.reason}"))
        ev = _Eval(accepted, reasons, adjust)
        self._eval_cache[sig] = ev
        return ev

    def _rank(self, cands: list[_Cand]) -> list[_Cand]:
        def other(c: _Cand) -> tuple[float, int, int]:
            chan = (
                3 if c.rec.channel in _STABLE_CHANNELS else 2 if c.rec.channel == "candidate" else 1
            )
            return (c.ev.adjust, TRUST_RANK[c.rec.trust], chan)

        if self.mode is ResolutionMode.LOWEST_COMPATIBLE:
            ordered = sorted(cands, key=lambda c: (c.rec.semver.sort_key, c.rec.version))
            return sorted(ordered, key=other, reverse=True)
        return sorted(
            cands,
            key=lambda c: (*other(c), c.rec.semver.sort_key, c.rec.version, c.rec.digest_sha256),
            reverse=True,
        )

    def _candidates(self, key: Key, constraints: list[Requirement]) -> list[_Cand]:
        versions = self._versions(key)
        pin = self._active_pins.get(artifact_uri(*key))
        if (
            self.mode is ResolutionMode.LOCKED
            and pin is None
            and artifact_uri(*key) not in self.overrides
        ):
            self._notes[(f"{key[1]}/{key[2]}", "root")] = (
                f"{artifact_uri(*key)} is not in the lockfile (run `ananke registry lock`)"
            )
            return []
        if pin is not None:
            versions = [v for v in versions if v.version == pin[0]] or (
                versions if self.options.pin_mode == "prefer" else []
            )
        good: list[_Cand] = []
        for rec in versions:
            evs = [self._evaluate(rec, c, key) for c in constraints]
            if all(e.accepted for e in evs):
                merged = _Eval(True, [], sum(e.adjust for e in evs))
                seen: set[str] = set()
                for e in evs:
                    for r in e.reasons:
                        if r.text not in seen:
                            seen.add(r.text)
                            merged.reasons.append(r)
                good.append(_Cand(rec, merged))
        if pin is not None and self.options.pin_mode == "prefer" and good:
            pinned = [c for c in good if c.rec.version == pin[0]]
            if pinned:
                return pinned
        return self._rank(good)

    def _solve(
        self,
        pending: list[Requirement],
        chosen: dict[Key, VersionRecord],
        constraints: dict[Key, list[Requirement]],
    ) -> dict[Key, VersionRecord] | None:
        self._steps += 1
        if self._steps > self.options.max_steps:
            raise ResolutionError(
                "dependency resolution exceeded the step limit (graph too complex)"
            )
        if not pending:
            return chosen
        req, rest = pending[0], pending[1:]
        try:
            key = self._key(req)
        except NotFoundError:
            self._notes[(f"{req.namespace}/{req.name}", "missing")] = (
                f"{req.namespace}/{req.name} is not registered"
            )
            if req.optional:
                return self._solve(rest, chosen, constraints)
            return None
        cons = {**constraints, key: [*constraints.get(key, []), req]}
        if key in chosen:
            vr_ok = all(
                VersionReq.parse(c.req).matches(chosen[key].semver)
                or not VersionReq.parse(c.req).comparators
                for c in cons[key]
            )
            if vr_ok:
                return self._solve(rest, chosen, cons)
            self._notes[(f"{key[1]}/{key[2]}", "conflict")] = (
                f"conflicting requirements for {key[1]}/{key[2]}: "
                + ", ".join(sorted({c.req for c in cons[key]}))
                + f" (already chose {chosen[key].version})"
            )
            return None
        cands = self._candidates(key, cons[key])
        if not cands:
            return self._solve(rest, chosen, cons) if req.optional else None
        for cand in cands:
            deps = [
                Requirement(
                    kind=d.artifact_ref.kind if d.artifact_ref else None,
                    namespace=d.artifact_ref.namespace if d.artifact_ref else None,
                    name=d.artifact_ref.name if d.artifact_ref else "",
                    req=d.version or "*",
                    optional=d.optional,
                    requested_by=f"{cand.rec.uri}@{cand.rec.version}",
                )
                for d in cand.rec.manifest.artifact_dependencies()
                if d.artifact_ref is not None
            ]
            outcome = self._solve([*deps, *rest], {**chosen, key: cand.rec}, cons)
            if outcome is not None:
                return outcome
            self._notes[(f"{cand.rec.uri}@{cand.rec.version}", "dep")] = (
                "a dependency could not be satisfied"
            )
        return None

    # ---- result building
    def _all_decisions(self, key: Key, req: Requirement) -> list[CandidateDecision]:
        out: list[CandidateDecision] = []
        for rec in self._versions(key):
            ev = self._evaluate(rec, req, key)
            matches = not any(r.text.startswith("does not match") for r in ev.reasons)
            out.append(
                CandidateDecision(
                    version=rec.version,
                    accepted=ev.accepted,
                    matches_requirement=matches,
                    reasons=ev.reasons,
                )
            )
        return out

    def _root_decisions(
        self, key: Key, req: Requirement, chosen: VersionRecord | None
    ) -> list[CandidateDecision]:
        decisions = self._all_decisions(key, req)
        ranked = self._rank(
            [
                _Cand(r, self._evaluate(r, req, key))
                for r in self._versions(key)
                if self._evaluate(r, req, key).accepted
            ]
        )
        order = {c.rec.version: i + 1 for i, c in enumerate(ranked)}
        for d in decisions:
            d.rank = order.get(d.version)
            d.selected = chosen is not None and d.version == chosen.version
            note = self._notes.get((f"{artifact_uri(*key)}@{d.version}", "dep"))
            if d.accepted and not d.selected and chosen is None and note:
                d.accepted = False
                d.reasons.append(Reason(ok=False, text=note))
        decisions.sort(key=lambda d: Version.parse(d.version).sort_key, reverse=True)
        return decisions

    def _describe_failure(self, key: Key, req: Requirement, cands: list[CandidateDecision]) -> str:
        for (_subject, kind), note in self._notes.items():
            if kind in {"conflict", "missing"}:
                return note
        if not cands:
            return f"no versions of {artifact_uri(*key)} are registered"
        matching = [c for c in cands if c.matches_requirement]
        if not matching:
            return f"no registered version matches {req.req}"
        if all(not c.accepted for c in matching):
            return "every matching version was rejected by policy"
        return "no combination of versions satisfies all dependency constraints"

    def _build_nodes(
        self,
        chosen: dict[Key, VersionRecord],
        roots: list[tuple[Key, Requirement]],
        result: ResolutionResult,
    ) -> list[ResolvedNode]:
        root_keys = {k for k, _ in roots}
        by_uri = {artifact_uri(*k): rec for k, rec in chosen.items()}
        requested_by: dict[str, set[str]] = {u: set() for u in by_uri}
        edges: dict[str, list[str]] = {}
        for uri, rec in by_uri.items():
            deps: list[str] = []
            for d in rec.manifest.artifact_dependencies():
                if d.id in by_uri:
                    deps.append(d.id)
                    requested_by[d.id].add(f"{uri}@{rec.version}")
            edges[uri] = sorted(set(deps))
        # topological order: dependencies before dependents
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(uri: str, trail: tuple[str, ...]) -> None:
            if state.get(uri) == 2:
                return
            if state.get(uri) == 1:
                raise ResolutionError(
                    "dependency cycle in resolved graph: " + " -> ".join((*trail, uri))
                )
            state[uri] = 1
            for dep in edges[uri]:
                visit(dep, (*trail, uri))
            state[uri] = 2
            order.append(uri)

        for uri in sorted(by_uri):
            visit(uri, ())
        nodes: list[ResolvedNode] = []
        for uri in order:
            rec = by_uri[uri]
            key = rec.key
            override = uri in self.overrides
            nodes.append(
                ResolvedNode(
                    ref=ArtifactVersionRef(uri=uri, version=rec.version, digest=rec.digest),
                    kind=rec.kind,
                    root=key in root_keys,
                    requested_by=sorted(requested_by[uri]),
                    dependencies=[
                        ArtifactVersionRef(
                            uri=d, version=by_uri[d].version, digest=by_uri[d].digest
                        )
                        for d in edges[uri]
                    ],
                    source="path" if override else self.source_label,
                    dirty=override,
                    override_path=rec.provenance.source_url if override else None,
                )
            )
        if any(n.dirty for n in nodes):
            result.warnings.append("path overrides in use (lockfile marks these dirty)")
        return nodes

    def _add_environment_warnings(
        self, chosen: dict[Key, VersionRecord], result: ResolutionResult
    ) -> None:
        for rec in chosen.values():
            for dep in rec.manifest.all_dependencies():
                if (
                    dep.type is DependencyType.SYSTEM_BINARY
                    and dep.name
                    and not shutil.which(dep.name)
                ):
                    result.warnings.append(
                        f"{rec.uri}: system binary {dep.name!r} not found on PATH"
                    )
                if dep.type is DependencyType.PYTHON_PACKAGE and dep.name:
                    bare = dep.name.split("[")[0]
                    try:
                        importlib.metadata.version(bare)
                    except importlib.metadata.PackageNotFoundError:
                        result.warnings.append(
                            f"{rec.uri}: Python package {dep.name!r} is not installed"
                        )


def resolve(
    registry: Registry,
    ref: str,
    *,
    kind: ArtifactKind | str | None = None,
    env: ResolutionEnvironment | None = None,
    options: ResolutionOptions | None = None,
) -> VersionRecord:
    """Resolve ``ref`` and return the selected record (raises ``ResolutionError``)."""
    result = Resolver(registry, env=env, options=options).resolve(ref, kind=kind)
    result.raise_if_failed()
    assert result.selected is not None  # noqa: S101
    return registry.exact_version(result.selected.ref)

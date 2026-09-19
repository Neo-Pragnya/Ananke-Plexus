"""Registry policy (spec §91, §100, §127): resolve, license, permission, approval, secrets."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.models import ResolutionMode, TrustStatus

POLICY_FILE = "policy.toml"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolvePolicy(_M):
    mode: ResolutionMode = ResolutionMode.HIGHEST_COMPATIBLE
    minimum_trust: TrustStatus = TrustStatus.DISCOVERED
    channel: list[str] = Field(default_factory=lambda: ["stable", "candidate", "approved"])
    allow_prerelease: bool = False
    allow_deprecated: bool = False
    allow_restricted: bool = False
    allow_yanked_when_locked: bool = True
    require_quality: list[Literal["tests", "evaluation"]] = Field(default_factory=list)


class LicensePolicy(_M):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    allow_unknown: bool = True


class NetworkPermissionPolicy(_M):
    require_review: bool = False


class PermissionPolicy(_M):
    network: NetworkPermissionPolicy = Field(default_factory=NetworkPermissionPolicy)
    forbid: list[str] = Field(default_factory=list)


class ApprovalPolicy(_M):
    minimum_trust: TrustStatus = TrustStatus.APPROVED
    require: list[
        Literal["provenance", "checksum", "tests", "vulnerability_scan", "license", "signature"]
    ] = Field(default_factory=list)
    run_tests: bool = False


class DynamicIntrospectionPolicy(_M):
    allowed: bool = False
    timeout_seconds: int = 20
    max_memory_mb: int = 512
    max_output_bytes: int = 5_000_000


class RemoteSourcesPolicy(_M):
    public: bool = False
    enterprise: bool = False
    allow_mcp_network: bool = False
    allow_git_remote: bool = False
    allow_cli_override: bool = True


class NamespacePolicy(_M):
    publishers: list[str] = Field(default_factory=list)


class SecretsPolicy(_M):
    mode: Literal["reject", "redact", "warn"] = "reject"


class SourceConfig(_M):
    type: Literal["local", "remote"] = "local"
    path: str | None = None
    url: str | None = None
    enabled: bool = True
    token_env: str | None = None  # NAME of an environment variable holding a bearer token


class TelemetryPolicy(_M):
    local_usage: bool = False


class OverridePolicy(_M):
    allow_in_release: bool = False


class TrustedKey(_M):
    """A public key the registry trusts to sign artifacts. Private keys never live here."""

    algorithm: Literal["ed25519"] = "ed25519"
    public_key: str  # base64 of the raw 32-byte key
    signer: str | None = None
    revoked: bool = False


class SigningPolicy(_M):
    trusted_keys: dict[str, TrustedKey] = Field(default_factory=dict)


class SemanticPolicy(_M):
    """Optional similarity search (spec §14). Off unless enabled; remote embedders need consent."""

    enabled: bool = False
    provider: str = "local-hash"
    allow_remote: bool = False


class RegistryPolicy(_M):
    resolve: ResolvePolicy = Field(default_factory=ResolvePolicy)
    licenses: LicensePolicy = Field(default_factory=LicensePolicy)
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    dynamic_introspection: DynamicIntrospectionPolicy = Field(
        default_factory=DynamicIntrospectionPolicy
    )
    remote_sources: RemoteSourcesPolicy = Field(default_factory=RemoteSourcesPolicy)
    signing: SigningPolicy = Field(default_factory=SigningPolicy)
    semantic: SemanticPolicy = Field(default_factory=SemanticPolicy)
    namespaces: dict[str, NamespacePolicy] = Field(default_factory=dict)
    secrets: SecretsPolicy = Field(default_factory=SecretsPolicy)
    telemetry: TelemetryPolicy = Field(default_factory=TelemetryPolicy)
    overrides: OverridePolicy = Field(default_factory=OverridePolicy)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    strict_semver: bool = False
    require_signature: bool = False
    default_namespace: str | None = "local"
    auto_register: bool = False
    activation_mode: Literal["copy", "link"] = "copy"

    @field_validator("default_namespace", mode="before")
    @classmethod
    def _empty_namespace_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    def dynamic_allowed(self, cli_override: bool = False) -> bool:
        if self.dynamic_introspection.allowed:
            return True
        return cli_override and self.remote_sources.allow_cli_override

    def remote_registry_allowed(self, name: str, cli_override: bool = False) -> bool:
        """Federation is pull-only network access, gated like every other network feature."""
        flag = {"public": self.remote_sources.public, "enterprise": self.remote_sources.enterprise}
        return flag.get(name, False) or (cli_override and self.remote_sources.allow_cli_override)

    def network_allowed(self, kind: Literal["mcp", "git"], cli_override: bool = False) -> bool:
        flag = (
            self.remote_sources.allow_mcp_network
            if kind == "mcp"
            else self.remote_sources.allow_git_remote
        )
        return flag or (cli_override and self.remote_sources.allow_cli_override)


def enterprise_policy() -> RegistryPolicy:
    """Recommended enterprise defaults: HighestApproved, approved-only, no unknown licenses."""
    return RegistryPolicy(
        resolve=ResolvePolicy(
            mode=ResolutionMode.HIGHEST_APPROVED,
            minimum_trust=TrustStatus.APPROVED,
            channel=["stable"],
        ),
        licenses=LicensePolicy(allow=["Apache-2.0", "MIT", "BSD-3-Clause"], allow_unknown=False),
        permissions=PermissionPolicy(network=NetworkPermissionPolicy(require_review=True)),
        approval=ApprovalPolicy(
            minimum_trust=TrustStatus.APPROVED,
            require=["provenance", "checksum", "tests", "vulnerability_scan", "license"],
        ),
        remote_sources=RemoteSourcesPolicy(allow_cli_override=False),
        strict_semver=True,
        default_namespace=None,
    )


PRESETS = {"default": RegistryPolicy, "enterprise": enterprise_policy}


def policy_path(registry_root: Path) -> Path:
    return registry_root / POLICY_FILE


def load_policy(registry_root: Path) -> RegistryPolicy:
    path = policy_path(registry_root)
    if not path.exists():
        return RegistryPolicy()
    try:
        if path.suffix == ".toml":
            data: Any = tomllib.loads(path.read_text(encoding="utf-8"))
        else:  # pragma: no cover - toml is canonical
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and "registry_policy" in data:
            data = data["registry_policy"]
        return RegistryPolicy.model_validate(data)
    except (ValidationError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(
            f"invalid registry policy {path}: {exc}", code="INVALID_POLICY"
        ) from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    import json

    return json.dumps(str(value), ensure_ascii=False)


def dump_policy_toml(policy: RegistryPolicy) -> str:
    data = policy.model_dump(mode="json", exclude_none=True)
    if policy.default_namespace is None:
        data["default_namespace"] = ""  # persist "no default namespace" explicitly
    lines: list[str] = [
        "# Ananke registry policy (spec §127). Edit and re-run `ananke registry doctor`."
    ]
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_value(v)}")

    def emit(prefix: str, table: dict[str, Any]) -> None:
        plain = {k: v for k, v in table.items() if not isinstance(v, dict)}
        nested = {k: v for k, v in table.items() if isinstance(v, dict)}
        if plain or not nested:
            lines.append("")
            lines.append(f"[{prefix}]")
            for k, v in plain.items():
                lines.append(f"{k} = {_toml_value(v)}")
        for k, v in nested.items():
            emit(f"{prefix}.{k}" if re.fullmatch(r"[A-Za-z0-9_-]+", k) else f'{prefix}."{k}"', v)

    for key, value in data.items():
        if isinstance(value, dict):
            emit(key, value)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- licenses

_SPDX_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+\-:]+")


def _spdx_eval(
    tokens: list[str], pos: int, allow: set[str], deny: set[str]
) -> tuple[bool, bool, int]:
    """Returns (satisfied_by_allow, hits_deny, next_pos) for an OR-of-ANDs expression."""

    def factor(i: int) -> tuple[bool, bool, int]:
        tok = tokens[i]
        if tok == "(":
            ok, bad, j = or_expr(i + 1)
            if j < len(tokens) and tokens[j] == ")":
                j += 1
            return ok, bad, j
        ident = tok.lower()
        j = i + 1
        if j + 1 < len(tokens) and tokens[j].upper() == "WITH":
            j += 2
        ok = (not allow) or ident in allow or ident.rstrip("+") in allow
        return ok, ident in deny or ident.rstrip("+") in deny, j

    def and_expr(i: int) -> tuple[bool, bool, int]:
        ok, bad, i = factor(i)
        while i < len(tokens) and tokens[i].upper() == "AND":
            ok2, bad2, i = factor(i + 1)
            ok, bad = ok and ok2, bad or bad2
        return ok, bad, i

    def or_expr(i: int) -> tuple[bool, bool, int]:
        ok, bad, i = and_expr(i)
        while i < len(tokens) and tokens[i].upper() == "OR":
            ok2, bad2, i = and_expr(i + 1)
            # OR: pick the acceptable branch
            if ok2 and not bad2:
                ok, bad = True, False
            elif not (ok and not bad):
                ok, bad = ok2, bad2
        return ok, bad, i

    return or_expr(pos)


def evaluate_license(expression: str | None, policy: LicensePolicy) -> tuple[str, str]:
    """Return ``(approval, reason)`` where approval is approved|denied|unknown."""
    if not expression or not expression.strip():
        return "unknown", "no license metadata"
    tokens = _SPDX_TOKEN.findall(expression)
    if not tokens:
        return "unknown", f"unparseable license expression {expression!r}"
    allow = {a.lower() for a in policy.allow}
    deny = {d.lower() for d in policy.deny}
    ok, bad, _ = _spdx_eval(tokens, 0, allow, deny)
    if bad:
        return "denied", f"license {expression} is on the deny list"
    if not ok:
        return "denied", f"license {expression} is not on the allow list"
    return "approved", f"license {expression} accepted"

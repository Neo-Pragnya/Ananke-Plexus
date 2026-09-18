"""ProfileConfig, ProfileKinds, BUILTIN_PROFILES dict."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field


class ProfileKinds:
    FAST: ClassVar[list[str]] = ["unit", "property", "snapshot"]
    STANDARD: ClassVar[list[str]] = [
        "unit",
        "property",
        "snapshot",
        "integration",
        "bdd",
        "acceptance",
        "contract",
        "coverage",
        "api_schema",
    ]
    STRICT: ClassVar[list[str]] = [*STANDARD, "mutation", "fuzz", "security", "stateful"]
    VERIFICATION: ClassVar[list[str]] = [*STRICT, "concurrency", "formal"]
    RELEASE: ClassVar[list[str]] = [*VERIFICATION, "performance", "agent_eval"]


class ProfileConfig(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    extends: str | None = None
    max_seconds: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {"kinds": ["unit", "property", "snapshot"], "max_seconds": 30},
    "standard": {
        "extends": "fast",
        "kinds": ["integration", "bdd", "acceptance", "contract", "coverage", "api_schema"],
    },
    "strict": {"extends": "standard", "kinds": ["mutation", "fuzz", "security", "stateful"]},
    "verification": {"extends": "strict", "kinds": ["concurrency", "formal"]},
    "release": {"extends": "verification", "kinds": ["performance", "agent_eval"]},
}


def resolve_profile_kinds(profile_name: str) -> list[str]:
    """Return the full (transitive) list of test kinds for a profile."""
    profile = BUILTIN_PROFILES.get(profile_name)
    if profile is None:
        return list(ProfileKinds.STANDARD)
    base_kinds: list[str] = []
    parent = profile.get("extends")
    if parent:
        base_kinds = resolve_profile_kinds(parent)
    own_kinds: list[str] = profile.get("kinds", [])
    seen: dict[str, None] = {}
    for k in base_kinds + own_kinds:
        seen[k] = None
    return list(seen)

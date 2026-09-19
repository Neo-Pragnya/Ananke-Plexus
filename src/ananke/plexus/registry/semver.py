"""SemVer 2.0 versions and requirement ranges (spec §17, §49).

Ranges follow the familiar Cargo/npm operators: ``^``, ``~``, ``>=``, ``<`` … A bare
full version (``2.1.3``) means an *exact* pin; a bare partial version (``1``, ``1.2``)
means the wildcard range for that prefix. Upper bounds derived from ``^``/``~``/``<`` use
the lowest possible pre-release (``2.0.0-0``) so ``^1`` never admits ``2.0.0-rc.1``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property

from ananke.plexus.registry.errors import InvalidRequirementError, InvalidVersionError

_IDENT = r"(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
_SEMVER_RE = re.compile(
    rf"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    rf"(?:-({_IDENT}(?:\.{_IDENT})*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_LENIENT_RE = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?(?P<rest>(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>alpha|a|beta|b|preview|pre|rc|c)[-_.]?(?P<pren>\d+)?)?"
    r"(?:[-_.]?(?:post|rev|r)[-_.]?(?P<post>\d+)?)?"
    r"(?:[-_.]?dev[-_.]?(?P<dev>\d+)?)?"
    r"(?:\+(?P<build>[0-9A-Za-z.\-]+))?$",
    re.IGNORECASE,
)
_PRE_LABELS = {
    "a": "alpha",
    "alpha": "alpha",
    "b": "beta",
    "beta": "beta",
    "rc": "rc",
    "c": "rc",
    "pre": "rc",
    "preview": "rc",
}

PreId = int | str


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[PreId, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _SEMVER_RE.match(text.strip())
        if match is None:
            raise InvalidVersionError(f"not a valid SemVer version: {text!r}")
        major, minor, patch, pre, build = match.groups()
        pre_ids: tuple[PreId, ...] = ()
        if pre:
            pre_ids = tuple(int(p) if p.isdigit() else p for p in pre.split("."))
        return cls(
            int(major), int(minor), int(patch), pre_ids, tuple(build.split(".")) if build else ()
        )

    def __str__(self) -> str:
        out = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            out += "-" + ".".join(str(p) for p in self.pre)
        if self.build:
            out += "+" + ".".join(self.build)
        return out

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    @cached_property
    def precedence_key(self) -> tuple[object, ...]:
        """SemVer §11 precedence — build metadata is ignored."""
        pre_key: tuple[tuple[int, int | str], ...] = tuple(
            (0, p) if isinstance(p, int) else (1, p) for p in self.pre
        )
        # A release sorts after every pre-release of the same triple.
        return (self.major, self.minor, self.patch, 1 if not self.pre else 0, pre_key)

    @cached_property
    def sort_key(self) -> tuple[object, ...]:
        """Total order: precedence, then build metadata (deterministic tie-break)."""
        return (*self.precedence_key, self.build)

    def __lt__(self, other: Version) -> bool:
        return self.sort_key < other.sort_key

    def __le__(self, other: Version) -> bool:
        return self.sort_key <= other.sort_key

    def __gt__(self, other: Version) -> bool:
        return self.sort_key > other.sort_key

    def __ge__(self, other: Version) -> bool:
        return self.sort_key >= other.sort_key

    def same_precedence(self, other: Version) -> bool:
        return self.precedence_key == other.precedence_key

    def bump_major(self) -> Version:
        return Version(self.major + 1, 0, 0)

    def bump_minor(self) -> Version:
        return Version(self.major, self.minor + 1, 0)

    def bump_patch(self) -> Version:
        return Version(self.major, self.minor, self.patch + 1)


def normalize_version(native: str, *, strict: bool = False) -> Version:
    """Map a foreign/native version string onto SemVer (spec §17).

    ``strict=True`` accepts only real SemVer. Otherwise PEP 440-style strings such as
    ``1.2``, ``1.2.3rc1`` or ``2.0.dev3`` are mapped best-effort.
    """
    try:
        return Version.parse(native)
    except InvalidVersionError:
        if strict:
            raise
    match = _LENIENT_RE.match(native.strip())
    if match is None:
        raise InvalidVersionError(f"cannot normalize version: {native!r}")
    pre: list[PreId] = []
    if match["pre"]:
        pre.append(_PRE_LABELS[match["pre"].lower()])
        if match["pren"]:
            pre.append(int(match["pren"]))
    if native and re.search(r"dev", native, re.IGNORECASE):
        pre.extend(["dev", int(match["dev"]) if match["dev"] else 0])
    build: list[str] = []
    if match["rest"]:
        build.append("x" + match["rest"].replace(".", "-").strip("-"))
    if re.search(r"(post|rev)", native, re.IGNORECASE) or (match["post"] is not None):
        build.append("post" + (f".{match['post']}" if match["post"] else ""))
    if match["build"]:
        build.extend(match["build"].split("."))
    return Version(
        int(match["major"]),
        int(match["minor"] or 0),
        int(match["patch"] or 0),
        tuple(pre),
        tuple(build),
    )


@dataclass(frozen=True)
class Comparator:
    op: str
    version: Version

    def matches(self, candidate: Version) -> bool:
        a, b = candidate.precedence_key, self.version.precedence_key
        if self.op == "=":
            return a == b
        if self.op == "!=":
            return a != b
        if self.op == ">=":
            return a >= b
        if self.op == ">":
            return a > b
        if self.op == "<=":
            return a <= b
        if self.op == "<":
            return a < b
        raise InvalidRequirementError(f"unknown operator {self.op!r}")  # pragma: no cover

    def __str__(self) -> str:
        return f"{self.op}{self.version}"


_LOWEST_PRE: tuple[PreId, ...] = (0,)
_SYMBOLS = frozenset({"latest", "stable", "approved"})
_PART_RE = re.compile(r"^(?P<op>\^|~|>=|<=|==|!=|>|<|=)?\s*(?P<ver>[0-9xX*][0-9A-Za-z.\-+*]*)$")


@dataclass(frozen=True)
class _Partial:
    major: int
    minor: int | None
    patch: int | None
    pre: tuple[PreId, ...]

    @property
    def is_full(self) -> bool:
        return self.minor is not None and self.patch is not None


def _parse_partial(text: str) -> _Partial:
    body, _, _build = text.partition("+")
    core, _, pre = body.partition("-")
    segments = core.split(".")
    if not 1 <= len(segments) <= 3:
        raise InvalidRequirementError(f"invalid version in requirement: {text!r}")
    nums: list[int | None] = []
    wildcard = False
    for seg in segments:
        if seg in {"x", "X", "*"}:
            wildcard = True
            nums.append(None)
        elif wildcard or not seg.isdigit():
            raise InvalidRequirementError(f"invalid version in requirement: {text!r}")
        else:
            nums.append(int(seg))
    while len(nums) < 3:
        nums.append(None)
    if nums[0] is None:
        raise InvalidRequirementError("major version required (use '*' for any)")
    pre_ids: tuple[PreId, ...] = ()
    if pre:
        pre_ids = tuple(int(p) if p.isdigit() else p for p in pre.split("."))
    return _Partial(nums[0], nums[1], nums[2], pre_ids)


def _floor(p: _Partial) -> Version:
    return Version(p.major, p.minor or 0, p.patch or 0, p.pre)


def _ceil_pre(major: int, minor: int, patch: int) -> Version:
    return Version(major, minor, patch, _LOWEST_PRE)


def _expand(op: str, raw: str, *, bare_exact: bool = True) -> list[Comparator]:
    p = _parse_partial(raw)
    if op == "":
        op = "=" if (p.is_full and bare_exact) else "~w"
    if op == "==":
        op = "="
    if op == "^":
        lo = _floor(p)
        if p.major > 0 or p.minor is None:
            hi = _ceil_pre(p.major + 1, 0, 0)
        elif p.minor > 0 or p.patch is None:
            hi = _ceil_pre(0, p.minor + 1, 0)
        else:
            hi = _ceil_pre(0, 0, (p.patch or 0) + 1)
        return [Comparator(">=", lo), Comparator("<", hi)]
    if op == "~":
        lo = _floor(p)
        hi = _ceil_pre(p.major + 1, 0, 0) if p.minor is None else _ceil_pre(p.major, p.minor + 1, 0)
        return [Comparator(">=", lo), Comparator("<", hi)]
    if op in {"~w", "="} and not p.is_full:
        lo = _floor(p)
        hi = _ceil_pre(p.major + 1, 0, 0) if p.minor is None else _ceil_pre(p.major, p.minor + 1, 0)
        if p.minor is not None and p.patch is None:
            hi = _ceil_pre(p.major, p.minor + 1, 0)
        return [Comparator(">=", lo), Comparator("<", hi)]
    if op == "=":
        return [Comparator("=", _floor(p))]
    if op == "!=":
        if not p.is_full:
            raise InvalidRequirementError("'!=' requires a full version")
        return [Comparator("!=", _floor(p))]
    if op == ">=":
        return [Comparator(">=", _floor(p))]
    if op == ">":
        if p.is_full:
            return [Comparator(">", _floor(p))]
        if p.minor is None:
            return [Comparator(">=", _ceil_pre(p.major + 1, 0, 0))]
        return [Comparator(">=", _ceil_pre(p.major, p.minor + 1, 0))]
    if op == "<":
        return [Comparator("<", Version(p.major, p.minor or 0, p.patch or 0, _LOWEST_PRE))]
    if op == "<=":
        if p.is_full:
            return [Comparator("<=", _floor(p))]
        if p.minor is None:
            return [Comparator("<", _ceil_pre(p.major + 1, 0, 0))]
        return [Comparator("<", _ceil_pre(p.major, p.minor + 1, 0))]
    raise InvalidRequirementError(f"unsupported operator {op!r}")  # pragma: no cover


@dataclass(frozen=True)
class VersionReq:
    raw: str
    comparators: tuple[Comparator, ...] = ()
    symbol: str | None = None

    @classmethod
    def parse(cls, text: str | None) -> VersionReq:
        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"", "*"}:
            return cls(raw or "*", (), "latest")
        if lowered in _SYMBOLS:
            return cls(lowered, (), lowered)
        comparators: list[Comparator] = []
        for chunk in raw.split(","):
            for part in re.split(r"\s+(?=[<>=!^~])", chunk.strip()):
                part = part.strip()
                if not part:
                    raise InvalidRequirementError(f"empty comparator in {raw!r}")
                match = _PART_RE.match(part)
                if match is None:
                    raise InvalidRequirementError(f"invalid version requirement: {raw!r}")
                comparators.extend(_expand(match["op"] or "", match["ver"]))
        return cls(raw, tuple(comparators))

    def matches(self, version: Version) -> bool:
        return all(c.matches(version) for c in self.comparators)

    @property
    def exact_version(self) -> Version | None:
        if len(self.comparators) == 1 and self.comparators[0].op == "=":
            return self.comparators[0].version
        return None

    @property
    def is_exact(self) -> bool:
        return self.exact_version is not None

    def allows_prerelease(self) -> bool:
        return any(c.version.pre and c.version.pre != _LOWEST_PRE for c in self.comparators)

    def __str__(self) -> str:
        return self.raw


def sort_versions(versions: list[Version], *, descending: bool = True) -> list[Version]:
    return sorted(versions, key=lambda v: v.sort_key, reverse=descending)


def max_satisfying(versions: list[Version], req: VersionReq) -> Version | None:
    ok = [v for v in versions if req.matches(v)]
    return max(ok, key=lambda v: v.sort_key) if ok else None


def bump_kind(old: Version, new: Version) -> str:
    """Return which component increased: ``major|minor|patch|pre|none|downgrade``."""
    if new.precedence_key == old.precedence_key:
        return "none"
    if new.precedence_key < old.precedence_key:
        return "downgrade"
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    if new.patch != old.patch:
        return "patch"
    return "pre"

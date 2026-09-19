"""SemVer versions and requirement ranges (spec §17, §49)."""

from __future__ import annotations

import random

import pytest

from ananke.plexus.registry.errors import InvalidRequirementError, InvalidVersionError
from ananke.plexus.registry.semver import (
    Version,
    VersionReq,
    bump_kind,
    max_satisfying,
    normalize_version,
    sort_versions,
)


def V(text: str) -> Version:
    return Version.parse(text)


def matches(req: str, version: str) -> bool:
    return VersionReq.parse(req).matches(V(version))


class TestVersionParsing:
    @pytest.mark.parametrize(
        "text",
        ["0.0.0", "1.2.3", "10.20.30", "1.0.0-alpha.1", "1.0.0+build.5", "1.0.0-rc.1+sha.abc"],
    )
    def test_valid(self, text: str) -> None:
        assert str(V(text)) == text

    @pytest.mark.parametrize(
        "text", ["1", "1.2", "01.2.3", "1.2.3.4", "v1.2.3", "1.2.3-", "", "a.b.c"]
    )
    def test_invalid(self, text: str) -> None:
        with pytest.raises(InvalidVersionError):
            V(text)

    def test_precedence_chain_from_spec(self) -> None:
        chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        versions = [V(c) for c in chain]
        assert sort_versions(list(reversed(versions)), descending=False) == versions

    def test_build_metadata_ignored_for_precedence_but_total_order(self) -> None:
        a, b = V("1.0.0+a"), V("1.0.0+b")
        assert a.same_precedence(b)
        assert a < b  # deterministic tie-break

    def test_bumps(self) -> None:
        assert str(V("1.2.3").bump_major()) == "2.0.0"
        assert str(V("1.2.3").bump_minor()) == "1.3.0"
        assert str(V("1.2.3").bump_patch()) == "1.2.4"

    def test_bump_kind(self) -> None:
        assert bump_kind(V("1.0.0"), V("2.0.0")) == "major"
        assert bump_kind(V("1.0.0"), V("1.1.0")) == "minor"
        assert bump_kind(V("1.0.0"), V("1.0.1")) == "patch"
        assert bump_kind(V("1.0.0"), V("1.0.0")) == "none"
        assert bump_kind(V("2.0.0"), V("1.0.0")) == "downgrade"


class TestNormalization:
    @pytest.mark.parametrize(
        ("native", "expected"),
        [
            ("1.2", "1.2.0"),
            ("3", "3.0.0"),
            ("v2.1.0", "2.1.0"),
            ("1.2.3rc1", "1.2.3-rc.1"),
            ("1.0a2", "1.0.0-alpha.2"),
            ("2.0.dev3", "2.0.0-dev.3"),
            ("1.0.post2", "1.0.0+post.2"),
        ],
    )
    def test_lenient(self, native: str, expected: str) -> None:
        assert str(normalize_version(native)) == expected

    def test_strict_rejects_foreign(self) -> None:
        with pytest.raises(InvalidVersionError):
            normalize_version("1.2", strict=True)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(InvalidVersionError):
            normalize_version("not-a-version")


class TestRequirements:
    def test_caret(self) -> None:
        assert matches("^1.4", "1.4.0") and matches("^1.4", "1.99.0")
        assert not matches("^1.4", "1.3.9") and not matches("^1.4", "2.0.0")

    def test_caret_zero_major(self) -> None:
        assert matches("^0.2.3", "0.2.9") and not matches("^0.2.3", "0.3.0")
        assert matches("^0.0.3", "0.0.3") and not matches("^0.0.3", "0.0.4")

    def test_caret_excludes_next_major_prerelease(self) -> None:
        assert not matches("^1", "2.0.0-rc.1")

    def test_tilde(self) -> None:
        assert matches("~2.3", "2.3.9") and not matches("~2.3", "2.4.0")
        assert matches("~2", "2.9.0") and not matches("~2", "3.0.0")

    def test_comparison_range(self) -> None:
        assert matches(">=1.5,<2", "1.9.9")
        assert not matches(">=1.5,<2", "2.0.0") and not matches(">=1.5,<2", "1.4.9")
        assert not matches(">=1.5,<2", "2.0.0-beta.1")

    def test_space_separated_range(self) -> None:
        assert matches(">=1.5 <2", "1.6.0") and not matches(">=1.5 <2", "2.1.0")

    def test_exact_forms(self) -> None:
        assert matches("=1.2.3", "1.2.3") and not matches("=1.2.3", "1.2.4")
        assert matches("1.2.3", "1.2.3") and not matches("1.2.3", "1.2.4")
        assert VersionReq.parse("=1.2.3").is_exact and VersionReq.parse("2.1.3").is_exact

    def test_partial_bare_is_wildcard(self) -> None:
        assert matches("1", "1.9.0") and not matches("1", "2.0.0")
        assert matches("1.2", "1.2.9") and not matches("1.2", "1.3.0")
        assert matches("1.x", "1.4.0") and matches("1.2.*", "1.2.7")

    def test_star_and_symbols(self) -> None:
        assert matches("*", "9.9.9")
        for sym in ("latest", "stable", "approved"):
            r = VersionReq.parse(sym)
            assert r.symbol == sym and r.matches(V("3.0.0"))

    def test_not_equal_and_strict_greater(self) -> None:
        assert matches("!=1.2.3", "1.2.4") and not matches("!=1.2.3", "1.2.3")
        assert matches(">1.5", "1.6.0") and not matches(">1.5", "1.5.9")
        assert matches("<=1.5", "1.5.9") and not matches("<=1.5", "1.6.0")

    def test_allows_prerelease_only_when_stated(self) -> None:
        assert VersionReq.parse(">=1.0.0-beta.1").allows_prerelease()
        assert not VersionReq.parse("^1.0").allows_prerelease()

    @pytest.mark.parametrize("bad", [">>1", "^", "1..2", "abc", "^x.1", ">=1.5,", "1.2.3.4"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(InvalidRequirementError):
            VersionReq.parse(bad)

    def test_max_satisfying(self) -> None:
        versions = [V(v) for v in ("1.0.0", "1.4.2", "1.9.0", "2.0.0", "2.1.0-beta.1")]
        assert str(max_satisfying(versions, VersionReq.parse("^1"))) == "1.9.0"
        assert max_satisfying(versions, VersionReq.parse("^3")) is None


def test_caret_property_random_versions() -> None:
    """^X.Y.Z admits exactly versions >= X.Y.Z that share the leftmost non-zero component."""
    rng = random.Random(7)
    for _ in range(300):
        base = V(f"{rng.randint(0, 3)}.{rng.randint(0, 3)}.{rng.randint(0, 3)}")
        cand = V(f"{rng.randint(0, 4)}.{rng.randint(0, 4)}.{rng.randint(0, 4)}")
        got = VersionReq.parse(f"^{base}").matches(cand)
        if base.major > 0:
            expect = cand >= base and cand.major == base.major
        elif base.minor > 0:
            expect = cand >= base and cand.major == 0 and cand.minor == base.minor
        else:
            expect = cand == base
        assert got == expect, (base, cand)

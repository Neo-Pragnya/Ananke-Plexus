"""compile_quality_obligations — reads quality: section from spec YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ananke.plexus.testing.models.suite import QualityProfile, QualitySuite
from ananke.plexus.testing.models.test import TestDefinition, TestKind


def compile_quality_obligations(
    spec_path: Path,
    suite_id: str | None = None,
) -> QualitySuite:
    """Read a spec YAML file and compile quality obligations into a QualitySuite.

    The spec YAML may contain a ``quality:`` top-level key with a list of test
    obligations or a profile reference.  If no quality section is found, an
    empty suite is returned.
    """
    try:
        import yaml
    except ImportError:
        return QualitySuite(id=suite_id or spec_path.stem)

    try:
        raw = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return QualitySuite(id=suite_id or spec_path.stem)

    quality_section: Any = raw.get("quality", {})
    if not quality_section:
        return QualitySuite(id=suite_id or spec_path.stem)

    profile_name: str = quality_section.get("profile", "standard")
    try:
        profile = QualityProfile(profile_name)
    except ValueError:
        profile = QualityProfile.STANDARD

    raw_tests: list[dict[str, Any]] = quality_section.get("tests", [])
    tests: list[TestDefinition] = []
    for raw_test in raw_tests:
        try:
            kind_str = raw_test.get("kind", "unit")
            kind = TestKind(kind_str)
        except ValueError:
            kind = TestKind.UNIT
        tests.append(
            TestDefinition(
                id=raw_test.get("id", f"obligation-{len(tests)}"),
                kind=kind,
                engine=raw_test.get("engine", "pytest"),
                description=raw_test.get("description"),
                tags=raw_test.get("tags", []),
                required=raw_test.get("required", True),
                config=raw_test.get("config", {}),
            )
        )

    return QualitySuite(
        id=suite_id or spec_path.stem,
        version=str(raw.get("version", "1")),
        profile=profile,
        tests=tests,
    )

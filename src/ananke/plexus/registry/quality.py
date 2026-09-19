"""Registry quality gate before trust promotion (spec §89, §141, §142, §143).

manifest → checksum → provenance → schemas → secrets → dependencies → runtime translation
→ license → permissions → tests → vulnerability scan → signature. Policy decides which
checks are *required*; failed required checks block promotion.

Running an artifact's own tests executes untrusted code, so it only happens when
explicitly requested (``run_tests=True`` / ``--run-tests``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ananke.plexus.registry.errors import RegistryError
from ananke.plexus.registry.hashing import canonical_json
from ananke.plexus.registry.jsonschema_util import check_json_schema
from ananke.plexus.registry.models import (
    ArtifactKind,
    LifecycleStatus,
    Quality,
    TrustStatus,
    VersionRecord,
)
from ananke.plexus.registry.payload import MANIFEST_FILE, extract_to, unpack_files
from ananke.plexus.registry.policy import evaluate_license
from ananke.plexus.registry.secrets import scan_files
from ananke.plexus.registry.semver import VersionReq
from ananke.plexus.registry.translate import check_translations

if TYPE_CHECKING:
    from ananke.plexus.registry.registry import Registry


class GateCheck(BaseModel):
    name: str
    ok: bool
    required: bool = True
    detail: str = ""


class GateReport(BaseModel):
    uri: str
    checks: list[GateCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.required)


class QualityGate:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def run(
        self,
        rec: VersionRecord,
        *,
        run_tests: bool | None = None,
        target: TrustStatus = TrustStatus.APPROVED,
        reviewed_by: str | None = None,
    ) -> GateReport:
        reg = self.registry
        policy = reg.policy
        require = set(policy.approval.require)
        checks: list[GateCheck] = []

        def add(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
            checks.append(GateCheck(name=name, ok=ok, detail=detail, required=required))

        if rec.lifecycle is LifecycleStatus.QUARANTINED:
            add("lifecycle", False, "quarantined versions cannot be promoted")
            return GateReport(uri=rec.version_uri, checks=checks)

        # 1. checksum + manifest integrity
        files: dict[str, bytes] = {}
        try:
            payload = reg.cas.get(rec.digest_sha256)
            files = unpack_files(payload)
            add("checksum", True, f"sha256:{rec.digest_sha256[:12]}… verified")
        except RegistryError as exc:
            add("checksum", False, str(exc))
        embedded = files.get(MANIFEST_FILE)
        if embedded is not None:
            same = embedded.rstrip(b"\n") == canonical_json(rec.manifest.canonical_dict())
            add(
                "manifest",
                same,
                "payload manifest matches registry record" if same else "payload manifest differs",
            )
        else:
            add("manifest", False, "payload has no ananke.registry.json")

        # 2. provenance
        has_prov = bool(
            rec.provenance.source_type and (rec.provenance.importer or rec.provenance.source_name)
        )
        add(
            "provenance",
            has_prov,
            rec.provenance.source_type if has_prov else "no provenance recorded",
            "provenance" in require,
        )

        # 3. schemas
        bad = [
            f"{role}: {err}"
            for role, sch in reg.store.schemas_of(
                reg.store.version_id(rec.kind, rec.namespace, rec.name, rec.version)
            ).items()
            if (err := check_json_schema(sch))
        ]
        add("schemas", not bad, "; ".join(bad) or "all schemas valid")

        # 4. secrets
        findings = scan_files(files)
        add(
            "secrets",
            not findings,
            f"{len(findings)} potential secret(s)" if findings else "no secrets detected",
        )

        # 5. dependencies
        problems: list[str] = []
        for dep in rec.manifest.artifact_dependencies():
            ref = dep.artifact_ref
            assert ref is not None and ref.kind is not None and ref.namespace is not None  # noqa: S101
            versions = reg.store.versions_of(ref.kind, ref.namespace, ref.name)
            req = VersionReq.parse(dep.version)
            if not any(req.matches(v.semver) for v in versions):
                problems.append(f"{ref.namespace}/{ref.name}@{dep.version or '*'} not registered")
        add(
            "dependencies",
            not problems,
            "; ".join(problems) or "all artifact dependencies are registered",
        )

        # 6. runtime translation
        failed = [
            f"{rt}: {err}"
            for rt, err in check_translations(rec)
            if err and "custom runtime" not in err
        ]
        add(
            "runtime-translation",
            not failed,
            "; ".join(failed) or "translates for every declared runtime",
        )

        # 7. license
        approval, why = evaluate_license(rec.license.expression, policy.licenses)
        must_license = "license" in require
        add(
            "license",
            approval == "approved"
            or (approval == "unknown" and policy.licenses.allow_unknown and not must_license),
            why,
        )

        # 8. permissions review
        has_net = bool(rec.manifest.permissions.network)
        needs_review = (policy.permissions.network.require_review) and has_net
        add(
            "permission-review",
            not needs_review or bool(reviewed_by),
            "network permission requires human review (--reviewed-by)"
            if needs_review and not reviewed_by
            else (f"reviewed by {reviewed_by}" if needs_review else "no review required"),
        )

        # 9. tests
        want_run = policy.approval.run_tests if run_tests is None else run_tests
        tests_status: str = rec.quality.tests_status
        if want_run:
            tests_status, detail = self._run_tests(rec, files)
            reg.update_quality(
                rec.version_uri,
                rec.quality.model_copy(update={"tests_status": tests_status}),
                "quality gate test run",
            )
        else:
            detail = f"recorded status: {tests_status}"
        add(
            "tests",
            tests_status == "pass"
            or ("tests" not in require and tests_status in {"none", "unknown"}),
            detail,
            "tests" in require,
        )

        # 10. vulnerability scan + critical findings
        sec = rec.security
        add(
            "security",
            sec.critical_count == 0 and sec.status != "vulnerable",
            f"status={sec.status}, critical={sec.critical_count}",
        )
        add(
            "vulnerability_scan",
            sec.last_scanned_at is not None and sec.status in {"clean", "findings"},
            "scanned" if sec.last_scanned_at else "no scan recorded",
            "vulnerability_scan" in require,
        )

        # 11. signature
        signed = self.registry.is_signed(rec)
        add(
            "signature",
            signed,
            "verified" if signed else "not signed/verified",
            "signature" in require or policy.require_signature,
        )
        return GateReport(uri=rec.version_uri, checks=checks)

    def _run_tests(self, rec: VersionRecord, files: dict[str, bytes]) -> tuple[str, str]:
        if not any(p.startswith("tests/") or p.startswith("test_") for p in files):
            return "none", "artifact ships no tests"
        try:
            from ananke.plexus.testing.api import run_quality_suite
            from ananke.plexus.testing.models.test import TestStatus
        except ImportError as exc:  # pragma: no cover
            return "unknown", f"testing harness unavailable: {exc}"
        with tempfile.TemporaryDirectory(prefix="ananke-reg-tests-") as tmp:
            root = Path(tmp)
            extract_to(self.registry.payload(rec), root)
            run = run_quality_suite(project_root=root, profile="fast", save_evidence=False)
        results = run.results
        if not results:
            return "none", "no tests discovered"
        failed = [r for r in results if r.status in {TestStatus.FAIL, TestStatus.ERROR}]
        passed = [r for r in results if r.status is TestStatus.PASS]
        if failed:
            return "fail", f"{len(failed)} failing test group(s)"
        if passed:
            return "pass", f"{len(passed)} test group(s) passed"
        return "unknown", "tests could not be executed (adapters unavailable)"


def quality_from_gate(report: GateReport, quality: Quality) -> Quality:
    tests = next((c for c in report.checks if c.name == "tests"), None)
    if tests is None:
        return quality
    return quality


def badges(rec: VersionRecord, signed: bool = False) -> list[str]:
    """Factual quality badges for docs (spec §143) — never popularity scores."""
    out: list[str] = []
    if rec.trust is TrustStatus.APPROVED:
        out.append("APPROVED")
    elif rec.trust is TrustStatus.VERIFIED:
        out.append("VERIFIED")
    elif rec.trust is TrustStatus.QUARANTINED:
        out.append("QUARANTINED")
    if rec.channel == "stable":
        out.append("STABLE")
    if rec.quality.tests_status == "pass":
        out.append("TESTED")
    if signed:
        out.append("SIGNED")
    if not rec.manifest.permissions.network:
        out.append("NO NETWORK")
    for rt in rec.manifest.runtime.supported:
        out.append(f"RUNTIME: {rt.upper()}")
    if rec.license.expression:
        out.append(f"LICENSE: {rec.license.expression.upper()}")
    if rec.lifecycle is LifecycleStatus.DEPRECATED:
        out.append("DEPRECATED")
    if rec.lifecycle is LifecycleStatus.YANKED:
        out.append("YANKED")
    if rec.kind is ArtifactKind.AGENT:
        out.append("AGENT")
    return out

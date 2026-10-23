# Release Guide

## Build tooling

Ananke Plexus uses **[uv](https://docs.astral.sh/uv/)** for all build, test, and publish operations. `twine` is not a runtime dependency.

| Action | Command |
|---|---|
| Install dev env | `make install` / `uv sync --all-extras` |
| Build artifacts | `make build` / `uv build` |
| Validate metadata | `uv run --with twine twine check dist/*` |
| Publish to TestPyPI | `make publish-testpypi` |
| Publish to PyPI | `make publish-pypi` |
| Security scan | `make trivy-scan` |

---

## 1. One-time GitHub repository setup

1. Create the package in both [TestPyPI](https://test.pypi.org) and [PyPI](https://pypi.org) with name `ananke-plexus`.
2. Configure **Trusted Publishers** (OIDC) in each index under your PyPI account:
   - **Publisher type:** GitHub Actions
   - **Owner:** `neo-pragnya`
   - **Repository:** `ananke-plexus`
   - **Workflow:** `release-pypi.yml` (or `release-testpypi.yml` for TestPyPI)
   - **Environment:** `pypi` (or `testpypi`)
3. Create protected GitHub **environments** named `pypi` and `testpypi` in repo Settings → Environments.
4. Enable required reviewers on the `pypi` environment for extra protection.

No long-lived PyPI API tokens are stored in GitHub secrets — OIDC handles the authentication automatically.

---

## 2. Pre-release quality gate

```bash
make install       # uv sync --all-extras
make verify        # ruff + mypy + pytest
make trivy-scan    # Trivy filesystem CVE scan (blocks on CRITICAL/HIGH)
make coverage      # coverage report
make acceptance-wheel  # capability scenarios against the built wheel + release gate
make scan          # what package firewalls check, on the built wheel and sdist (scan-report/summary.md)
```

The **acceptance gate** (`acceptance/`) installs the built wheel into a fresh virtual environment and runs more than
60 Gherkin scenarios against the real CLI, each in its own sandbox project. The scenarios cover install, specs,
graph, policy, hooks, verify, the registry and its security, Dex Trove, MCP, workflows, docs and the testing harness.
The gate decides:

| Decision | When | Release |
| --- | --- | --- |
| **PROMOTE** | every capability passed | ship |
| **CONDITIONAL** | known issues, unverified or non-blocker gaps (all listed in `acceptance/report/summary.md`) | ship deliberately, with the list in the release notes |
| **BLOCK** | a blocker capability failed or has no scenario | do not ship |

The `release-pypi` workflow runs the same gate on the exact wheel it publishes, and publishing waits for it. See
[`acceptance/README.md`](https://github.com/Neo-Pragnya/Ananke-Plexus/blob/main/acceptance/README.md) to add
scenarios.

---

## 3. Local token-based publish (manual fallback)

Only needed if OIDC is not configured. Set `UV_PUBLISH_TOKEN` in `.env`:

```bash
cp .env.example .env
# Edit .env: set UV_PUBLISH_TOKEN and UV_PUBLISH_TOKEN_TESTPYPI

# Publish to TestPyPI first
make publish-testpypi

# Validate the TestPyPI install
uv tool install --index https://test.pypi.org/simple/ ananke-plexus
ananke version

# Publish to PyPI
make publish-pypi
```

---

## 4. GitHub Trusted Publishing (recommended — CI path)

1. Bump version in `src/ananke/plexus/version.py`.
2. Update `CHANGELOG.md` with release notes.
3. Commit, push, then tag:

```bash
VERSION="$(uv run python -c 'from ananke.plexus.version import __version__; print(__version__)')"

git add src/ananke/plexus/version.py CHANGELOG.md
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"
git push origin main "v$VERSION"
```

The workflows run in this order:

```
release-testpypi.yml        release-pypi.yml
  └─ build                    └─ build
                              └─ acceptance (built wheel)
                              └─ dist-scan (blocking: secrets, vulnerable dependencies)
  └─ trivy (blocking)         └─ trivy (blocking)
  └─ provenance               └─ provenance
  └─ publish TestPyPI         └─ publish PyPI
                              └─ GitHub Release
```

**Build-once principle:** the same dist artifacts built in the `build` job are downloaded by the `publish` job — never rebuilt between validation and publishing.

---

## 5. What the release workflows produce

| Artifact | Description |
|---|---|
| `dist/*.whl` | Python wheel |
| `dist/*.tar.gz` | Source distribution |
| `dist/sbom.cdx.json` | CycloneDX SBOM |
| `trivy-results.sarif` | Trivy SARIF (uploaded to GitHub Security) |
| `dist-scan-<tag>` | Distribution scan report: `summary.md` and `scan.json` |
| Build attestation | GitHub artifact provenance via `actions/attest-build-provenance` |

---

## 6. Security gates in every release

Both release workflows have Trivy as a **hard gate**:

```
trivy job (CRITICAL + HIGH, unfixed) → exit-code 1 → blocks publish job
```

This means a release cannot proceed if there are unfixed CRITICAL or HIGH CVEs in the package or its dependencies.

### Distribution scan: what package firewalls see

Trivy scans the repository. Package firewalls and SCA tools (Black Duck, JFrog Xray and Curation, Sonatype, Socket)
scan the **published files**, and quarantine a release for secret-shaped strings, vulnerable dependencies,
malicious-code heuristics or licence mismatches. The `dist-scan` job runs `scripts/scan_dist.py` on the exact wheel
and sdist the release will publish, using free tools at pinned versions:

| Check | Tool | Effect |
| --- | --- | --- |
| Secrets in the wheel and sdist | gitleaks | blocks publish |
| Vulnerable dependencies in the tree the wheel resolves to (`all,mcp,dex` extras) | pip-audit | blocks publish |
| Malicious-package heuristics | GuardDog | reported |
| High-severity code issues in the shipped code | Bandit | reported |
| Inventory: scripts, binaries, minified JS, declared licence | built in | reported |

Run it locally with `make scan` (it needs `gitleaks` and `uv`; the Python tools come through `uvx`).

**Reviewed heuristic hits (0.3.11):**
- GuardDog's obfuscation, network and process rules match the vendored `mermaid.min.js` (MIT, 11.17.2, no known
  advisories). It ships so `ananke docs` renders diagrams offline.
- `threat-filesystem-destruction` matches the `rm -rf /` string in the governance evaluator's *deny* list.
- `threat-filesystem-read` matches `adapters.env`, Ananke's own git-ignored credentials file.

If your organisation's firewall quarantines a release, ask for the policy violation names and compare them with
this report. Packages only days old are often held by maturity rules regardless of content.

Additionally the weekly `security.yml` workflow runs:
- Trivy filesystem scan (SARIF + blocking)
- Semgrep SAST
- pip-audit dependency CVE check
- Gitleaks secrets scan

---

## 7. Post-release validation

```bash
# Install from PyPI and smoke-test
uv tool install ananke-plexus
ananke version
ananke doctor

# Or with pip
pip install -U ananke-plexus
ananke --help
```

---

## 8. Trivy local usage

```bash
# Install Trivy: https://aquasecurity.github.io/trivy/latest/getting-started/installation/

# Table report (blocks on CRITICAL/HIGH)
make trivy-scan

# JSON report (all severities)
make trivy-scan-json

# SARIF for IDE or GitHub upload
make trivy-scan-sarif
```

Trivy config can be customised via `.trivyignore` at the repo root.

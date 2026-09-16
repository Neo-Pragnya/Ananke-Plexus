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
```

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
git add src/ananke/plexus/version.py CHANGELOG.md
git commit -m "chore: release v0.1.0"
git tag v0.1.0
git push origin main v0.1.0
```

The workflows run in this order:

```
release-testpypi.yml        release-pypi.yml
  └─ build                    └─ build
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
| Build attestation | GitHub artifact provenance via `actions/attest-build-provenance` |

---

## 6. Security gates in every release

Both release workflows have Trivy as a **hard gate**:

```
trivy job (CRITICAL + HIGH, unfixed) → exit-code 1 → blocks publish job
```

This means a release cannot proceed if there are unfixed CRITICAL or HIGH CVEs in the package or its dependencies.

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

.PHONY: install lint format format-check typecheck test coverage verify \
        build build-check sbom trivy-scan trivy-scan-json trivy-scan-sarif \
        security docs-build docs-serve publish-testpypi publish-pypi clean check-uv \
        acceptance acceptance-wheel acceptance-dist scan scan-dist

UV := uv

# ── Setup ──────────────────────────────────────────────────────────────────
check-uv:
	@command -v $(UV) >/dev/null 2>&1 || \
	  (echo "uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh" && exit 1)

install: check-uv
	$(UV) sync --all-extras

# ── Quality ────────────────────────────────────────────────────────────────
lint:
	$(UV) run ruff check src tests

format:
	$(UV) run ruff format src tests

format-check:
	$(UV) run ruff format --check src tests

typecheck:
	$(UV) run mypy src

test:
	$(UV) run pytest

coverage:
	$(UV) run pytest --cov=src/ananke --cov-report=html --cov-report=term
	@echo "HTML report: htmlcov/index.html"

verify: lint format-check typecheck test

# ── Security ───────────────────────────────────────────────────────────────
trivy-scan:
	@command -v trivy >/dev/null 2>&1 || \
	  (echo "trivy not found — https://aquasecurity.github.io/trivy/latest/getting-started/installation/" && exit 1)
	trivy fs . \
	  --exit-code 1 \
	  --severity CRITICAL,HIGH \
	  --ignore-unfixed \
	  --ignorefile .trivyignore \
	  --format table
	@echo "Trivy scan passed."

trivy-scan-json:
	@command -v trivy >/dev/null 2>&1 || (echo "trivy not found" && exit 1)
	trivy fs . \
	  --format json \
	  --output trivy-report.json \
	  --severity CRITICAL,HIGH,MEDIUM \
	  --ignore-unfixed \
	  --ignorefile .trivyignore

trivy-scan-sarif:
	@command -v trivy >/dev/null 2>&1 || (echo "trivy not found" && exit 1)
	trivy fs . \
	  --format sarif \
	  --output trivy-results.sarif \
	  --severity CRITICAL,HIGH \
	  --ignore-unfixed \
	  --ignorefile .trivyignore

security: trivy-scan
	$(UV) run pip-audit
	@echo "Security scans complete."

# ── Build ──────────────────────────────────────────────────────────────────
build: check-uv
	rm -rf dist
	$(UV) build
	@echo "Artifacts in dist/:"
	@ls -lh dist/

build-check: build
	$(UV) run --with twine twine check dist/*

sbom:
	$(UV) run --with cyclonedx-bom cyclonedx-py environment \
	  --output-format json \
	  --output-file dist/sbom.cdx.json
	@echo "SBOM written to dist/sbom.cdx.json"

# ── Docs ───────────────────────────────────────────────────────────────────
docs-build:
	$(UV) run mkdocs build

docs-serve:
	$(UV) run mkdocs serve

# ── Publish ────────────────────────────────────────────────────────────────
# Set UV_PUBLISH_TOKEN or UV_PUBLISH_TOKEN_TESTPYPI in .env before running.
publish-testpypi: build
	@echo "Publishing to TestPyPI via uv..."
	$(UV) publish \
	  --publish-url https://test.pypi.org/legacy/ \
	  --token "$$UV_PUBLISH_TOKEN_TESTPYPI"

publish-pypi: build trivy-scan
	@echo "Publishing to PyPI via uv..."
	$(UV) publish --token "$$UV_PUBLISH_TOKEN"

# ── Acceptance (capability scenarios + release gate) ─────────────────────────
# Gherkin scenarios in acceptance/features drive the real CLI in sandbox projects; the gate writes
# acceptance/report/{acceptance.json,summary.md} and fails on BLOCK. See acceptance/README.md.
ACCEPTANCE_VENV := .acceptance-venv
ACCEPTANCE_EXTRAS := dex,docs,mcp,test-python
ACCEPTANCE_ARGS ?=

acceptance:
	$(UV) run --frozen pytest acceptance -n auto -p no:cacheprovider $(ACCEPTANCE_ARGS)

# What ships: build the wheel, install it (with extras and the project tooling verify runs) into a fresh venv,
# and run the suite from that venv. Release workflows run exactly this.
# Scan the built wheel and sdist the way package firewalls do (secrets, vulnerable dependencies, malicious-code
# heuristics, inventory) with free tools: gitleaks, pip-audit, GuardDog, Bandit. Report in scan-report/.
SCAN_EXTRAS ?= all,mcp,dex
scan: build scan-dist

scan-dist:
	$(UV) run --no-project python scripts/scan_dist.py --dist dist --out scan-report $(if $(SCAN_EXTRAS),--extras $(SCAN_EXTRAS)) $(SCAN_ARGS)

acceptance-wheel: build acceptance-dist

# Run the suite against an already-built dist/*.whl (CI uses this to test the exact artifact it publishes).
acceptance-dist:
	rm -rf $(ACCEPTANCE_VENV)
	$(UV) venv -q --python 3.12 $(ACCEPTANCE_VENV)
	$(UV) pip install -q --python $(ACCEPTANCE_VENV)/bin/python "$$(ls -t dist/*.whl | head -1)[$(ACCEPTANCE_EXTRAS)]" \
	  ruff mypy pytest pytest-bdd pytest-xdist pyyaml
	ACCEPTANCE_BIN_DIR=$(CURDIR)/$(ACCEPTANCE_VENV)/bin $(ACCEPTANCE_VENV)/bin/python -m pytest acceptance \
	  -n auto -p no:cacheprovider $(ACCEPTANCE_ARGS)

# ── Clean ──────────────────────────────────────────────────────────────────
clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov \
	       trivy-report.txt trivy-report.json trivy-results.sarif site/ \
	       $(ACCEPTANCE_VENV) acceptance/report

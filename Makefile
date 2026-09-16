.PHONY: install lint format format-check typecheck test coverage verify \
        build build-check sbom trivy-scan trivy-scan-json trivy-scan-sarif \
        security docs-build docs-serve publish-testpypi publish-pypi clean check-uv

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

# ── Clean ──────────────────────────────────────────────────────────────────
clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov \
	       trivy-report.txt trivy-report.json trivy-results.sarif site/

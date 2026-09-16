#!/usr/bin/env bash
# Local publish to PyPI using uv.
# Reads UV_PUBLISH_TOKEN from .env or the environment.
set -euo pipefail

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

: "${UV_PUBLISH_TOKEN:?UV_PUBLISH_TOKEN is required — set it in .env}"

echo "==> Running Trivy security gate..."
if command -v trivy &>/dev/null; then
  trivy fs . --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed --format table \
    || { echo "Trivy found CRITICAL/HIGH issues. Aborting publish."; exit 1; }
else
  echo "WARNING: trivy not installed — skipping local security gate"
fi

echo "==> Building artifacts with uv..."
rm -rf dist
uv build

echo "==> Validating metadata..."
uv run --with twine twine check dist/*

echo "==> Publishing to PyPI..."
uv publish --token "$UV_PUBLISH_TOKEN"

echo "Done. See: https://pypi.org/project/ananke-plexus/"

#!/usr/bin/env bash
# Local publish to TestPyPI using uv.
# Reads UV_PUBLISH_TOKEN_TESTPYPI from .env or the environment.
set -euo pipefail

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

: "${UV_PUBLISH_TOKEN_TESTPYPI:?UV_PUBLISH_TOKEN_TESTPYPI is required — set it in .env}"

echo "==> Building artifacts with uv..."
rm -rf dist
uv build

echo "==> Validating metadata..."
uv run --with twine twine check dist/*

echo "==> Publishing to TestPyPI..."
uv publish \
  --publish-url https://test.pypi.org/legacy/ \
  --token "$UV_PUBLISH_TOKEN_TESTPYPI"

echo "Done. See: https://test.pypi.org/project/ananke-plexus/"

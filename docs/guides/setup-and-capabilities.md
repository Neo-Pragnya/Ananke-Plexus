# Setup and Capabilities

This guide gives you an operational setup for building and shipping `ananke-plexus`.

## Core capabilities in this implementation

- CLI control plane:
  - `ananke init`
  - `ananke doctor`
  - `ananke configure auth`
  - `ananke spec create`
  - `ananke spec plan`
  - `ananke spec tasks`
  - `ananke spec lock`
  - `ananke spec validate`
  - `ananke spec diff`
  - `ananke spec converge`
  - `ananke spec superpowers`
  - `ananke graph build`
  - `ananke graph query`
  - `ananke graph impact`
  - `ananke graph export`
  - `ananke run start`
  - `ananke run status`
  - `ananke run cancel`
  - `ananke run replay`
  - `ananke serve-mcp`
  - `ananke lifecycle branch`
  - `ananke lifecycle issue-transition`
  - `ananke lifecycle pr-create`
  - `ananke lifecycle confluence-upsert`
  - `ananke lifecycle worktrees`
  - `ananke plugin list`
  - `ananke verify`
- Skill & Agent Registry (see [Registry Guide](registry-guide.md)):
  - `ananke registry init | learn | register | inspect | show | list | search | diff | resolve`
  - `ananke registry promote | yank | deprecate | quarantine | alias`
  - `ananke registry activate | translate | lock | verify-lock | publish | link`
  - `ananke registry export | import | backup | restore | verify | doctor | gc | report | analytics`
  - `ananke registry docs build | dump`, `ananke registry serve | watch | schema | policy | benchmark`
  - `ananke registry key generate | trust | revoke | list`, `sign`, `signatures`
  - `ananke registry remote list | search | pull`, `analytics query`, `search --semantic`
  - `ananke skill ...`, `ananke agent ...`, `ananke sync`
- Evaluation harness: `ananke eval run | list | ...` ([details](../concepts/evaluation.md))
- Testing harness: `ananke test run | discover | report | profile | adapter | mutation | fuzz | formal` ([details](../concepts/testing.md))
- Spec providers:
  - `speckit` adapter (invokes `specify` or `spec-kit` if installed)
  - `native` provider fallback
- APM baseline:
  - `apm list`
  - `apm install --source PATH`
  - `apm activate --name NAME`
  - `apm deactivate --name NAME`
  - `apm info`
  - `apm verify`
  - `apm resolve --ref VALUE`
  - `apm audit --manifest PATH`
  - `apm sandbox-check --manifest PATH ...`
  - `apm import-copilot --source PATH`
  - Registry-backed: `apm search`, `apm install REF`, `apm activate REF`, `apm info REF`, `apm lock`, `apm upgrade`, `apm link`, `apm unlink`, `apm publish`
- Local-first workspace assets:
  - `.ananke/config.toml`
  - `.ananke/config.local.toml`
  - `.ananke/config.local.toml.example`
  - `.ananke/secrets/adapters.env` (chmod 600)
  - `.ananke/policy/default.toml`
  - `.ananke/architecture/system.calm.json`
  - `.ananke/registry/` (registry.sqlite3, blobs/, policy.toml, docs/, exports/, backups/)
  - `.ananke/activation.toml`, `ananke.lock`
- Evidence output:
  - `.ananke/evidence/<run-id>/run.json`
  - `.ananke/evidence/<run-id>/manifest.json`
  - `.ananke/evidence/<run-id>/checksums.sha256`

## Tooling matrix and current state

- Ruff: wired in local + CI
- mypy: wired in local + CI
- pytest: wired in local + CI
- pip/twine/build: wired for package publishing
- Gitleaks: scaffolded in security workflow
- Semgrep: scaffolded in security workflow
- pip-audit: scaffolded in security workflow
- Trivy: scaffolded in security workflow
- MCP read-only stdio + HTTP transport with tools/resources/prompts routing: implemented baseline (`ananke serve-mcp --transport stdio|http`)
- Jira/Bitbucket adapters: planned next phase
- Graph canonical snapshot/query/impact baseline: implemented
- Graphifyy/code-review-graph adapters: planned next phase
- Lifecycle idempotency store and local transition/PR simulation: implemented baseline
- Isolated run worktree folder lifecycle: implemented baseline

## Optional extras

| Extra | Adds |
|---|---|
| `registry` | `registry-fast` (`blake3`), `registry-zstd` (`.tar.zst`), `registry-validate` (jsonschema), `registry-signing`, `registry-watch` |
| `registry-signing` | `cryptography` — Ed25519 signing (verifying needs nothing) |
| `registry-watch` | `watchfiles` — native file events for `registry watch` |
| `registry-analytics` | DuckDB analytics engine and `registry.duckdb` (optional, not in `registry`) |
| `eval-enterprise` | Enterprise eval harness dependencies |
| `test-python`, `test-bdd`, `test-property`, `test-api`, `test-snapshot`, `test-mutation`, `test-contract`, `test-automation`, `test-all` | Test-harness engines |
| `all` | `mcp`, `telemetry`, `jira`, `security`, `docs`, `eval-enterprise`, `test-python`, `registry` |

The registry works with none of these installed; each accelerator falls back to a pure-Python path (gzip instead of zstd, SHA-256 without blake3, structural checks without jsonschema).

## Local development commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make verify
```

## Initialize project state

```bash
ananke init
ananke doctor
ananke doctor --json
ananke configure auth \
  --remote-live-enabled "true" \
  --remote-live-services "jira,bitbucket,confluence" \
  --jira-base-url "https://your-org.atlassian.net" \
  --jira-email "you@company.com" \
  --jira-token "***" \
  --jira-bearer-token "" \
  --jira-bearer-token-cmd "" \
  --confluence-base-url "https://your-org.atlassian.net/wiki" \
  --confluence-email "you@company.com" \
  --confluence-token "***" \
  --confluence-bearer-token "" \
  --confluence-bearer-token-cmd "" \
  --bitbucket-base-url "https://api.bitbucket.org" \
  --bitbucket-workspace "your-workspace" \
  --bitbucket-repo "your-repo" \
  --bitbucket-destination "main" \
  --bitbucket-username "your-user" \
  --bitbucket-app-password "***" \
  --bitbucket-bearer-token "" \
  --bitbucket-bearer-token-cmd ""

# token broker / SSO command sources (optional)
ananke configure auth \
  --jira-token-cmd "security find-generic-password -w -s ANANKE_JIRA_TOKEN" \
  --confluence-token-cmd "security find-generic-password -w -s ANANKE_CONFLUENCE_TOKEN" \
  --bitbucket-app-password-cmd "security find-generic-password -w -s ANANKE_BITBUCKET_APP_PASSWORD"

# safer for terminals and shell history
ananke configure auth-interactive

# load credentials into current shell session
ananke configure auth-export --shell zsh

# validate adapter readiness
ananke configure auth-validate
```

This creates or updates a centralized credential store at `.ananke/secrets/adapters.env` and keeps adapter configs in `.ananke/config.local.toml` mapped through environment keys.

### Cross-platform and container loading patterns

- macOS/Linux (zsh/bash/sh):
  - `eval "$(ananke configure auth-export --shell zsh --project . | sed -n '/set -a/,$p')"`
- Windows PowerShell:
  - `ananke configure auth-export --shell pwsh --project .`
  - Run the printed snippet in the same session.
- Windows CMD:
  - `ananke configure auth-export --shell cmd --project .`
  - Run the printed `for /f ...` snippet in the same session.
- Docker run:
  - `docker run --env-file .ananke/secrets/adapters.env ...`
- Docker Compose:
  - `env_file:` `.ananke/secrets/adapters.env`

Adapter lifecycle commands support `--mode local|auto|remote-dry-run`.
- `auto`: uses remote-dry-run only when required credentials are present.
- `local`: no remote adapter behavior.
- `remote-dry-run`: validates credentials and prints endpoint/method intent without making network calls.

Adapter lifecycle commands also support `--mode remote-live`.
- `remote-live`: performs real HTTP requests to Jira/Confluence/Bitbucket with retries, timeout, and structured error reporting.
- remote-live execution is policy-gated by `ANANKE_REMOTE_LIVE_ENABLED` and `ANANKE_REMOTE_LIVE_SERVICES`.

Telemetry for remote adapter health and circuit-breaker state:
- `ananke lifecycle telemetry --project .`
- `ananke lifecycle evidence --service jira --status remote_live_error --limit 20 --project .`
- `ananke lifecycle evidence --service jira --since-hours 24 --format compact --limit 20 --project .`
- `ananke lifecycle evidence --format json --limit 50 --project .`
- `ananke lifecycle evidence --aggregate --format compact --since-hours 24 --project .`
- `ananke lifecycle evidence --aggregate --format csv --csv-path ./evidence-summary.csv --project .`
- `ananke lifecycle evidence --severity critical --since-hours 24 --format compact --project .`
- `ananke lifecycle evidence --trend hour --since-hours 24 --format compact --project .`
- `ananke lifecycle evidence --trend day --format csv --csv-path ./evidence-trend.csv --project .`

Architecture commands:
- `ananke arch init --project .`
- `ananke arch validate --project .`
- `ananke arch diff --project .`
- `ananke arch reconcile --project .`
- `ananke arch reconcile --apply --project .`
- `ananke arch render --project .`

## Create a requirement-backed spec

```bash
ananke spec create \
  --id DEMO-101 \
  --title "Evidence manifest" \
  --body "Generate immutable evidence on verify" \
  --acceptance "manifest.json exists" \
  --acceptance "checksums.sha256 exists" \
  --provider speckit
```

## Run full end-to-end spec pipeline (superpowers)

```bash
ananke spec superpowers \
  --id ANANKE-201 \
  --title "Implement spec lifecycle end-to-end" \
  --body "Create requirement, spec, plan, tasks, BMAD, lock, and drift status" \
  --acceptance "plan exists" \
  --acceptance "tasks exists" \
  --acceptance "spec.lock.json exists" \
  --provider speckit
```

## Run verification and produce evidence

```bash
ananke verify
ananke policy explain --stage verify

# retention (safe dry-run first)
ananke evidence prune --older-than 30d --project .
ananke evidence prune --older-than 30d --apply --project .

# config migration (safe analysis first)
ananke config migrate --project .
ananke config migrate --apply --project .
```

## Publish flows

### Flow A: Local token-based publish

Use when publishing directly from your machine.

```bash
cp .env.example .env
# update UV_PUBLISH_TOKEN and UV_PUBLISH_TOKEN_TESTPYPI
bash scripts/publish_local_testpypi.sh
bash scripts/publish_local_pypi.sh
```

### Flow B: GitHub Trusted Publishing

Use when publishing through CI with OIDC (recommended).

```bash
VERSION="$(uv run python -c 'from ananke.plexus.version import __version__; print(__version__)')"

# bump src/ananke/plexus/version.py before this
git add .
git commit -m "release: v$VERSION"
git tag "v$VERSION"
git push origin main --tags
```

`release-testpypi.yml` and `release-pypi.yml` then publish by tag.

## Token update checklist

1. Open `.env`.
2. Set `UV_PUBLISH_TOKEN` to your PyPI token.
3. Set `UV_PUBLISH_TOKEN_TESTPYPI` to your TestPyPI token.
4. For CI fallback, add matching GitHub environment secrets (`pypi` and `testpypi`).
5. Never commit `.env`.

## Next capability milestones

- Implement full `ananke run start` DAG engine
- Add MCP server (`ananke serve-mcp`)
- Add graph provider adapters and impact reports
- Add lifecycle adapters (Jira and Bitbucket)
- Add SARIF + richer evidence schemas

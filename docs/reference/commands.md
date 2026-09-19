# Command Reference

## ananke (core)

- `ananke version`: print package version.
- `ananke init [--project PATH]`: bootstrap `.ananke/` structure and baseline config.
- `ananke doctor [--project PATH] [--json]`: run environment diagnostics.
- `ananke verify [--project PATH]`: run baseline verification and emit evidence bundle.
- `ananke serve-mcp [--project PATH] [--transport stdio|http] [--token VALUE]`: start MCP read-only loop over stdio or HTTP transport.

---

## ananke configure

- `ananke configure auth [--project PATH] [--jira-... --confluence-... --bitbucket-... --mcp-http-token ...]`: create/update centralized adapter credentials in `.ananke/secrets/adapters.env`.
    - Supports command-based secret providers: `--jira-token-cmd`, `--confluence-token-cmd`, `--bitbucket-app-password-cmd`, `--mcp-http-token-cmd`.
    - Supports bearer command providers: `--jira-bearer-token-cmd`, `--confluence-bearer-token-cmd`, `--bitbucket-bearer-token-cmd`.
    - Supports bearer providers: `--jira-bearer-token`, `--confluence-bearer-token`, `--bitbucket-bearer-token`.
    - Supports Bitbucket repository targeting: `--bitbucket-workspace`, `--bitbucket-repo`, `--bitbucket-destination`.
    - Supports remote-live policy controls: `--remote-live-enabled true|false`, `--remote-live-services jira,bitbucket,confluence`.
- `ananke configure auth-interactive [--project PATH]`: prompt securely for adapter configuration; token/password fields are hidden input.
- `ananke configure auth-export [--project PATH] [--shell zsh|bash|sh|fish|pwsh|cmd|docker-env]`: print shell or container snippet to load `.ananke/secrets/adapters.env` into current process.
- `ananke configure auth-validate [--project PATH]`: validate required credentials for Jira/Confluence/Bitbucket adapters.

---

## ananke config

- `ananke config migrate [--apply] [--project PATH]`: analyze or apply safe config key migrations for `.ananke/config.toml`; refuses rewrites when unknown keys are present.

---

## ananke evidence

- `ananke evidence prune [--older-than 30d|12h|90m] [--apply] [--project PATH]`: analyze or prune old evidence run directories while skipping git-tracked evidence paths.

---

## ananke spec

- `ananke spec create --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: create requirement/spec/BMAD bundle.
- `ananke spec plan --feature-dir PATH [--provider speckit|native]`: generate implementation plan.
- `ananke spec tasks --feature-dir PATH [--provider speckit|native]`: generate task checklist.
- `ananke spec lock --feature-dir PATH`: write/update spec lock.
- `ananke spec validate --feature-dir PATH`: validate required artifacts and drift.
- `ananke spec diff --feature-dir PATH`: report lock drift.
- `ananke spec converge --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: run end-to-end spec pipeline.
- `ananke spec superpowers --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: converge pipeline via single high-agency command.

---

## ananke bmad

- `ananke bmad compile --feature-dir PATH [--project PATH]`: compile BMAD contracts (behavior scenarios, model schemas, architecture contract) from a feature directory.
- `ananke bmad show --feature-dir PATH [--project PATH]`: display compiled BMAD contract contents.
- `ananke bmad trace --feature-dir PATH [--project PATH]`: show traceability matrix linking requirements to contracts to evidence.
- `ananke bmad verify --feature-dir PATH [--project PATH]`: verify contracts against declared architecture and spec lock.

---

## ananke graph

- `ananke graph build [--project PATH]`: build canonical code graph snapshot.
- `ananke graph query --text TEXT [--project PATH]`: query graph nodes by name/path.
- `ananke graph impact --changed-file PATH ... [--project PATH]`: compute impacted symbols.
- `ananke graph export --format json|markdown [--project PATH]`: export graph snapshot.

---

## ananke arch

- `ananke arch init [--project PATH]`: initialize the CALM architecture file.
- `ananke arch validate [--project PATH]`: validate CALM structure and relationship references.
- `ananke arch diff [--project PATH]`: compare CALM components against graph-derived component roots.
- `ananke arch reconcile [--apply] [--project PATH]`: preview or apply missing graph components into CALM.
- `ananke arch render [--project PATH]`: generate Markdown and Mermaid architecture output with graph overlay status.

---

## ananke policy

- `ananke policy explain [--stage verify] [--project PATH]`: evaluate configured policy rules for a stage and print rule-by-rule decisions.
- `ananke policy list [--project PATH] [--json]`: list all configured policy rules.
- `ananke policy check [--project PATH]`: run policy checks and report gate outcomes.
- `ananke policy install-pack PACK_NAME [--project PATH]`: install a named policy pack (e.g. `python-library`, `data-pipeline`) into `.ananke/policy/`.

---

## ananke hooks

- `ananke hooks install [--project PATH]`: install git hooks (pre-commit and pre-push) into `.git/hooks/`.
- `ananke hooks uninstall [--project PATH]`: remove installed git hooks.
- `ananke hooks status [--project PATH]`: show which hooks are installed and their hash.
- `ananke hooks run --stage pre-commit|pre-push [--project PATH]`: run a hook stage manually.

---

## ananke run

- `ananke run start --spec-id ID [--project PATH]`: start an autonomous run baseline.
- `ananke run status --run-id ID [--project PATH]`: get run state.
- `ananke run cancel --run-id ID [--project PATH]`: cancel run state.
- `ananke run replay --run-id ID [--project PATH]`: replay run to READY state.
- `ananke run approvals --run-id ID [--project PATH]`: list pending approval requests for a run.
- `ananke run approve --run-id ID --approval-id ID [--project PATH]`: grant an approval request.

---

## ananke backend

- `ananke backend list [--project PATH] [--json]`: list registered agent backends and their capabilities.
- `ananke backend doctor [--backend NAME] [--project PATH]`: check health and prerequisites for a backend.
- `ananke backend test --backend NAME [--spec-id ID] [--project PATH]`: run a quick capability test against a backend.

---

## ananke lifecycle

- `ananke lifecycle branch --type TYPE --ticket KEY --slug TEXT [--project PATH]`: generate policy branch name.
- `ananke lifecycle issue-transition --issue KEY --to STATE --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: transition issue.
- `ananke lifecycle pr-create --title TEXT --branch NAME --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: create PR.
- `ananke lifecycle worktrees [--project PATH]`: list isolated run worktree paths.
- `ananke lifecycle confluence-upsert --space SPACE --title TITLE --content FILE --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: upsert Confluence page.
- `ananke lifecycle telemetry [--project PATH]`: show remote adapter telemetry and circuit-breaker counters.
- `ananke lifecycle evidence [--service jira|confluence|bitbucket] [--status ...] [--severity info|warning|critical] [--limit N] [--since-hours N] [--format compact|json|csv] [--aggregate] [--trend hour|day] [--csv-path PATH] [--project PATH]`: inspect recent remote lifecycle evidence events.

---

## ananke plugin

- `ananke plugin list [--project PATH]`: discover installed entry-point plugins.

---

## ananke eval

The evaluation harness CLI.

- `ananke eval run [--suite TEXT] [--case TEXT] [--trace PATH] [--output TEXT] [--project PATH] [--json]`: run an evaluation suite against an agent trace or output. Exit 0 = PASS/WARN, 1 = BLOCK, 2 = not found.
- `ananke eval report --run RUN_ID [--format console|markdown|json|junit] [--project PATH]`: render evaluation report for a completed run.
- `ananke eval compare --baseline BASELINE_ID --run RUN_ID [--project PATH]`: compare a run against an approved baseline with regression analysis.

### ananke eval suite

- `ananke eval suite list [--project PATH] [--json]`: list eval suites found in `.ananke/evals/suites/`.
- `ananke eval suite validate --path SUITE_FILE`: validate an eval suite JSON file.

### ananke eval dataset

- `ananke eval dataset list [--project PATH] [--json]`: list datasets in `.ananke/evals/datasets/`.
- `ananke eval dataset validate --path DATASET_FILE`: validate a dataset YAML file.

### ananke eval baseline

- `ananke eval baseline create --run RUN_ID --id BASELINE_ID [--project PATH]`: promote a completed run to a named baseline.

### ananke eval trace

- `ananke eval trace show --path TRACE_FILE`: display a trace file in readable form.
- `ananke eval trace import --path TRACE_FILE [--run-id ID] [--project PATH]`: import a trace JSON into the evidence store.

### ananke eval adapter

- `ananke eval adapter list [--json]`: list available eval adapters and their availability status.
- `ananke eval adapter doctor [ADAPTER_ID] [--json]`: run health checks on eval adapters.

### ananke eval judge

- `ananke eval judge list`: list registered LLM judge providers.
- `ananke eval judge test [--provider local|azure|bedrock|anthropic|openai]`: run a connectivity test against a judge provider.

---

## ananke test

The unified quality test harness CLI.

- `ananke test run [--profile fast|standard|strict|verification|release] [--kinds KINDS] [--project PATH] [--json]`: run the quality suite for the given profile.
- `ananke test discover [--project PATH] [--json]`: discover all test files and categorise them by kind.
- `ananke test list [--project PATH] [--json]`: list all tests that would run for the active profile.
- `ananke test report --run RUN_ID [--format console|markdown|json|junit|sarif] [--project PATH]`: render a test run report.

### ananke test profile

- `ananke test profile list [--json]`: list all built-in test profiles and their included test kinds.
- `ananke test profile show PROFILE_NAME`: show the full configuration for a profile.

### ananke test adapter

- `ananke test adapter list [--json]`: list all registered test adapters.
- `ananke test adapter doctor [ADAPTER_ID] [--json]`: run health checks on test adapters.

### ananke test mutation

- `ananke test mutation run [--target PATH] [--project PATH]`: run mutation testing via mutmut.

### ananke test fuzz

- `ananke test fuzz run [--target PATH] [--project PATH]`: run fuzz testing via cargo-fuzz or equivalent.

### ananke test formal

- `ananke test formal run [--target PATH] [--project PATH]`: run formal verification via kani or equivalent.

---

## ananke registry

Full reference: [Registry Reference](registry.md). Every command accepts `--project PATH` and `--user`.

- `ananke registry init [--policy default|enterprise]`: create the registry and policy file.
- `ananke registry learn SOURCE [--kind --namespace --name --version X|auto --license --channel --allow-dynamic --allow-network --plugin --dry-run --force --interactive --json]`: discover a source (path, `python:`, `rust:`, `mcp:`, `mcp-stdio:`, `mcp-http:`, `git:`, archive, `framework:`, `dynamic:`) and register new immutable versions.
- `ananke registry register PATH`, `unregister REF [--purge]`, `inspect REF | --source SRC [--report FILE.html]`, `show REF`, `list [REF] [--versions]`, `search [QUERY] [--kind --runtime --trust --channel --capability --tag --license]`.
- `ananke registry diff A B`: capability/permission/schema/dependency diff with suggested SemVer.
- `ananke registry resolve REF [--runtime --mode --explain]`: policy-aware resolution with explanations.
- `ananke registry promote REF [--channel --trust --reviewed-by --run-tests --skip-gate]`, `yank`, `unyank`, `deprecate [--replacement]`, `quarantine`, `release-quarantine`, `alias set|list|remove`.
- `ananke registry activate|deactivate REF`, `translate REF --runtime RT`.
- `ananke registry lock [REFS…]`, `verify-lock [PATH]`, `publish PATH`, `link PATH`, `unlink REF`, `evidence`.
- `ananke registry verify`, `doctor`, `export`, `import`, `backup`, `restore`, `gc [--apply]`, `rebuild-index`, `snapshot`, `events`.
- `ananke registry report NAME`, `analytics build`, `duplicates`, `recommend CAPABILITY`, `watch [--once]`, `schema [NAME]`, `policy show|init`.
- `ananke registry docs build [--single-file]`, `docs dump -o FILE`, `serve [--host --port --token-env NAME]`.
- Signing: `key generate|trust|revoke|list`, `sign REF --key FILE`, `signatures REF`.
- Federation (pull-only): `remote list`, `remote search QUERY`, `remote pull REF [--no-deps --dry-run --allow-network]`.
- Search & insight: `search --semantic` (policy-gated), `analytics query [QUESTION]`, `watch --backend auto|native|poll`, `benchmark [--size --save-baseline --baseline]`.

## ananke skill / ananke agent

- `ananke skill|agent list [--versions]`, `show REF`, `search [QUERY]`, `register PATH`, `resolve REF [--explain]`, `versions REF`, `activate REF`.
- `ananke agent search [--skill S] [--runtime R] [--capability C]`: agents that compose a skill or provide a capability.

## ananke sync

- `ananke sync [--lock PATH --update --runtime --mode --activate --release --dry-run]`: resolve `[tool.ananke.agent]` / `[tool.ananke.skills]` (and APM-installed requirements) and write `ananke.lock`.

## apm

- `apm list [--project PATH] [--json]`: list installed and active local skills.
- `apm install --source PATH [--project PATH]`: install local skill package with manifest and lock update (legacy).
- `apm install REF [--activate --allow-prerelease --runtime]`: resolve from the registry and install the dependency graph.
- `apm search QUERY`, `apm lock [--update]`, `apm upgrade [--dry-run]`, `apm link PATH`, `apm unlink REF`, `apm publish PATH`.
- `apm activate REF | --name NAME [--project PATH]`: activate a registry version, or an installed skill by directory name (legacy).
- `apm deactivate --name NAME [--project PATH]`: deactivate a skill.
- `apm info [REF] [--project PATH]`: registry details for `REF`; without `REF`, print apm.lock details.
- `apm verify [--project PATH]`: verify lockfile structure.
- `apm resolve --ref VALUE [--project PATH]`: resolve skill path or installed reference.
- `apm audit --manifest PATH`: audit manifest permissions.
- `apm sandbox-check --manifest PATH [--shell CMD] [--network HOST] [--write PATH]`: evaluate permission decisions.
- `apm import-copilot --source PATH [--project PATH]`: import Copilot SKILL.md assets as APM skills.
- `apm bundle list --bundle PATH`: inspect members of an exported APM bundle archive.
- `apm bundle export --name INSTALLED_NAME ... [--output PATH] [--project PATH]`: export installed skills into a bundle archive.
- `apm bundle install --bundle PATH [--project PATH]`: install all members from a bundle archive.

---

## Make targets

- `make install`: `uv sync --all-extras`.
- `make lint`: run Ruff checks.
- `make format`: run Ruff formatter.
- `make typecheck`: run mypy.
- `make test`: run pytest.
- `make verify`: lint + typecheck + tests.
- `make build`: `uv build`.
- `make docs-build`: build static MkDocs site into `site/`.
- `make docs-serve`: run local MkDocs preview server.
- `make publish-testpypi`: publish to TestPyPI via `uv publish`.
- `make publish-pypi`: publish to PyPI via `uv publish`.

---

## Docs Hosting

GitHub Pages deploy workflow: `.github/workflows/docs.yml` — uses uv, copies `assets/` into `docs/`, then runs `mkdocs gh-deploy --force`.

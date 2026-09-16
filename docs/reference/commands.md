# Command Reference

## ananke

- `ananke version`: print package version.
- `ananke init [--project PATH]`: bootstrap `.ananke/` structure and baseline config.
- `ananke doctor [--project PATH] [--json]`: run environment diagnostics.
- `ananke verify [--project PATH]`: run baseline verification and emit evidence bundle.
- `ananke policy explain [--stage verify] [--project PATH]`: evaluate configured policy rules for a stage and print rule-by-rule decisions.
- `ananke serve-mcp [--project PATH] [--transport stdio|http] [--token VALUE]`: start MCP read-only loop over stdio or HTTP transport.
- `ananke config migrate [--apply] [--project PATH]`: analyze or apply safe config key migrations for `.ananke/config.toml`; refuses rewrites when unknown keys are present.
- `ananke evidence prune [--older-than 30d|12h|90m] [--apply] [--project PATH]`: analyze or prune old evidence run directories while skipping git-tracked evidence paths.
- `ananke configure auth [--project PATH] [--jira-... --confluence-... --bitbucket-... --mcp-http-token ...]`: create/update centralized adapter credentials in `.ananke/secrets/adapters.env`.
	- Supports command-based secret providers: `--jira-token-cmd`, `--confluence-token-cmd`, `--bitbucket-app-password-cmd`, `--mcp-http-token-cmd`.
	- Supports bearer command providers: `--jira-bearer-token-cmd`, `--confluence-bearer-token-cmd`, `--bitbucket-bearer-token-cmd`.
	- Supports bearer providers: `--jira-bearer-token`, `--confluence-bearer-token`, `--bitbucket-bearer-token`.
	- Supports Bitbucket repository targeting: `--bitbucket-workspace`, `--bitbucket-repo`, `--bitbucket-destination`.
	- Supports remote-live policy controls: `--remote-live-enabled true|false`, `--remote-live-services jira,bitbucket,confluence`.
- `ananke configure auth-interactive [--project PATH]`: prompt securely for adapter configuration; token/password fields are hidden input.
- `ananke configure auth-export [--project PATH] [--shell zsh|bash|sh|fish|pwsh|cmd|docker-env]`: print shell or container snippet to load `.ananke/secrets/adapters.env` into current process.
- `ananke configure auth-validate [--project PATH]`: validate required credentials for Jira/Confluence/Bitbucket adapters.
- `ananke spec create --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: create requirement/spec/BMAD bundle.
- `ananke spec plan --feature-dir PATH [--provider speckit|native]`: generate implementation plan.
- `ananke spec tasks --feature-dir PATH [--provider speckit|native]`: generate task checklist.
- `ananke spec lock --feature-dir PATH`: write/update spec lock.
- `ananke spec validate --feature-dir PATH`: validate required artifacts and drift.
- `ananke spec diff --feature-dir PATH`: report lock drift.
- `ananke spec converge --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: run end-to-end spec pipeline.
- `ananke spec superpowers --id ID --title TEXT [--body TEXT] [--acceptance TEXT ...] [--provider speckit|native]`: converge pipeline via single high-agency command.
- `ananke graph build [--project PATH]`: build canonical code graph snapshot.
- `ananke graph query --text TEXT [--project PATH]`: query graph nodes by name/path.
- `ananke graph impact --changed-file PATH ... [--project PATH]`: compute impacted symbols.
- `ananke graph export --format json|markdown [--project PATH]`: export graph snapshot.
- `ananke arch init [--project PATH]`: initialize the CALM architecture file.
- `ananke arch validate [--project PATH]`: validate CALM structure and relationship references.
- `ananke arch diff [--project PATH]`: compare CALM components against graph-derived component roots.
- `ananke arch reconcile [--apply] [--project PATH]`: preview or apply missing graph components into CALM.
- `ananke arch render [--project PATH]`: generate Markdown and Mermaid architecture output with graph overlay status.
- `ananke run start --spec-id ID [--project PATH]`: start an autonomous run baseline.
- `ananke run status --run-id ID [--project PATH]`: get run state.
- `ananke run cancel --run-id ID [--project PATH]`: cancel run state.
- `ananke run replay --run-id ID [--project PATH]`: replay run to READY state.
- `ananke plugin list [--project PATH]`: discover installed entry-point plugins.
- `ananke lifecycle branch --type TYPE --ticket KEY --slug TEXT [--project PATH]`: generate policy branch name.
- `ananke lifecycle issue-transition --issue KEY --to STATE --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: transition issue in local simulation, dry-run adapter mode, or live remote mode.
- `ananke lifecycle pr-create --title TEXT --branch NAME --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: create PR in local simulation, dry-run adapter mode, or live remote mode.
- `ananke lifecycle worktrees [--project PATH]`: list isolated run worktree paths.
- `ananke lifecycle confluence-upsert --space SPACE --title TITLE --content FILE --idempotency-key KEY [--mode local|auto|remote-dry-run|remote-live] [--project PATH]`: upsert Confluence page in local simulation, dry-run adapter mode, or live remote mode.
- `ananke lifecycle telemetry [--project PATH]`: show remote adapter telemetry and circuit-breaker counters.
- `ananke lifecycle evidence [--service jira|confluence|bitbucket] [--status remote_live_success|remote_live_error|policy_blocked|circuit_open] [--severity info|warning|critical] [--limit N] [--since-hours N] [--format compact|json|csv] [--aggregate] [--trend hour|day] [--csv-path PATH] [--project PATH]`: inspect recent remote lifecycle evidence events, optionally filtered by severity, grouped by service/status, grouped by time trend, or exported as CSV.

## apm

- `apm list [--project PATH] [--json]`: list installed and active local skills.
- `apm install --source PATH [--project PATH]`: install local skill package with manifest and lock update.
- `apm activate --name NAME [--project PATH]`: activate an installed skill.
- `apm deactivate --name NAME [--project PATH]`: deactivate a skill.
- `apm info [--project PATH]`: print apm.lock details.
- `apm verify [--project PATH]`: verify lockfile structure.
- `apm resolve --ref VALUE [--project PATH]`: resolve skill path or installed reference.
- `apm audit --manifest PATH`: audit manifest permissions.
- `apm sandbox-check --manifest PATH [--shell CMD] [--network HOST] [--write PATH]`: evaluate permission decisions.
- `apm import-copilot --source PATH [--project PATH]`: import Copilot SKILL.md assets as APM skills.
- `apm bundle list --bundle PATH`: inspect members of an exported APM bundle archive.
- `apm bundle export --name INSTALLED_NAME ... [--output PATH] [--project PATH]`: export installed skills into a bundle archive.
- `apm bundle install --bundle PATH [--project PATH]`: install all members from a bundle archive and record bundle membership in `apm.lock`.

## Make targets

- `make install`: install package with dev dependencies.
- `make lint`: run Ruff checks.
- `make format`: run Ruff formatter.
- `make typecheck`: run mypy.
- `make test`: run pytest.
- `make verify`: lint + typecheck + tests.
- `make build`: build wheel/sdist and run Twine check.
- `make docs-build`: build static MkDocs site into `site/`.
- `make docs-serve`: run local MkDocs preview server.
- `make publish-testpypi`: publish with Twine to TestPyPI.
- `make publish-pypi`: publish with Twine to PyPI.

## Docs Hosting

- GitHub Pages deploy workflow: `.github/workflows/docs.yml`.

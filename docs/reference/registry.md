# Registry Reference

For the concepts behind these commands see [Skill & Agent Registry](../concepts/registry.md); for a walkthrough see the [Registry Guide](../guides/registry-guide.md).

All `ananke registry …` commands accept `--project PATH` (default `.`) and `--user` (use `~/.ananke/registry` instead of the project registry). Most read commands accept `--json`.

## Files and layout

```text
.ananke/registry/
├── registry.db            SQLite (WAL) — authoritative metadata
├── blobs/sha256/<aa>/…    content-addressed immutable payloads (read-only files)
├── materialized/<digest>/ extracted payloads (derived, rebuildable)
├── docs/                  generated static site (+ .build-cache.json)
├── index/  locks/         reserved
├── policy.toml            registry policy (see below)
├── events.jsonl           registry event audit log (also in SQLite)
├── cache.db               derived cache (source fingerprints, similarity vectors) — safe to delete
├── watch-state.json  pending.json      registry watch state
├── links.json (under .ananke/registry/)   dev links
├── backups/  analytics/   optional outputs
ananke.lock                project lockfile (root of the project)
.ananke/activation.toml    activation profile
.ananke/skills/{installed,active,linked}/   .ananke/agents/{installed,active}/
```

The schema is versioned (`schema_migrations`); readers refuse a newer schema than they support and a backup is copied before any migration. Schema v2 adds `artifact_signatures` (opening an older registry migrates it automatically).

Signing private keys are **not** part of the registry; the default location for `key generate` is `.ananke/secrets/registry-signing.key` (gitignored, mode `0600`).

## `ananke registry` commands

| Command | Purpose |
|---|---|
| `init [--policy default\|enterprise]` | Create the registry and write a policy file |
| `learn SOURCE [options]` | Discover a source and register new immutable versions |
| `register PATH [--version auto\|X] [--channel] [--namespace] [--license] [--dry-run]` | Register a local directory |
| `unregister REF [--purge] [--force] [--reason]` | Archive a version (default) or purge it if unreferenced |
| `inspect REF` / `inspect --source SRC [--report FILE.html]` | Full details of a version, or a pre-registration report |
| `search [QUERY] [--kind] [--runtime] [--trust] [--channel] [--capability]… [--tag] [--license] [--limit] [--all-versions] [--semantic]` | Lexical search with filters and the query DSL; `--semantic` needs `[semantic] enabled` |
| `list [REF] [--kind] [--namespace] [--versions]` | List artifacts or versions |
| `show REF` | Show an artifact (latest, or `@version`) |
| `diff A B` | Compare two versions (capabilities, permissions, schemas, dependencies, suggested SemVer) |
| `resolve REF [--runtime] [--mode] [--explain] [--allow-prerelease] [--allow-yanked] [--allow-deprecated] [--channel]…` | Resolve a requirement under policy |
| `promote REF [--channel] [--trust] [--reviewed-by] [--reason] [--run-tests] [--skip-gate]` | Promote channel/trust (runs the quality gate) |
| `yank` / `unyank` / `deprecate [--replacement]` / `quarantine` / `release-quarantine` | Lifecycle transitions (all audited) |
| `alias set\|list\|remove` | Manage aliases |
| `activate REF [--mode copy\|link]` / `deactivate REF` | Materialize + activate for the project |
| `translate REF --runtime RT` | Runtime-native descriptor |
| `lock [REFS…] [--update] [--output] [--runtime] [--mode]` / `verify-lock [PATH]` | Write / verify `ananke.lock` |
| `publish PATH [--version] [--channel] [--namespace] [--license] [--run-tests] [--dry-run]` | validate → test → hash → dependencies → conflict/policy → register → docs |
| `link PATH` / `unlink REF` | Dev-mode working copies (never enter the immutable registry) |
| `evidence [--run-dir DIR]` | Capability versions in play, from `ananke.lock` |
| `verify [--fast]` / `doctor` | Integrity and operational checks |
| `export [-o FILE] [--compression auto\|gzip\|zstd]` / `import ARCHIVE [--dry-run] [--skip-policy] [--reset-trust]` | Portable archive |
| `backup [--output-dir]` / `restore ARCHIVE --into DIR [--force]` | Filesystem-level backup |
| `gc [--apply] [--grace-days N]` | Delete unreachable blobs (dry run by default) |
| `rebuild-index` | Rebuild the FTS5 projection |
| `snapshot [--note]` | Persist the current snapshot id |
| `events [--limit] [--type PREFIX]` | Immutable audit log |
| `report NAME [--format table\|json\|csv] [--days N]` | `inventory licenses trust stale deprecated unused security compatibility` |
| `analytics build` | JSONL projections (+ `registry.duckdb` if DuckDB is installed) |
| `analytics query [QUESTION] [--days N] [--engine auto\|duckdb\|sqlite]` | Built-in questions: `approved-skills-per-runtime` `agents-on-deprecated` `network-skills` `not-verified-recently` `locked-versions` `bundle-licenses` |
| `key generate [-o FILE] [--trust] [--signer] [--force]` / `key trust ID --public-key B64` / `key revoke ID` / `key list` | Ed25519 signing keys (only public keys enter policy) |
| `sign REF --key FILE [--key-id] [--signer]` / `signatures REF` | Sign a version; show each signature's *computed* status |
| `remote list` / `remote search Q [--source]` / `remote pull REF [--source] [--no-deps] [--dry-run] [--allow-network]` | Pull-only federation |
| `benchmark [--size N] [--iterations N] [--only OP]… [--save-baseline F] [--baseline F] [--tolerance X]` | Spec §132/§170 benchmarks with regression tracking |
| `duplicates` | Potential duplicates (payload, source id, tool schemas, capability set) |
| `recommend CAPABILITY` | Policy-eligible skills providing a capability |
| `watch [--once] [--interval] [--backend auto\|native\|poll]` | Detect changes under `.ananke/skills`/`agents` (`native` = OS events via `registry-watch`) |
| `schema [NAME] [--output DIR]` | Publish JSON Schemas |
| `policy show\|init [--preset]` | Inspect or write the policy |
| `docs build [--output] [--single-file] [--full]` / `docs dump -o FILE` | Static site / single-file dump |
| `serve [--host] [--port] [--token-env NAME]` | Read-only server (docs + JSON API); a bearer token is required off loopback |

### `learn` options

`--kind`, `--namespace`, `--name`, `--version X|auto`, `--license SPDX`, `--channel`, `--allow-dynamic`, `--allow-network`, `--plugin module:function`, `--plugin-path DIR`, `--dry-run`, `--force`, `--interactive`, `--json`. Source forms:

```text
./path                       manifest or generic layout (or a directory of them)
python:PKG | python:./dir    Python distribution / project (static metadata only)
rust:./crate                 Cargo crate (+ ananke.registry.json)
mcp:tools-list.json          saved MCP tools/list response
mcp-stdio:"<command …>"      launches a local process   (needs --allow-dynamic)
mcp-http:https://host/mcp    remote server              (needs --allow-network)
git:URL[#ref] | git:./repo   git (remote needs --allow-network)
archive.tar.gz | .zip        archives, safely extracted
framework:NAME:./src         AST scan: pydantic-ai, microsoft-agent-framework, langgraph, crewai, generic-python
dynamic:NAME:./src           sandboxed introspection plugin (needs --allow-dynamic and --plugin)
```

Exit codes: `0` success, `1` generic failure (incomplete/conflict/denied candidates, resolution failure), `2` unknown/invalid reference or registry not initialised, `3` policy blocked (incl. secrets, quarantine), `4` verification/integrity failure, `6` remote source unsupported.

### Query DSL

```text
kind:skill capability:graph.query runtime:pydantic trust:approved graph review
```

Filters: `kind`, `capability` (hierarchical prefix: `graph` matches `graph.query`), `runtime`, `trust`, `channel`, `license`, `tag`, `namespace`, `lifecycle`. Remaining words are free text (name > tags > capabilities > summary > description).

### References and version requirements

| Form | Meaning |
|---|---|
| `ananke://skill/core/graph-review@2.1.3` | Full URI |
| `core/graph-review@^2` | Namespace/name with requirement |
| `graph-review` | Alias or unique bare name |
| `=1.2.3` / `1.2.3` | Exact pin |
| `^1.4`, `~2.3` | Caret / tilde (`^0.2.3` → `<0.3.0`; upper bounds exclude pre-releases of the next version) |
| `>=1.5,<2` | Comma- or space-separated comparators (`>`, `>=`, `<`, `<=`, `!=`) |
| `1`, `1.2`, `1.x` | Wildcard ranges |
| `latest`, `stable`, `approved` | Symbolic — resolved through policy (`stable` = stable channel, `approved` = approved trust) |

## `ananke skill …` / `ananke agent …`

Kind-scoped shortcuts: `list [--versions]`, `show`, `search`, `register`, `resolve [--explain]`, `versions`, `activate`. `ananke agent search` additionally accepts `--skill`, `--runtime`, `--capability` to find agents that compose a skill or provide a capability.

## `ananke sync`

Resolves the project's declared requirements and writes `ananke.lock`:

```toml
# pyproject.toml
[tool.ananke.agent]
name = "engineering/coding-agent"
version = "^3"

[tool.ananke.skills]
"core/graph-review" = "^2"

[tool.ananke.overrides]
"core/graph-review" = { path = "../graph-review-dev" }   # dev only → lock marks source="path", dirty=true
```

Options: `--lock PATH`, `--update`, `--runtime`, `--mode`, `--activate`, `--release` (forbid overrides/dev links), `--dry-run`. Requirements from `apm install REF` are included automatically. Overrides can also live in `.ananke/registry/overrides.toml`.

## APM additions

| Command | Behaviour |
|---|---|
| `apm search QUERY` | Registry search (`--kind`, `--runtime`, `--capability`, `--json`) |
| `apm info [REF]` | Registry details for `REF` (policy-resolved); without `REF`, the legacy `apm.lock` |
| `apm install REF [--activate] [--allow-prerelease] [--runtime]` | Resolve + materialize the whole dependency graph; only the requested ref is recorded as a root requirement. `apm install --source PATH` (legacy) is unchanged |
| `apm activate REF` | Activate an exact version or resolved range. `apm activate --name NAME` (legacy) is unchanged |
| `apm lock [--update] [--output]` | Same as `ananke sync` for APM-installed requirements |
| `apm upgrade [--dry-run]` | Re-resolve to newer compatible versions and refresh installs (activated ones stay activated) |
| `apm link PATH` / `apm unlink REF` | Dev mode |
| `apm publish PATH [--version] [--channel] [--run-tests] [--dry-run]` | Publish pipeline |

Installed registry artifacts are laid out as `<namespace>.<name>@<version>` under `.ananke/skills/installed/`, so `apm list` shows them.

## Manifest reference (`ananke.toml` / `.yaml` / `ananke.registry.json`)

```yaml
kind: skill                       # skill | agent | tool | workflow | evaluator | prompt | policy | bundle | runtime-profile
namespace: core
name: graph-review
version: 2.1.0
summary: Reviews source changes using code-graph blast radius.
description: |
  Longer Markdown description.
ananke_id: urn:example:graph-review      # optional explicit identity (used for identity matching)

capabilities: [graph.query, graph.blast_radius, architecture.read]   # hierarchical ids
runtime:
  supported: [pydantic, microsoft, generic-mcp]
  provider: pydantic                     # agents
model_requirements: {tool_calling: true, structured_output: true}

inputs:  {schema: schemas/input.json}    # JSON Schema (canonical); paths are resolved inside the payload
outputs: {schema: schemas/output.json}
tools:
  - {name: find, description: "…", input_schema: schemas/find.json}

permissions:
  filesystem: {read: ["**"], write: []}
  shell: {allow: [pytest]}
  network: []                            # or true / [host, …]

dependencies:
  - skill: core/repo-context             # shorthand for type: artifact
    version: "^1.4"
  - {type: python-package, name: httpx, version: ">=0.28"}
  - {type: system-binary, name: git, version: ">=2.40"}
  - {type: mcp-server, name: github}
  - {type: model-capability, name: tool_calling}

# agents
skills: [{ref: core/graph-review, version: "^2.0"}]
policies: [safe-coding]                  # slash-qualified refs (org/policy) become dependencies
evaluation: {suite: coding-agent-standard}
instructions: {ref: prompts/coding-agent.md}

compatibility:
  ananke: ">=1.2,<2"
  python: ">=3.11"
  runtimes: {pydantic: ">=1"}
  operating_systems: [linux, macos]
  architecture: [x86_64, arm64]
license: {expression: Apache-2.0}
metadata: {tags: [graph, review]}
owners: [{team: ai-platform}, {user: alice}]
maintainers: [alice, bob]
```

Unknown fields are rejected (typos are errors, not silence). Capability ids should follow the taxonomy `filesystem.read`, `filesystem.write`, `shell.execute`, `network.http`, `graph.query`, `graph.write`, `architecture.read`, `architecture.validate`, `spec.read`, `spec.write`, `git.commit`, `git.push`, `scm.pull_request.create`, `issue.read`, `issue.update`, `eval.run`, `test.run` (custom ids are allowed with an info diagnostic; declaring `filesystem.write`/`shell.execute`/`network.http` without the matching permission raises a warning).

Registrable only if: valid identity and SemVer, payload hash computed, schemas valid, dependency declarations parse and do not form a cycle, no path traversal, no secrets (per policy), licence known or explicitly allowed unknown, provenance present, permissions parse, manifest schema valid.

Publish JSON Schemas for IDE completion with `ananke registry schema --output schemas/` (`skill-manifest`, `agent-manifest`, `bundle-manifest`, `registry-export`, `lockfile`).

## Policy (`.ananke/registry/policy.toml`)

```toml
default_namespace = "local"        # "" = none required (enterprise)
strict_semver = false
require_signature = false          # promotion/resolution need a signature verified by a trusted key
auto_register = false              # registry watch never publishes unless true
activation_mode = "copy"           # copy | link

[resolve]
mode = "highest-compatible"        # highest-approved for enterprise
minimum_trust = "discovered"       # approved for enterprise
channel = ["stable", "candidate", "approved"]
allow_prerelease = false
allow_deprecated = false
allow_restricted = false
allow_yanked_when_locked = true
require_quality = []               # ["tests", "evaluation"]

[licenses]
allow = []                         # empty = any known licence (SPDX AND/OR/WITH supported)
deny = []
allow_unknown = true

[permissions]
forbid = []                        # e.g. ["shell.execute"]
[permissions.network]
require_review = false             # promotion needs --reviewed-by

[approval]
minimum_trust = "approved"
require = []                       # provenance checksum tests vulnerability_scan license signature
run_tests = false

[dynamic_introspection]
allowed = false
timeout_seconds = 20
max_memory_mb = 512

[remote_sources]
public = false
enterprise = false                 # network access to [sources.enterprise] (pull-only)
allow_mcp_network = false
allow_git_remote = false
allow_cli_override = true          # enterprise preset: false (flags cannot bypass policy)

[signing.trusted_keys.ed25519-1a2b3c4d5e6f7a8b]   # public keys only; private keys never go here
public_key = "base64-of-32-raw-bytes"
signer = "release-bot"
revoked = false

[semantic]
enabled = false                    # optional similarity search
provider = "local-hash"            # or an ananke.registry.embedders entry point
allow_remote = false               # remote embedders send text off-machine

[sources.enterprise]
type = "remote"
url = "https://registry.example.internal"
token_env = "ANANKE_REGISTRY_TOKEN"   # env var NAME, never the token

[namespaces.core]
publishers = ["ananke-maintainers"]   # protected namespace

[secrets]
mode = "reject"                    # reject | redact | warn

[telemetry]
local_usage = false                # opt-in, never leaves the machine
```

`ananke registry policy init --preset enterprise` writes: `HighestApproved`, approved-only, `stable` channel, licence allow-list (Apache-2.0/MIT/BSD-3-Clause), unknown licences denied, network permissions need review, promotion requires provenance/checksum/tests/vulnerability scan/licence, strict SemVer, no default namespace, CLI flags cannot override dynamic/network gates.

## Python API

```python
from ananke.plexus import Ananke
from ananke.plexus.registry import Registry, Resolver, Requirement, ResolutionEnvironment

registry = Ananke.open(".").registry            # or Registry.for_project(".", create=True)

registry.skills.register("./skills/graph-review")
skill = registry.skills.get("core/graph-review", "^2")           # policy-aware resolve
agent = registry.agents.get("engineering/coding-agent", "stable")

result = Resolver(registry, env=ResolutionEnvironment.detect(runtime="pydantic")).resolve(
    Requirement.parse("core/graph-review@^2")
)
print(result.explain())            # spec-style explanation; result.nodes is the solved graph

registry.promote("core/graph-review@2.1.3", trust="approved", channel="stable")
hits = registry.search("kind:skill capability:graph.query graph review")
registry.export_archive("registry.tar.gz"); registry.build_docs(); registry.dump_docs("registry.html")
```

Key types: `Registry` (registration, lifecycle, aliases, snapshots, `sign`/`signatures`/`is_signed`/`add_signature`, delegating helpers), `RemoteRegistry`/`pull()`, `answer_question()`, `run_benchmarks()`, `Resolver`/`ResolutionResult` (`ok`, `selected`, `nodes`, `explain()`), `ResolverRule`/`RuleOutcome` (extension point), `learn()`/`inspect_source()`, `sync()`, `publish()`, `activate()`, `QualityGate`, `verify()`/`doctor()`, `export_registry()`/`import_registry()`, `build_site()`/`build_dump()`. Errors derive from `RegistryError` and carry a stable `.code` (`VERSION_CONTENT_CONFLICT`, `POLICY_VIOLATION`, `SECRET_DETECTED`, `DEPENDENCY_CYCLE`, `INVALID_TRUST_TRANSITION`, `QUALITY_GATE_FAILED`, `NETWORK_NOT_APPROVED`, `DYNAMIC_INTROSPECTION_DISABLED`, `RESOLUTION_FAILED`, `NOT_FOUND`, …).

## HTTP API (`ananke registry serve`)

Read-only; `POST/PUT/PATCH/DELETE` return `405 READ_ONLY`. With `--token-env`, every request needs `Authorization: Bearer <token>` (`401` otherwise); binding anywhere but loopback without a token is refused. Security headers (`CSP`, `nosniff`, `no-store`) on every response.

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | Registry id and snapshot |
| `GET /api/v1/artifacts[?kind=]` | List artifacts |
| `GET /api/v1/artifacts/{kind}/{namespace}/{name}` | Latest version detail |
| `GET /api/v1/artifacts/{kind}/{namespace}/{name}/versions` | All versions |
| `GET /api/v1/search?q=&kind=&capability=&runtime=&trust=&channel=` | Search |
| `GET /api/v1/resolve?ref=&runtime=&mode=` | Resolution with explanation |
| `GET /api/v1/capabilities` | Capability index |
| `GET /api/v1/bundle?uri=…` (repeatable, ≤ 50) | Portable archive of exact versions (used by `remote pull`; quarantined versions refused) |
| `GET /…` | Generated static docs |

## MCP

| Tool | Arguments |
|---|---|
| `registry_search` | `query`, `kind`, `capability`, `runtime`, `trust`, `channel`, `limit`, `semantic` |
| `registry_get_skill` / `registry_get_agent` | `ref`, `version` |
| `registry_resolve` | `ref`, `runtime`, `mode`, `kind`, `version` |
| `registry_compare_versions` | `a`, `b` |
| `registry_list_capabilities` | — |

Resources: `ananke://registry/skills`, `ananke://registry/agents`, `ananke://registry/skill/<ns>/<name>/<version>`, `ananke://registry/agent/<ns>/<name>/<version>`.

## Events

Registry mutations append immutable audit events (`artifact.registered`, `artifact.yanked`, `artifact.deprecated`, `artifact.quarantined`, `trust.approved`, `alias.updated`, `activation.changed`, `lock.created`, …) and publish matching Ananke event-bus events (`registry.version.registered`, `registry.version.yanked`, `registry.trust.changed`, `registry.activation.changed`, `registry.lock.generated`, `registry.docs.generated`, …). `.ananke/registry/events.jsonl` mirrors the bus.

## Extras

| Extra | Adds |
|---|---|
| `registry-fast` | `blake3` — records BLAKE3 alongside SHA-256 |
| `registry-zstd` | `zstandard` — `tar.zst` exports/backups on Python < 3.14 |
| `registry-analytics` | `duckdb` — optional `registry.duckdb` projection and DuckDB analytics engine |
| `registry-signing` | `cryptography` — Ed25519 *signing* (verification needs nothing) |
| `registry-watch` | `watchfiles` — native file events (Rust `notify`) for `registry watch` |
| `registry-validate` | `jsonschema` — full JSON-Schema validation (a structural check is used otherwise) |
| `registry` | `registry-fast` + `registry-zstd` + `registry-validate` + `registry-signing` + `registry-watch` |

The registry works without any extra.

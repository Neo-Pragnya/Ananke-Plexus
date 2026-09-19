# Registry Guide

A hands-on tour of the skill & agent registry. Everything here runs locally and offline.

## 1. Create a registry and learn your first skill

```bash
cd my-project
ananke registry init                       # SQLite + content-addressed store + policy.toml
```

A skill is a directory with an `ananke.yaml` (or `.toml`) manifest:

```text
skills/graph-review/
├── ananke.yaml
├── README.md
├── prompts/review.md
├── schemas/input.json
└── tests/test_review.py
```

```yaml
# skills/graph-review/ananke.yaml
kind: skill
namespace: core
name: graph-review
version: 1.0.0
summary: Reviews changes using code-graph blast radius.
capabilities: [graph.query, architecture.read]
runtime: {supported: [pydantic, generic-mcp]}
license: {expression: Apache-2.0}
inputs: {schema: schemas/input.json}
permissions:
  filesystem: {read: ["src/**"]}
```

```bash
ananke registry learn ./skills/graph-review
```

```text
                                learn ./skills/graph-review
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ action     ┃ artifact                ┃ version ┃ note                                              ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ registered │ skill/core/graph-review │ 1.0.0   │ registered ananke://skill/core/graph-review@1.0.0 │
└────────────┴─────────────────────────┴─────────┴───────────────────────────────────────────────────┘
```

Point `learn` at a **directory of skills** and it registers each one. Directories that are *not* Ananke-native (a `SKILL.md`, `skill.yaml`, `manifest.yaml`, `AGENT.md`…) are recognised too, and everything that can't be inferred is reported instead of guessed:

```bash
ananke registry learn ./external-skills --dry-run          # discover only
ananke registry learn ./external-skills --namespace team --license MIT
ananke registry inspect --source ./external-skills --report inspect.html   # human-reviewable HTML report
```

## 2. Inspect, search, compare

```bash
ananke registry show core/graph-review
ananke registry list core/graph-review            # versions table
ananke registry search "graph review" --capability graph.query --trust approved
ananke registry search "kind:skill runtime:pydantic review"

# after publishing 1.1.0 …
ananke registry diff core/graph-review@1.0.0 core/graph-review@1.1.0
```

```text
Capabilities:
  + graph.reverse_dependencies
Permissions:
  unchanged
Inputs:
  backward compatible
Suggested semver:
  MINOR
```

**Permission expansion is flagged as security-significant even when the API is compatible.**

Changed a skill without bumping its version? `learn` refuses (`VERSION_CONTENT_CONFLICT`), shows what changed, and suggests the bump. `--version auto` applies it:

```bash
ananke registry learn ./skills/graph-review --version auto
```

## 3. Promote, then resolve

New versions start as `discovered` in the `candidate` channel. Promote after review — the quality gate runs first:

```bash
ananke registry promote core/graph-review@1.0.0 --trust approved --channel stable
# network-using skills need a named human reviewer when policy requires it
ananke registry promote core/net-tool@1.0.0 --trust approved --reviewed-by alice
# execute the skill's own tests as part of the gate (runs untrusted code — explicit opt-in)
ananke registry promote core/graph-review@1.0.0 --trust approved --run-tests
```

```bash
ananke registry resolve core/graph-review@^1 --runtime pydantic --explain
```

The explanation lists why each candidate was accepted or rejected, then the resolved dependency graph.

## 4. Agents as dependency graphs

```yaml
# agents/coding-agent/ananke.yaml
kind: agent
namespace: engineering
name: coding-agent
version: 3.2.0
runtime: {provider: pydantic, supported: [pydantic]}
model_requirements: {tool_calling: true}
skills:
  - {ref: core/graph-review, version: "^1"}
  - {ref: core/test-runner,  version: "^3"}
instructions: {ref: prompts/coder.md}
```

```bash
ananke registry learn ./agents/coding-agent
ananke registry resolve engineering/coding-agent@^3 --explain     # solves the whole graph
ananke agent search --skill graph-review --runtime pydantic
```

## 5. Lock, activate, and use from APM

Declare what the project consumes:

```toml
# pyproject.toml
[tool.ananke.agent]
name = "engineering/coding-agent"
version = "^3"
```

```bash
ananke sync                      # resolves and writes ananke.lock (reproducible)
ananke sync --activate           # also materializes + activates everything locked
#   Wrote ananke.lock
#     + ananke://agent/engineering/coding-agent@3.2.0
#     + ananke://skill/core/graph-review@1.0.0
#     activated ananke://skill/core/graph-review@1.0.0
ananke registry verify-lock      # every entry exists, digest matches, nothing quarantined

# APM is the package-manager UX over the same services
apm search graph
apm install core/graph-review@^1 --activate
apm info core/graph-review
apm upgrade --dry-run            # what would change within your constraints?
```

`registered ≠ installed ≠ activated`: `apm install` materializes into `.ananke/skills/installed/`, activation adds the `active/` marker and updates `.ananke/activation.toml`.

## 6. Publishing your own skill

```bash
apm publish ./skills/graph-review --dry-run
apm publish ./skills/graph-review --version auto --channel beta
```

```text
✓ validate: ananke://skill/core/graph-review
✓ test: skipped (use --run-tests to execute the skill's own tests)
✓ hash: sha256:9c1f…
✓ resolve-dependencies: all dependencies registered
✓ conflict-and-policy: no conflicts; policy accepts
✓ register: ananke://skill/core/graph-review@1.0.1
✓ docs: incremental rebuild if docs exist
```

## 7. Develop against a working copy

```bash
apm link ../graph-review-dev          # or: ananke registry link …
ananke sync                            # lock marks it  source = "path", dirty = true
ananke sync --release                  # …and a release profile refuses dirty locks
apm unlink core/graph-review
```

Linked skills never enter the immutable registry. `ananke registry watch` detects changes under `.ananke/skills` and `.ananke/agents` and lists pending registrations, but publishing stays manual (unless `auto_register = true`).

## 8. Import from other ecosystems

```bash
ananke registry learn python:my-agent-package                # entry points ananke.skills / ananke.agents
ananke registry learn mcp:github-tools.json --license MIT    # saved tools/list response
ananke registry learn rust:./graph-crate --kind skill
ananke registry learn framework:pydantic-ai:./src --namespace team --license MIT   # AST scan, no execution
ananke registry learn git:./local-repo
```

Anything that runs code or touches the network is opt-in:

```bash
ananke registry learn mcp-stdio:"python my_server.py" --allow-dynamic
ananke registry learn mcp-http:https://mcp.example.com/mcp --allow-network
ananke registry learn dynamic:crewai:./src --allow-dynamic --plugin myplugin:introspect --plugin-path ./plugin
```

The dynamic path runs in an isolated subprocess (no network, scrubbed environment, resource limits, timeout, JSON-only output). Treat it as defence in depth, not a container.

## 9. Docs for humans

```bash
ananke registry docs build                  # .ananke/registry/docs/  (multi-page, incremental)
ananke registry docs dump -o registry.html  # ONE offline file: search, diagrams, comparisons, matrix
ananke registry serve                       # optional read-only server on http://127.0.0.1:8765
```

Open `docs/index.html` directly — no server needed. Each artifact page has a **Latest approved / Latest stable / All versions** selector, immutable pages per version, a version-history table with **BREAKING** markers and **compare** links.

## 10. Enterprise setup

```bash
ananke registry policy init --preset enterprise     # HighestApproved, approved-only, allow-listed licences
```

Then edit `.ananke/registry/policy.toml`, for example:

```toml
[namespaces.core]
publishers = ["ananke-maintainers"]      # only these actors may publish into core/*

[permissions]
forbid = ["shell.execute"]

[approval]
require = ["provenance", "checksum", "tests", "vulnerability_scan", "license", "signature"]
```

Feed scan results into the registry so the gate and resolver can act on them (critical findings quarantine a version when `vulnerability_scan` is required):

```python
from ananke.plexus.registry.models import Security
registry.update_security("core/graph-review@1.0.0",
    Security(status="clean", scanner="trivy", last_scanned_at="2026-09-19T00:00:00Z"))
```

Operate it:

```bash
ananke registry verify && ananke registry doctor
ananke registry report licenses --format csv
ananke registry report deprecated            # deprecated/yanked versions and who depends on them
ananke registry export -o registry.tar.gz    # share with another team/registry
ananke registry import registry.tar.gz --dry-run
ananke registry backup && ananke registry gc  # gc is a dry run until --apply
```

## 11. Signing and verifying

```bash
pip install "ananke-plexus[registry-signing]"     # signing only; verifying needs nothing
ananke registry key generate --trust             # private key → .ananke/secrets/registry-signing.key (0600)
ananke registry sign core/graph-review@1.0.0 --key .ananke/secrets/registry-signing.key
ananke registry signatures core/graph-review@1.0.0
```

```text
                                  signatures
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ key                       ┃ signer ┃ verified ┃ detail                                 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ed25519-70762b01870c54ae  │        │ yes      │ valid signature from a trusted key     │
└───────────────────────────┴────────┴──────────┴────────────────────────────────────────┘
```

Set `require_signature = true` in policy and only versions with a signature from a trusted, unrevoked key can be promoted or resolved. `ananke registry key revoke <id>` invalidates everything that key signed, immediately. Signatures made elsewhere (for example in CI) can be attached with the Python API `registry.add_signature(...)`; their trust is always recomputed against *your* keys.

## 12. Sharing with other registries

```bash
# on the serving side (bearer token required off loopback)
export ANANKE_REGISTRY_TOKEN=...
ananke registry serve --host 0.0.0.0 --token-env ANANKE_REGISTRY_TOKEN

# on the consuming side: policy.toml → [remote_sources] enterprise = true, [sources.enterprise] url/token_env
ananke registry remote list
ananke registry remote pull core/coding-agent@^2 --dry-run
ananke registry remote pull core/coding-agent@^2
ananke registry promote core/coding-agent@2.0.0 --trust approved --channel stable   # trust is local
```

## 13. Finding things by similarity, and asking questions

```bash
# policy.toml: [semantic] enabled = true
ananke registry search "secrit scaner" --semantic       # tolerates typos and word forms

ananke registry analytics query                          # list the built-in questions
ananke registry analytics query agents-on-deprecated
ananke registry analytics query not-verified-recently --days 30
```

## 14. Recording what ran

If `ananke.lock` exists, every evidence bundle includes `registry-capabilities.json`: the agent, each skill's URI, exact version and payload digest, the registry snapshot and the lockfile hash — enough to reconstruct the capability graph of any run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[REGISTRY_NOT_INITIALIZED]` | `ananke registry init` |
| `[VERSION_CONTENT_CONFLICT]` | Bump the version (`--version auto`) — published versions are immutable |
| `[SECRET_DETECTED]` | Remove the credential; or set `[secrets] mode = "redact"` deliberately |
| `[DEPENDENCY_CYCLE]` | Break the cycle; registration rejects graphs that depend on themselves |
| `resolve` fails with `trust status = discovered` | Promote the version, or lower `resolve.minimum_trust` |
| `[QUALITY_GATE_FAILED]` | Read the failing checks; satisfy them (e.g. `--reviewed-by`, tests, scan) |
| `search-index` check fails in `verify` | `ananke registry rebuild-index` |
| `[SIGNING_UNAVAILABLE]` | `pip install ananke-plexus[registry-signing]` (only needed to *sign*) |
| `[KEY_PERMISSIONS]` | `chmod 600` the private key file |
| `[SIGNATURE_INVALID]` | The signature does not match this version's URI + digest (or the key file is wrong) |
| `[NETWORK_DENIED]` | Enable `[remote_sources]` for that source in policy, or pass `--allow-network` (unless overrides are disabled) |
| `[SEMANTIC_DISABLED]` | Set `[semantic] enabled = true` in policy |
| `.tar.zst` unsupported | `pip install ananke-plexus[registry-zstd]` or use gzip (`--compression gzip`) |

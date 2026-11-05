# Configuration Reference

## Config files

| File | Purpose | Committed |
|---|---|---|
| `.ananke/config.toml` | Project configuration | ✅ |
| `.ananke/config.local.toml` | Local overrides | ❌ (gitignored) |
| `.ananke/secrets/adapters.env` | Credential store | ❌ |
| `.ananke/registry/policy.toml` | Registry policy (trust, licences, secrets, network/dynamic gates, resolver defaults) — see [Registry Reference](registry.md#policy-anankeregistrypolicytoml) | ✅ |
| `.ananke/registry/overrides.toml` | Local path overrides for `ananke sync` | ✅ |
| `.ananke/activation.toml` | Active skills/agents profile written by activation | ✅ |
| `ananke.lock` | Deterministic resolved-capability lockfile | ✅ |
| `pyproject.toml` `[tool.ananke.agent]`, `[tool.ananke.skills]`, `[tool.ananke.overrides]` | Capabilities the project consumes | ✅ |
| `.ananke/registry/registry.sqlite3`, `blobs/` | Registry database and content-addressed store | ❌ (generated) |

## Precedence (highest wins)

1. CLI flags
2. Environment variables
3. `.ananke/config.local.toml`
4. `.ananke/config.toml`
5. User config (`~/.config/ananke/config.toml`)
6. Built-in defaults

## Complete config reference

```toml
[project]
name = "my-service"
default_branch = "main"
package_ecosystems = ["python"]

[ananke]
mode = "developer"      # developer | ci | strict
fail_closed = true      # hard gate default
offline = false         # disable network adapters

[spec]
provider = "native"     # native | speckit
contract_mode = "ananke-bmad"

[architecture]
provider = "calm"
schema_version = "pinned"
strict = true

[graph]
providers = ["native"]          # native | graphifyy | code-review-graph
primary = "native"

[backend]
default = "fake"                # fake | copilot | amazon-q | kiro | hermes

[lifecycle]
issue_tracker = "jira"
scm = "bitbucket"               # bitbucket | github

[hooks]
mode = "native"                 # native | pre-commit-framework | delegated | disabled

[telemetry]
enabled = true
exporter = "console"            # console | otel
redact_prompts = true

[security]
allow_network = false
secret_scan = true
sast = true
sca = true
license_scan = true
```

## Capability sources

`[capability_sources.<id>]` adds a source to the [provider port](../guides/capability-sources.md):

| Key | Default | Meaning |
| --- | --- | --- |
| `type` | `local` | `local` (an approved folder) or `git` (a pinned repository) |
| `root` | `""` | `local`: the folder, relative to the project or absolute. `git`: a subfolder in the repository |
| `namespace` | required | the namespace every capability from this source gets |
| `url` | `""` | `git`: the repository URL, without credentials |
| `commit` | `""` | `git`: a full 40-character commit sha |
| `credential_env` | `""` | `git`: the *name* of the environment variable holding a token |
| `writable` | `false` | allow reviewed publishes into this source |
| `visibility` | `internal` | `internal` or `public`; internal lessons never reach a public source |
| `enabled` | `true` | switch the source off without deleting it |

## Secret references

Secrets MUST be referenced, not embedded:

```toml
[jira]
base_url = "https://example.atlassian.net"
token = { env = "ANANKE_JIRA_TOKEN" }

[bitbucket]
token = { cmd = "op read op://vault/bitbucket/token" }

[my-service]
api_key = { keychain = "ananke-my-service-key" }
```

Reference types:
- `{ env = "VAR_NAME" }` — read from environment variable
- `{ cmd = "command args" }` — execute command, use stdout
- `{ keychain = "service-name" }`, optionally with `account = "name"`: read that one entry from the OS keychain (macOS Keychain, Windows Credential Manager or the Secret Service on Linux) through `keyring`. Install it with `pip install 'ananke-plexus[keychain]'`

## CLI global flags

These flags apply to all commands:

```
--project PATH      Repository root (default: .)
--config PATH       Config file override
--offline           Disable all network adapters
--json              Machine-readable JSON output
--quiet             Suppress non-essential output
--verbose           Verbose output
--no-color          Disable color
--non-interactive   Disable interactive prompts
```

## Config migration

```bash
# Analyze config for migration issues
ananke config migrate

# Apply migration changes
ananke config migrate --apply
```

Ananke never silently rewrites unknown config keys.

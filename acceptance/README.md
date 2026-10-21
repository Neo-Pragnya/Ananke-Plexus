# Capability acceptance suite

Behaviour-level checks for everything Ananke Plexus promises, run against the real CLI before every release.
Unit tests (`tests/`) prove the parts; this suite proves the product: install it, point it at a project, use each
capability the way a person or an agent would, and decide whether the build may ship.

The design follows the agent-evaluation harness from the WWI Data Science Agent course (Week 12):

- **Expected behaviour, not snapshots.** Scenarios state what must be true (a drifted spec fails validation, a
  leaked token is refused, a quarantined version is never selected). They don't replay recorded output.
- **Attack probes have legitimate twins.** Every `@adversarial` scenario sits next to a clean case that must still
  work, so a guard that blocks everything also fails.
- **A release gate with evidence.** Scenario results roll up to capabilities, and capabilities roll up to one
  decision: **PROMOTE**, **CONDITIONAL** or **BLOCK**. Every rule cites the scenarios behind it.

## Run it

```bash
make acceptance            # against the dev environment (uv run), ~1.5 min with -n auto
make acceptance-wheel      # build the wheel, install it in a fresh venv, run the suite from there (what ships)
make acceptance ACCEPTANCE_ARGS="-k registry"           # a slice
make acceptance ACCEPTANCE_ARGS="--acceptance-require=promote"  # fail unless the gate says PROMOTE
```

Reports land in `acceptance/report/`: `acceptance.json` (machine-readable) and `summary.md` (also written to the
GitHub job summary in CI). CI runs `make acceptance-wheel` on every push and PR (`.github/workflows/acceptance.yml`).
The release workflow runs the gate on the **exact wheel it is about to publish**, and publishing waits for it.

## How a scenario runs

Each scenario gets a sandbox: a fresh git project (from `fixtures/projects/`), its own `HOME`, no colour and wide
columns. Commands run through `bash -c` with the target's `bin/` first on `PATH`, so `ananke`, `apm`, `dex` and
`python` are the build under test. Set `ACCEPTANCE_BIN_DIR` to test any other install. Shell features work
(`&&`, `$(…)`, pipes), and `$PROJECT`, `$SANDBOX`, `$FIXTURES` and `$REPO` are set.

```gherkin
@cap_registry_security @adversarial
Scenario: A skill carrying a real-format credential is refused
  When I run "ananke registry learn ./skills/leaky-skill --json"
  Then it fails
  And the JSON output at "candidates[0].action" equals "denied"
  And the output contains "SECRET_DETECTED"
```

## Step vocabulary (harness.py)

| Given | When | Then |
| --- | --- | --- |
| `a fresh project` · `a fresh project from fixture "x"` · `an empty directory` | `I run "cmd"` | `it succeeds` · `it fails` · `it fails cleanly` (no traceback, no ✓) · `the exit code is N` · `it does not crash` |
| `the fixture "x" is copied to "dest"` | `I run "cmd" with input:` (docstring = stdin) | `the output contains "t"` · `… does not contain` · `… matches "regex"` · `the output contains each of:` |
| `the file "p" with content:` | `I run:` (docstring = script) | `the file "p" exists` / `does not exist` / `contains "t"` · `a file matching "glob" exists` |
| `I have run "cmd"` (must succeed) | | `the JSON output at "a.b[0].c" equals V` / `contains V` / `has at least N items` · `the JSON output has at least N items` |
| `the environment variable "K" is "V"` · `the changes are committed` | | `the JSON file "glob" at "path" equals V` · `it finishes within N seconds` · `the path "$SANDBOX/x" does not exist anywhere` |

JSON paths: `a.b`, `[0]`, `length`. On a list, `a.name` maps over the items.

## The gate

`capabilities.yaml` lists each capability, whether it is a **blocker**, and the CLI surface. A capability is:

| Status | Meaning |
| --- | --- |
| ✅ pass | at least one scenario passed and none failed |
| ❌ fail | a scenario failed |
| ⚠️ known_issue | a `@known_issue` scenario (the *correct* behaviour of something currently broken) is still failing |
| ❔ unverified | its scenarios were all skipped (for example no network) |
| ⬜ missing | no scenario proves it yet |

**BLOCK** when any blocker fails or has no scenario. **CONDITIONAL** when anything else is short of pass, or CLI
coverage is below `surface_target`. **PROMOTE** otherwise. `@known_issue` scenarios run as strict xfails: when the
bug is fixed they start passing, the suite fails, and the tag must be removed. The gate never quietly forgets them.

## Adding a scenario

1. Pick (or add) the capability in `capabilities.yaml`. Mark it `blocker: true` only if a release must never ship
   without it.
2. Write the scenario in the matching `features/NN_*.feature` file and tag it `@cap_<id>`.
3. Probe the real command first and assert **behaviour** (exit codes, files, JSON fields, refusals), not wording
   that is free to change.
4. If the product is wrong, still write the correct expectation and tag it `@known_issue`. Then file the bug.

## Found by this suite (all fixed in 0.3.6)

The first runs, against a mature package with about 1,800 unit tests, found:

| What the scenario expected | What was wrong |
| --- | --- |
| A standard MCP client can initialise and list tools | `serve-mcp` spoke a custom `{"action": …}` protocol, not MCP JSON-RPC 2.0 |
| Adapter credentials are never staged by `git add -A` | nothing git-ignored `.ananke/secrets/` or `config.local.toml` |
| An unknown trust level is a clean error / an unknown channel is refused | a `ValueError` traceback / any channel was stored |
| Auditing a non-APM file is a clean error | `apm audit` crashed |
| Run worktrees never end up in the project's git index | `.ananke/runs/<id>/worktree` was unignored |
| A run in which no test executed does not pass | `ananke test run` reported PASS |
| Failures are not reported with a success mark | `spec diff/validate`, `hooks run`, `run status` printed ✓ |
| The offline docs build within 120 s | `docs build` copied the whole checkout (about 6 minutes) |

When a new problem is found, add a `@known_issue` scenario that states the correct behaviour, and list it here.

The harness, scaffolder and probe ship as the **capability-acceptance** skill in Dex Trove, so other repos can
adopt the same suite.

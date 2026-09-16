# Policy Language Reference

## Rule schema

```toml
[[rule]]
id = "my.rule.id"          # unique rule identifier
stage = "verify"           # pre_commit | verify | pre_push | ci | release
severity = "high"          # info | low | medium | high | critical
gate = "ruff"              # gate runner to invoke
assert = "gates.ruff.exit_code == 0"   # expression to evaluate
on_failure = "block"       # block | warn
```

## Stages

| Stage | When it runs |
|---|---|
| `pre_commit` | `ananke hooks run pre-commit` |
| `verify` | `ananke verify` |
| `pre_push` | `ananke hooks run pre-push` |
| `ci` | CI pipeline |
| `release` | Release workflow |

## Expression language

Expressions evaluate dotted path lookups against gate results, config, graph state, and findings.

### Available contexts

```
config.ananke.fail_closed       — config value
gates.<gate_id>.exit_code       — gate exit code (0 = pass)
gates.<gate_id>.status          — PASS|BLOCKED|UNAVAILABLE|...
findings.count                  — total finding count
coverage.line                   — line coverage percentage
graph.domain_cycles             — number of architecture cycles
```

### Comparison operators

```
==    equals
!=    not equals
>=    greater than or equal
<=    less than or equal
>     greater than
<     less than
```

### Examples

```toml
assert = "config.ananke.fail_closed == true"
assert = "gates.ruff.exit_code == 0"
assert = "coverage.line >= 85"
assert = "findings.count == 0"
assert = "graph.domain_cycles == 0"
```

## Builtin packs

### baseline

```toml
[[rule]]
id = "policy.core.fail-closed"
stage = "verify"
severity = "high"
gate = "core"
assert = "config.ananke.fail_closed == true"
on_failure = "block"

[[rule]]
id = "security.no-secret"
stage = "pre_commit"
severity = "critical"
gate = "gitleaks"
assert = "findings.count == 0"
on_failure = "block"
```

### python-library (excerpt)

```toml
[[rule]]
id = "quality.ruff-check"
stage = "pre_commit"
severity = "medium"
gate = "ruff"
assert = "gates.ruff.exit_code == 0"
on_failure = "block"

[[rule]]
id = "tests.minimum-coverage"
stage = "verify"
severity = "medium"
gate = "pytest"
assert = "coverage.line >= 85"
on_failure = "block"
```

## Policy commands

```bash
# List all active rules
ananke policy list
ananke policy list --json

# Evaluate a stage
ananke policy check --stage verify

# Explain decisions
ananke policy explain --stage pre_commit

# Install a pack
ananke policy install-pack agentic-security
```

## Policy decision record

Every decision is recorded in `policy-decisions.jsonl`:

```json
{
  "rule_id": "security.no-secret",
  "stage": "pre_commit",
  "gate_id": "gitleaks",
  "severity": "critical",
  "status": "PASS",
  "summary": "Rule security.no-secret passed.",
  "expression": "findings.count == 0",
  "actual": "0",
  "expected": "0",
  "timestamp": "2026-09-16T..."
}
```

# Ananke BMAD — Behavior, Model, Architecture

Within Ananke Plexus, **BMAD** means:

> **Behavior — Model — Architecture Driven Development**

These represent three distinct forms of engineering truth that must all align for a change to be considered proven.

```mermaid
flowchart TB
    SPEC["📜 Specification"]
    B["🧪 Behavior<br/>What must happen?"]
    M["🧱 Model<br/>What structures exist?"]
    A["🏗️ Architecture<br/>How may they interact?"]
    IMPL["💻 Implementation"]

    SPEC --> B
    SPEC --> M
    SPEC --> A

    B --> IMPL
    M --> IMPL
    A --> IMPL

    IMPL -->|"Tests"| B
    IMPL -->|"Schema Validation"| M
    IMPL -->|"Graph Reconciliation"| A
```

---

## Compiling BMAD from a requirement

```bash
# Create a requirement with acceptance criteria
ananke spec create \
  --id PROJ-101 \
  --title "Add idempotent payment webhook" \
  --acceptance "Duplicate events are deduplicated" \
  --acceptance "Idempotency key is stored per transaction"

# Compile all BMAD artifacts
ananke bmad compile --feature-dir .ananke/specs/PROJ-101
```

This produces:

```
.ananke/specs/PROJ-101/
├── bmad.yaml                    — BMAD index
├── model-contract.yaml          — API schema skeleton
├── architecture-contract.yaml   — CALM + allow/deny rules
└── tests/
    ├── test_proj_101_behavior.py    — Failing pytest skeleton
    └── proj_101.feature             — Gherkin scenarios
```

---

## Behavior contract

Behavior is expressed through failing tests and Gherkin scenarios. Every generated test records its full traceability chain:

```
Requirement → Acceptance Criterion → Scenario → Test ID
```

Generated pytest skeleton (`tests/test_proj_101_behavior.py`):

```python
import pytest

# Traceability: PROJ-101 -> AC-1 -> test_ac_1_duplicate_events_are_deduplicated
@pytest.mark.requirement('PROJ-101')
@pytest.mark.ac('1')
def test_ac_1_duplicate_events_are_deduplicated() -> None:
    """AC-1: Duplicate events are deduplicated"""
    # TODO: implement acceptance criterion assertion
    pytest.fail('not implemented')
```

Generated Gherkin feature:

```gherkin
Feature: Add idempotent payment webhook
  # Requirement: PROJ-101

  Scenario: AC-1 — Duplicate events are deduplicated
    Given the system is in a known state
    When the condition for AC-1 is met
    Then the system satisfies the acceptance criterion
```

### Behavior principle

Never claim that an automatically generated test proves the requirement unless traceability exists. Every test records `Requirement → AC → Scenario → Test ID`.

---

## Model contract

Model contracts define API schemas, event payloads, domain value objects, and compatibility rules.

Generated `model-contract.yaml`:

```yaml
contract_id: model-proj-101
requirement_id: PROJ-101
title: Add idempotent payment webhook
schemas:
  - name: proj-101_request
    type: object
    description: API request boundary for this requirement
  - name: proj-101_response
    type: object
    description: API response boundary for this requirement
compatibility_policy: backward_compatible
boundary_types:
  - api_request
  - api_response
```

Example domain model:

```python
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal
from uuid import UUID

class PaymentWebhookPayload(BaseModel):
    transaction_id: UUID
    customer_id: UUID
    amount: Decimal
    currency: str
    occurred_at: datetime
    idempotency_key: str
```

---

## Architecture contract

Architecture contracts are checked against the CALM system document, graph provider facts, and component allow/deny rules.

Generated `architecture-contract.yaml`:

```yaml
contract_id: arch-proj-101
requirement_id: PROJ-101
calm_ref: .ananke/architecture/system.calm.json
allowed_dependencies: []
denied_dependencies: []
invariant_ids: []
import_rules:
  deny_cross_domain_imports: true
  deny_circular_imports: true
```

---

## Verifying BMAD artifacts

```bash
ananke bmad verify --feature-dir .ananke/specs/PROJ-101
# exits 0 if all artifacts present, 4 if missing

ananke bmad show --feature-dir .ananke/specs/PROJ-101
# prints the bmad.yaml index

ananke bmad trace --feature-dir .ananke/specs/PROJ-101
# shows requirement → AC → test traceability
```

---

## From an approved PRD

A PRD is the Markdown spec `dev spec new` writes: front matter with `status: approved` once it has been reviewed, a
`## Requirements` section whose lines start with an id (`**R1**`), optionally under `### Feature` headings, and a
`## Acceptance` section whose lines start with the requirement they check (`- R1: ...`). Indented bullets under a
requirement count as its criteria too.

```bash
ananke spec map docs/orders-prd.md            # features, acceptance ids and tests; exit 1 if inconsistent
ananke triage api-contract --out contract     # optional: the API's model contract
ananke spec compile docs/orders-prd.md --out .ananke/specs/bmad --api-contract contract/model-contract.yaml
```

- **Map:** each criterion gets a global id, `AC-R1-1`, the behaviour test that will be generated for it, and the tests
  that already mention it (by acceptance id, or by requirement id). The map is consistent when the PRD is approved,
  has requirements, every requirement has a criterion, no id repeats and every criterion names a real requirement.
  `--require-tests`, after implementation, also makes an untested criterion an error.
- **Compile:** one folder per requirement with `requirement.md` and the BMAD bundle. With `--api-contract`, the model
  contract holds the operations the requirement names (`POST /orders`, a path such as `/orders/{order_id}`, or an
  operation id in backticks) and their schemas, and says why when it names none. With a CALM file (`--calm`, or
  `.ananke/architecture/system.calm.json` when present) the architecture contract lists the components it names.
- **Linked to the revision:** `bmad-index.json` records the PRD's sha256, the map's digest, the API contract's digest
  and every generated file's sha256, so evidence made later can be traced to the exact spec it answers.
- **Inconsistency blocks:** an inconsistent PRD compiles nothing (exit 1). `--draft` maps or compiles one that isn't
  approved yet, with a warning.

---

## BMAD vs BMAD-METHOD

Ananke uses **Ananke BMAD** (Behavior, Model, Architecture) as its internal contract triad.

The external **BMAD-METHOD** ecosystem is separate. If supported, it lives under `ananke.plexus.integrations.bmad_method` to avoid semantic collision.

---

## Drift detection

After locking the spec, Ananke detects when artifacts have changed:

```bash
ananke spec lock --feature-dir .ananke/specs/PROJ-101
ananke spec diff --feature-dir .ananke/specs/PROJ-101
# → [] if no drift
# → ["HASH_MISMATCH:spec.md"] if spec changed after lock
```

Drift types: `REQUIREMENT_DRIFT`, `SPEC_DRIFT`, `ARCHITECTURE_DRIFT`, `MODEL_DRIFT`, `POLICY_DRIFT`, `TOOLCHAIN_DRIFT`, `GRAPH_DRIFT`

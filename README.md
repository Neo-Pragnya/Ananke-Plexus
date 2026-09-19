<div align="center">

<img src="https://raw.githubusercontent.com/Neo-Pragnya/Ananke-Plexus/main/assets/branding/ananke-logo.png" alt="Ananke Plexus Logo" width="260" />

# Ananke Plexus

### *Freedom at the edge. Necessity at the core.*

A local-first **Agent Development Life Cycle (ADLC) control plane** that gives AI coding agents<br/>
architectural context, deterministic guardrails, security gates, and verifiable delivery evidence.

[![PyPI](https://img.shields.io/pypi/v/ananke-plexus?label=PyPI&color=4f46e5)](https://pypi.org/project/ananke-plexus/)
[![Python](https://img.shields.io/pypi/pyversions/ananke-plexus?color=4f46e5)](https://pypi.org/project/ananke-plexus/)
[![License](https://img.shields.io/badge/license-Apache--2.0-22c55e)](LICENSE)
[![CI](https://github.com/Neo-Pragnya/Ananke-Plexus/actions/workflows/ci.yml/badge.svg)](https://github.com/Neo-Pragnya/Ananke-Plexus/actions/workflows/ci.yml)
[![Security](https://github.com/Neo-Pragnya/Ananke-Plexus/actions/workflows/security.yml/badge.svg)](https://github.com/Neo-Pragnya/Ananke-Plexus/actions/workflows/security.yml)
[![Coverage](https://img.shields.io/badge/coverage-80%25-22c55e)](https://neo-pragnya.github.io/Ananke-Plexus/)
[![uv](https://img.shields.io/badge/built%20with-uv-7c3aed)](https://docs.astral.sh/uv/)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-4f46e5)](https://neo-pragnya.github.io/Ananke-Plexus/)

</div>

---

![Ananke Plexus — System Architecture Overview](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/ananke_plexus_architecture_overview.png)

---

## Table of Contents

- [Why Ananke Plexus?](#-why-ananke-plexus)
- [The Tensegrity Model](#-the-tensegrity-model)
- [What is ADLC?](#-what-is-adlc)
- [The Plexus — From Intent to Implementation](#-the-plexus--from-intent-to-implementation)
- [Feature Highlights](#-feature-highlights)
- [Quick Start](#-quick-start)
- [The Complete Lifecycle](#-the-complete-lifecycle)
- [Code Graph Intelligence](#-code-graph-intelligence)
- [Governed Agentic Execution](#-governed-agentic-execution)
- [Security, Verification & Evidence](#-security-verification--evidence)
- [Open Ecosystem](#-open-ecosystem)
- [Package Architecture](#-package-architecture)
- [CLI Reference](#-cli-reference)
- [MCP Server](#-mcp-server)
- [APM — Agent Package Manager](#-apm--agent-package-manager)
- [Skill & Agent Registry](#-skill--agent-registry)
- [Policy Engine](#-policy-engine)
- [Ecosystem Integrations](#-ecosystem-integrations)
- [Development Setup](#-development-setup)
- [Release & Publishing](#-release--publishing)
- [Documentation](#-documentation)
- [License](#-license)

---

## 🎯 Why Ananke Plexus?

Modern coding agents can already do far more than autocomplete. They can understand requirements, explore repositories, modify dozens of files, write tests, run commands, create commits, and open pull requests.

That creates an entirely different engineering problem.

The question is no longer **"Can an AI write code?"** — it is increasingly:

> **"How do we safely allow autonomous systems to participate in software engineering without losing architectural integrity, security, traceability, or control?"**

Ananke Plexus is designed around that question.

| Problem                        | Without Ananke                                   | With Ananke                                                        |
| ------------------------------ | ------------------------------------------------ | ------------------------------------------------------------------ |
| 🌀 **Agent drift**              | Implementation diverges from requirements        | Specs, acceptance criteria, policies, and evidence preserve intent |
| 🧠 **Context overload**         | Agents read huge repos as undifferentiated text  | Code graphs deliver targeted structural context                    |
| 🏗️ **Architecture drift**       | Agents imitate accidental dependencies           | CALM expresses intended; graphs reveal actual                      |
| 🔓 **Excessive autonomy**       | Broad filesystem, shell, Git, and network access | Capability boundaries, policy gates, and approval flows            |
| 🧪 **Late testing**             | Problems discovered only in CI or review         | Progressive local verification at every lifecycle stage            |
| 🛡️ **Security as afterthought** | Secrets or unsafe changes reach PRs              | Security integrated: secrets, SAST, SCA, licenses, provenance      |
| 🧾 **Poor traceability**        | Nobody knows why a change exists                 | Evidence bundles connect intent → change → verification            |
| 🔁 **Fragile automation**       | Failed runs leave partial external side effects  | Recovery, retry, checkpointing, idempotency, state machines        |
| 🏢 **Enterprise distrust**      | Teams restrict autonomy — can't prove safety     | Auditable execution with deterministic gates                       |

---

## ⚖️ The Tensegrity Model

**Ananke** (Greek: necessity) represents the laws that cannot be negotiated away at runtime — type contracts, test outcomes, security gates, architecture boundaries, approval requirements, release provenance.

**Plexus** represents the graph of relationships connecting requirements, specifications, code, tests, architecture, policies, agents, runs, evidence, and deliverables.

**Tensegrity** is the governing metaphor: agents remain *creative at the edge* while the surrounding structure of contracts, graphs, and policies keeps the system geometrically sound.

![Governed Software Architecture — Tensegrity Model](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/ananke_plexus_governed_software_architecture.png)

This allows the system to support **more autonomy precisely because stronger constraints exist where they matter**.

---

## 🤖 What is ADLC?

**Agent Development Life Cycle** extends traditional SDLC and Spec-Driven Development to address the unique concerns of autonomous agents:

| Dimension          | SDLC              | SDD               | **ADLC**                               |
| ------------------ | ----------------- | ----------------- | -------------------------------------- |
| Primary driver     | Human process     | Specification     | Agent + human execution                |
| Source of truth    | Documents / code  | Specification     | Specs + policies + execution artifacts |
| Testing            | Stage or pipeline | Spec-derived      | Continuous, spec-traced                |
| Context management | Human cognition   | Spec artifacts    | Explicit context assembly              |
| Tool permissions   | Usually implicit  | Usually external  | First-class                            |
| Retry / recovery   | Human-driven      | Not central       | Core concern                           |
| Traceability       | Tickets + Git     | Spec → Code       | Intent → Agent → Code → Evidence       |
| Governance         | Organizational    | Contract-oriented | Machine-enforced                       |

![Why ADLC Matters — Governed Autonomous Development](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/why_adlc_matters_governed_autonomous_development.png)

The core engineering equation:

```
Safe Autonomy ∝ Agent Capability × Context Quality × Verification Strength × Governance Quality
```

A more capable model with poor context, weak policies, no architecture understanding, and no testing may simply create mistakes **faster**.

---

## 🕸️ The Plexus — From Intent to Implementation

Ananke Plexus does not attempt to replace your agents, IDE, issue tracker, CI system, or model provider. It sits **above and between them** — becoming the engineering control plane.

```
Intent → Specification → Architecture Contracts → Graph Context
       → Policy Gates  → Agent Execution        → Verification
       → Evidence      → Lifecycle Delivery
```

Every requirement is progressively transformed into durable, verifiable artifacts:

![From Intent to Implementation — Full Specification Flow](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/from_intent_to_implementation.png)

The **Ananke BMAD** triad drives the transformation:

| Contract         | What it captures                                  | Generated artifact                      |
| ---------------- | ------------------------------------------------- | --------------------------------------- |
| **Behavior**     | Acceptance criteria as executable tests + Gherkin | `tests/test_*_behavior.py`, `*.feature` |
| **Model**        | API schema, event payloads, boundary models       | `model-contract.yaml`                   |
| **Architecture** | CALM ref, allowed/denied dependencies, invariants | `architecture-contract.yaml`            |

Every generated test records its full traceability chain: `Requirement → AC → Scenario → Test ID`.

---

## ✨ Feature Highlights

### Specification & Contracts
| Command               | What it does                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `ananke spec create`  | Captures requirements with acceptance criteria from CLI, Jira, or file                           |
| `ananke spec plan`    | Generates technical plan from spec                                                               |
| `ananke spec tasks`   | Converts plan into executable task list                                                          |
| `ananke spec lock`    | Locks spec with SHA-256 hashes for drift detection                                               |
| `ananke spec diff`    | Detects drift since last lock                                                                    |
| `ananke bmad compile` | Compiles Behavior + Model + Architecture contracts — generates failing pytest skeleton + Gherkin |
| `ananke bmad trace`   | Shows full Requirement → AC → Test traceability                                                  |
| `ananke bmad verify`  | Confirms all BMAD artifacts are present                                                          |

### Architecture
| Command                 | What it does                                    |
| ----------------------- | ----------------------------------------------- |
| `ananke arch validate`  | Reconciles declared CALM vs observed code graph |
| `ananke arch diff`      | Shows architecture delta (declared vs observed) |
| `ananke arch render`    | Renders Mermaid diagrams from CALM              |
| `ananke arch reconcile` | Applies architectural corrections               |

### Graph & Impact
| Command               | What it does                                                     |
| --------------------- | ---------------------------------------------------------------- |
| `ananke graph build`  | Builds AST-based code graph (Graphifyy + CRG adapters available) |
| `ananke graph impact` | Calculates blast radius and impacted symbols for changed files   |
| `ananke graph query`  | Queries graph by symbol, kind, or path                           |
| `ananke graph export` | Exports graph as JSON                                            |

### Policy & Verification
| Command                      | What it does                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------------- |
| `ananke policy install-pack` | Installs `baseline`, `python-library`, `agentic-security`, or `enterprise-strict`  |
| `ananke policy list`         | Lists active policy rules                                                          |
| `ananke policy check`        | Evaluates a policy stage (exits 3 on block)                                        |
| `ananke verify`              | Full gate suite: Ruff, mypy, pytest, Gitleaks, Semgrep, pip-audit, Trivy, licenses |

### Hooks
| Command                       | What it does                                                          |
| ----------------------------- | --------------------------------------------------------------------- |
| `ananke hooks install`        | Installs git hooks (native, pre-commit-framework, or delegated modes) |
| `ananke hooks run pre-commit` | Ruff + secrets scan on staged files                                   |
| `ananke hooks run pre-push`   | Full verification suite before push                                   |

### Autonomous Runs
| Command                | What it does                                                    |
| ---------------------- | --------------------------------------------------------------- |
| `ananke run start`     | Executes a governed DAG run with checkpointing and compensation |
| `ananke run approvals` | Lists pending/resolved approvals for a run                      |
| `ananke run approve`   | Grants or denies a run approval                                 |
| `ananke run cancel`    | Cancels a running job with safe cleanup                         |
| `ananke run replay`    | Replays a run from checkpoint                                   |

### MCP & Skills
| Command              | What it does                                                |
| -------------------- | ----------------------------------------------------------- |
| `ananke serve-mcp`   | Exposes 15 read-only + 5 mutation MCP tools (stdio or HTTP) |
| `apm install`        | Installs agent skills with lockfile, sandbox, provenance    |
| `apm audit`          | Audits skill permissions                                    |
| `apm bundle export`  | Bundles installed skills as a versioned archive             |
| `apm import-copilot` | Imports GitHub Awesome Copilot skills with provenance       |

### Skill & Agent Registry
| Command                        | What it does                                                          |
| ------------------------------ | --------------------------------------------------------------------- |
| `ananke registry learn`        | Discovers skills/agents from dirs, Python, Rust, MCP, git, archives   |
| `ananke registry promote`      | Runs the quality gate, then raises trust and channel                  |
| `ananke registry resolve`      | Policy-aware, explainable dependency resolution                       |
| `ananke sync`                  | Resolves project requirements into a deterministic `ananke.lock`      |
| `ananke registry docs build`   | Generates static, offline registry documentation                      |

### Evidence
| Command                      | What it does                                         |
| ---------------------------- | ---------------------------------------------------- |
| `ananke evidence show`       | Inspects latest evidence bundle                      |
| `ananke evidence prune`      | Prunes old bundles with dry-run safety               |
| `ananke lifecycle pr-create` | Creates PR with full 8-section evidence storytelling |

---

## 🚀 Quick Start

### Install

```bash
# Recommended
uv tool install ananke-plexus

# Or with pip
pip install ananke-plexus

# With all optional integrations
pip install "ananke-plexus[mcp,jira,telemetry]"
```

### Initialize and diagnose

```bash
cd my-project
ananke init
ananke doctor --json
```

### First requirement → BMAD contracts

```bash
ananke spec create \
  --id PROJ-101 \
  --title "Add idempotent payment webhook" \
  --acceptance "Duplicate events are deduplicated" \
  --acceptance "Idempotency key stored per transaction" \
  --acceptance "Returns 200 on replay without side effects"

# Compile: generates failing tests, model schema, architecture contract
ananke bmad compile --feature-dir .ananke/specs/PROJ-101

# View traceability
ananke bmad trace --feature-dir .ananke/specs/PROJ-101

# Lock spec for drift detection
ananke spec lock --feature-dir .ananke/specs/PROJ-101
```

### Build graph + run verification

```bash
ananke graph build
ananke graph impact --files src/webhooks.py

ananke policy install-pack python-library
ananke verify
```

### Install git hooks

```bash
ananke hooks install
# or with pre-commit framework
ananke hooks install --mode pre-commit-framework
```

### MCP server for your IDE

```json
{
  "mcpServers": {
    "ananke": {
      "command": "ananke",
      "args": ["serve-mcp"]
    }
  }
}
```

### Golden demo

```bash
ananke run start DEMO-101 --backend fake
```

Expected output:
```
✓ Requirement captured
✓ Spec locked                 sha256:a3f8...
✓ Behavior contract compiled  3 scenarios
✓ Model contract compiled     2 schemas
✓ Architecture validated
✓ Isolated worktree created   .ananke/runs/run-20260916/worktree
✓ Backend completed           fake backend edit
✓ Ruff passed
✓ Type checking passed
✓ Tests passed
✓ Secret scan passed
✓ SAST passed
✓ Dependency audit passed
✓ Graph review: 8 impacted symbols, 0 forbidden edges
✓ Evidence bundle finalized   .ananke/evidence/20260916T.../
✓ Pull request ready
```

---

## 🔄 The Complete Lifecycle

```
📋 Jira Story / Requirement
         │
         ▼
    📜 Specification
    ├── spec.md        acceptance criteria
    ├── plan.md        technical approach
    ├── tasks.md       work breakdown
    └── spec.lock.json hash snapshot
         │
         ▼
  🧬 BMAD Contracts
  ├── tests/test_*_behavior.py   failing pytest skeleton
  ├── *.feature                  Gherkin scenarios
  ├── model-contract.yaml        API schema skeleton
  └── architecture-contract.yaml CALM + deny rules
         │
         ▼
  🕸️ Code Graph + Architecture
  ├── graph build + blast radius
  └── CALM declared vs observed reconciliation
         │
         ▼
  🛡️ Policy Gates
  ├── pre-commit:  ruff, secrets
  ├── verify:      tests, mypy, semgrep, pip-audit, trivy
  └── pre-push:    architecture, licenses, full suite
         │
         ▼
  🤖 Agent Backend
  ├── Copilot / Amazon Q / Kiro / Hermes / generic CLI
  ├── Isolated git worktree
  ├── Approval gates (PLAN, FILE_WRITE, GIT_PUSH, PR_CREATE)
  └── DAG scheduler with checkpoint + compensation
         │
         ▼
  🧾 Evidence Bundle
  ├── manifest.json       (SHA-256 hashes)
  ├── policy-decisions.jsonl
  ├── gates/*.json        (per-gate results)
  ├── gates/sast.sarif    (SARIF 2.1.0)
  └── checksums.sha256
         │
         ▼
  🚀 Delivery
  ├── PR with 8-section evidence body
  ├── Jira verification comment
  └── Release with SBOM + provenance attestation
```

---

## 🕸️ Code Graph Intelligence

Ananke treats your repository as a **graph of semantic relationships**, not a flat directory tree.

![Code Graph Intelligence — Blast Radius & Symbol Impact](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/code_graph_intelligence_infographic.png)

### Why this matters

Without graph intelligence, agents read huge repositories as undifferentiated text. With it:

```
Agent → Graph Query → Relevant Subgraph → Focused Context → Better Implementation
```

### Blast radius analysis

When `authenticate()` changes, Ananke traverses the graph to answer:
- What may break?
- Which tests matter?
- Which architecture components are involved?
- Which routes rely on the changed symbol?

```bash
ananke graph impact --files src/auth.py
# → 14 impacted symbols across 3 files
# → 0 forbidden edges
# → blast_radius: 14
```

### Graph providers

| Provider              | When to use                                                       |
| --------------------- | ----------------------------------------------------------------- |
| **native** (built-in) | Always available; AST-based Python analysis                       |
| **Graphifyy**         | Broad knowledge-graph enrichment; install `pip install graphifyy` |
| **code-review-graph** | Incremental blast-radius; install `pip install code-review-graph` |

All providers normalize to the same canonical model (14 node kinds, 14 edge kinds) with provenance: `extracted | inferred | ambiguous | declared`.

---

## 🤖 Governed Agentic Execution

Ananke orchestrates agent execution as a deterministic DAG — not a free-form chat loop.

![Governed Agentic Execution Pipeline](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/governed_agentic_execution_pipeline.png)

### Execution plan

Every run follows a typed step sequence with dependency ordering:

```
read_context → create_worktree → create_branch → generate_contracts
→ invoke_backend → run_gate → update_graph → render_architecture
→ commit → push → create_pr → post_evidence
```

### Approval gates

Sensitive steps require explicit human approval before proceeding:

| Approval class | Required for                   |
| -------------- | ------------------------------ |
| `PLAN`         | Approving the execution plan   |
| `FILE_WRITE`   | Writing outside declared paths |
| `GIT_PUSH`     | Pushing to remote              |
| `PR_CREATE`    | Creating a pull request        |
| `RELEASE`      | Publishing a release           |

```bash
ananke run approvals --run-id 20260916T042859Z-abc12345
ananke run approve  --run-id 20260916T042859Z-abc12345 --approval-id a1b2c3d4
```

### Resilience features

- **Checkpointing** — state saved after every side effect; resume from crash with `ananke run replay`
- **Compensation** — failed runs retain branch, retry evidence posts, and mark run as partial (never silently delete work)
- **Concurrency** — independent ready steps execute in parallel via `ThreadPoolExecutor`
- **Idempotency** — all lifecycle operations carry stable keys; duplicate Jira comments or PRs are detected and skipped

### Supported backends

| Backend            | Binary                   | Notes                                        |
| ------------------ | ------------------------ | -------------------------------------------- |
| **fake**           | built-in                 | Deterministic demo backend; always available |
| **GitHub Copilot** | `gh` + copilot extension | `gh copilot suggest` integration             |
| **Amazon Q**       | `q`                      | `q chat --no-interactive` integration        |
| **Kiro**           | `kiro`                   | `kiro run --task` integration                |
| **Hermes**         | `hermes`                 | Full session + streaming + subagents         |
| **Generic CLI**    | any                      | stdin/stdout JSON protocol                   |

```bash
ananke backend list
ananke backend doctor copilot
ananke backend test fake
```

---

## 🛡️ Security, Verification & Evidence

Security is not one tool — it is a layered system integrated at every stage.

![Verifiable Pipeline for Autonomous Engineering](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/verifiable_pipeline_for_autonomous_engineering.png)

### Security layers

| Layer                     | Tool                  | Stage               |
| ------------------------- | --------------------- | ------------------- |
| Secrets detection         | Gitleaks              | pre-commit, CI      |
| SAST                      | Semgrep               | verify, CI          |
| Python CVEs               | pip-audit             | verify, CI          |
| Filesystem/container CVEs | **Trivy** (hard gate) | verify, CI, release |
| License compliance        | pip-licenses          | verify              |
| Workflow security         | actionlint, zizmor    | CI                  |
| Code scanning             | CodeQL                | CI                  |
| SBOM                      | CycloneDX             | release             |
| Build provenance          | GitHub Attestations   | release             |

### Trivy as a hard gate

Trivy blocks the CI pipeline and release workflow on **CRITICAL + HIGH unfixed CVEs**:

```bash
make trivy-scan          # table report — exits 1 on CRITICAL/HIGH
make trivy-scan-sarif    # SARIF output for GitHub Security tab
make security            # trivy + pip-audit together
```

In CI and release workflows, Trivy SARIF is automatically uploaded to the **GitHub Security tab**.

### Evidence bundle

Every `ananke verify` run produces an immutable, content-addressed evidence bundle:

```
.ananke/evidence/<run-id>/
├── manifest.json          ← SHA-256 hashes of all files
├── run.json               ← run metadata + gate summary
├── policy-decisions.jsonl ← every rule evaluation with result
├── checksums.sha256        ← integrity file
└── gates/
    ├── ruff.json
    ├── mypy.json
    ├── pytest.json
    ├── gitleaks.json
    ├── semgrep.json
    ├── pip-audit.json
    ├── trivy.json
    ├── license.json
    └── sast.sarif          ← SARIF 2.1.0 (uploadable to GitHub Security)
```

### PR evidence storytelling

`ananke lifecycle pr-create` generates a PR body with 8 structured sections:

```markdown
## Requirement        — acceptance criteria with traceability
## Behavioral Contract — AC → test mapping
## Architecture Delta  — CALM declared vs observed
## Code-Graph Impact   — blast radius, impacted symbols
## Verification Evidence — run ID, gate results
## Security Evidence   — SARIF, scanner results
## Residual Risk       — known open items
## Traceability        — spec hash, evidence bundle ID
```

---

## 🌐 Open Ecosystem

Ananke Plexus is a **control plane, not a mega-dependency**. Every external integration is swappable.

![Open Ecosystem — Getting Started with Any Agent](https://raw.githubusercontent.com/neo-pragnya/ananke-plexus/main/assets/open_ecosystem_getting_started.png)

```
              ┌─────────────────────────────────────┐
              │          Ananke Plexus               │
              │  ┌──────────────────────────────┐   │
              │  │         Core Runtime          │   │
              │  │  Config · Policy · Events    │   │
              │  │  Evidence · Execution · IDs  │   │
              │  └──────────────────────────────┘   │
              └──┬──────┬──────┬──────┬─────────────┘
                 │      │      │      │
         ┌───────┘  ┌───┘  ┌───┘  ┌───┘
         ▼          ▼      ▼      ▼
    Agent Port  Graph   SCM    Scanner
    ──────────  Port    Port   Port
    Copilot     Native  Bitbucket  Ruff
    Amazon Q    Graphifyy GitHub   Semgrep
    Kiro        CRG             Trivy
    Hermes                      Gitleaks
    Generic CLI
```

Tools will change. Models will change. IDEs will change. The governance model survives those changes.

---

## 📦 Package Architecture

```
src/ananke/plexus/
├── cli/           — ananke + apm Typer CLIs
├── core/          — IDs, result types, event bus, workflow audit
├── config/        — Config (TOML), secrets resolver (env/cmd/keychain), migration
├── contracts/     — BMAD: behavior pytest + Gherkin, model schema, architecture contract
├── specs/         — Spec pipeline, providers (native, Spec Kit), lock, drift detection
├── architecture/  — CALM loader, validation, delta, reconciliation, Mermaid rendering
├── graph/         — Canonical graph model, native/Graphifyy/CRG providers
├── policy/        — Policy engine, expression evaluator, 4 builtin packs
├── gates/         — Gate runner with adapters: Ruff, mypy, pytest, Gitleaks, Semgrep, Trivy, pip-audit
├── execution/     — ExecutionPlan DAG, concurrent scheduler, checkpoint, approvals, compensation
├── backends/      — AgentBackend protocol: Copilot, Q, Kiro, Hermes, generic CLI, fake
├── lifecycle/     — Git service, git worktree isolation, Jira, Bitbucket, PR evidence generator
├── hooks/         — Safe tensile git hooks: native, pre-commit-framework, delegated
├── mcp/           — MCP server (stdio + HTTP), 15 R/O + 5 mutation tools, dynamic resources
├── apm/           — Skill manifests, lockfile, sandbox enforcement, Copilot importer, bundles
├── evidence/      — Evidence bundle, SARIF 2.1.0, SHA-256 checksums, retention
├── events/        — In-process event bus with 24 event types + JSONL audit log
├── telemetry/     — OpenTelemetry adapter + noop
└── plugins/       — Entry-point discovery, PluginMetadata model
```

---

## ⌨️ CLI Reference

### `ananke` — core commands

```bash
ananke version                  # print version
ananke init                     # initialize .ananke/ workspace
ananke doctor [--json]          # environment diagnostics
ananke verify                   # full local gate suite
ananke config show              # show effective config
ananke config migrate [--apply] # migrate config schema
```

### `ananke spec` — specification lifecycle

```bash
ananke spec create  --id ID --title TITLE --acceptance "..." [--acceptance "..."]
ananke spec plan    --feature-dir PATH
ananke spec tasks   --feature-dir PATH
ananke spec lock    --feature-dir PATH
ananke spec diff    --feature-dir PATH
ananke spec validate --feature-dir PATH
ananke spec converge --feature-dir PATH
```

### `ananke bmad` — BMAD contracts

```bash
ananke bmad compile  --feature-dir PATH    # generate all 3 contracts
ananke bmad show     --feature-dir PATH    # print bmad.yaml
ananke bmad trace    --feature-dir PATH [--json]
ananke bmad verify   --feature-dir PATH    # check artifacts present
```

### `ananke graph` — code graph

```bash
ananke graph build   [--project PATH]
ananke graph update  [--project PATH]
ananke graph status  [--project PATH]
ananke graph query   --text SYMBOL
ananke graph impact  --files FILE [FILE ...]
ananke graph export  [--format json]
ananke graph review  [--project PATH]
```

### `ananke arch` — architecture

```bash
ananke arch init       # create CALM skeleton
ananke arch validate   # reconcile declared vs observed
ananke arch diff       # show delta
ananke arch reconcile  # apply corrections
ananke arch render     # generate Mermaid diagrams
```

### `ananke policy` — policy engine

```bash
ananke policy list         [--json]
ananke policy check        --stage verify
ananke policy explain      --stage pre_commit
ananke policy install-pack NAME   # baseline | python-library | agentic-security | enterprise-strict
```

### `ananke hooks` — git hooks

```bash
ananke hooks install   [--mode native|pre-commit-framework|delegated] [--chain]
ananke hooks uninstall [all|pre-commit|post-commit|pre-push]
ananke hooks status    [--json]
ananke hooks run       STAGE [--verbose]
```

### `ananke run` — autonomous execution

```bash
ananke run start     --spec-id ID [--backend fake|copilot|amazon-q|kiro|hermes]
ananke run status    --run-id ID
ananke run approvals --run-id ID [--json]
ananke run approve   --run-id ID --approval-id AID [--deny] [--notes "..."]
ananke run cancel    --run-id ID
ananke run replay    --run-id ID
```

### `ananke backend` — agent backends

```bash
ananke backend list   [--json]
ananke backend doctor BACKEND_NAME
ananke backend test   BACKEND_NAME
```

### `ananke lifecycle` — enterprise integrations

```bash
ananke lifecycle branch          --type TYPE --ticket KEY --slug SLUG
ananke lifecycle issue-transition --ticket KEY --state STATE
ananke lifecycle pr-create       --title TITLE --branch BRANCH
ananke lifecycle worktrees
ananke lifecycle evidence        [--format compact|json|csv] [--since-hours N]
```

### `ananke evidence` — evidence management

```bash
ananke evidence show   [--run-id ID]
ananke evidence verify [--run-id ID]
ananke evidence export [--run-id ID] [--format json|sarif]
ananke evidence prune  --older-than 30d [--apply]
```

### `ananke serve-mcp` — MCP server

```bash
ananke serve-mcp                              # stdio transport (default)
ananke serve-mcp --transport http --port 8765 # HTTP transport
ananke serve-mcp --allow-mutations            # enable mutation tools
```

### `apm` — agent package manager

```bash
apm list
apm install        --source PATH
apm info           NAME
apm activate       NAME
apm deactivate     NAME
apm verify         NAME
apm audit          NAME
apm sandbox-check  NAME
apm resolve        NAME
apm import-copilot --path PATH
apm bundle list    --bundle PATH
apm bundle export  --name N1 --name N2 [--output PATH]
apm bundle install --bundle PATH

# registry-backed
apm search         QUERY
apm install        core/graph-review@^1 [--activate]
apm lock | upgrade [--dry-run] | link PATH | unlink REF | publish PATH
```

### `ananke registry` / `skill` / `agent` / `sync` — skill & agent registry

```bash
ananke registry init
ananke registry learn SOURCE [--dry-run] [--version auto] [--allow-dynamic] [--allow-network]
ananke registry inspect | show | list | search | diff | resolve [--explain]
ananke registry promote | yank | deprecate | quarantine | alias
ananke registry activate | translate | lock | verify-lock | publish | link
ananke registry export | import | backup | restore | verify | doctor | gc | report | analytics
ananke registry docs build | dump      ananke registry serve | watch | schema | policy | benchmark
ananke registry key generate | trust | revoke | list      ananke registry sign REF --key FILE | signatures REF
ananke registry remote list | search | pull      ananke registry analytics query [QUESTION]
ananke skill|agent list | show | search | register | resolve | versions | activate
ananke sync [--activate] [--release] [--dry-run]
```

---

## 🔌 MCP Server

Ananke exposes a governed engineering intelligence surface via Model Context Protocol:

### Read-only tools (safe for any agent)
Registry tools: `registry_search`, `registry_get_skill`, `registry_get_agent`, `registry_resolve`, `registry_compare_versions`, `registry_list_capabilities` (resources `ananke://registry/skills`, `ananke://registry/agents`).

```
ananke.project.status     ananke.spec.get           ananke.spec.validate
ananke.spec.create        ananke.arch.get            ananke.arch.validate
ananke.arch.render        ananke.graph.query         ananke.graph.impact
ananke.graph.update       ananke.policy.explain      ananke.verify.run
ananke.run.status         ananke.evidence.get
```

### Mutation tools (requires `--allow-mutations`)
```
ananke.run.execute             ananke.git.create_branch
ananke.git.commit              ananke.lifecycle.transition_issue
ananke.lifecycle.create_pr
```

### Dynamic resources
```
ananke://project/status         ananke://architecture/system
ananke://spec/PROJ-101          ananke://graph/snapshot
ananke://policy/baseline        ananke://run/<id>/evidence
ananke://evidence/index         ananke://policy/index
```

### Prompts
```
architecture-aware-implementation    blast-radius-review
spec-clarification                   bmad-contract-generation
pr-evidence-summary
```

---

## 📦 APM — Agent Package Manager

Agent skills are supply-chain artifacts with provenance, permissions, and lockfiles.

```toml
# ananke-skill.toml
[skill]
name = "graph-reviewer"
version = "1.2.0"

[permissions]
filesystem_read  = ["src/**", "tests/**", ".ananke/**"]
filesystem_write = [".ananke/evidence/**"]
network          = []
shell            = ["ananke graph *"]

[provenance]
source     = "github"
repository = "org/awesome-skills"
revision   = "abc123def456..."
```

**Untrusted skills cannot:**
- Access `.ananke/secrets/**` or `config.local.toml`
- Run blocked patterns (`git *`, `curl *`, `rm -rf*`, etc.)
- Write outside declared `filesystem_write` globs
- Access hosts not in `network` allowlist

---

## 🗂️ Skill & Agent Registry

A local-first, enterprise-grade registry for skills, agents, tools, workflows, evaluators, prompts, policies and bundles. Discovery, trust and resolution are separate concerns:

```text
learn (discover) → register (immutable) → promote (trust) → resolve (policy) → lock → activate
```

- **Immutable & content-addressed** — versions are SHA-256 verified payloads; trust, channel and lifecycle are separate, audited mutable state.
- **Importers** — Ananke manifests, plain directories (`SKILL.md`, `skill.yaml`…), Python packages, Rust crates, MCP servers, git, archives, framework AST scans and an opt-in sandboxed dynamic scan. Missing fields are reported, never guessed.
- **Policy-aware resolver** — SemVer, channels, trust, licences, security status, compatibility and enterprise approval, with `✓/✗` explanations and a backtracking dependency solver.
- **Reproducible** — deterministic `ananke.lock` via `ananke sync`; evidence bundles record the exact capability graph of every run.
- **Secure by default** — secret scanning, path-traversal/symlink/archive checks, git argument-injection protection, network and dynamic execution gated by policy, read-only server and MCP tools.
- **Signed** — Ed25519 signatures bound to URI + digest; `verified` is always recomputed against your policy's trusted keys (never read from a manifest or archive).
- **Federated, safely** — pull-only remote registries: https, no redirects, digest-checked, policy-validated, and trust is never inherited.
- **Operable** — export/import, backup/restore, verify/doctor, dry-run-first GC, reports, analytics questions (DuckDB or SQLite), optional similarity search, native file watching, benchmarks with regression tracking, generated offline docs.

```bash
ananke registry init
ananke registry learn ./skills/graph-review
ananke registry promote core/graph-review@1.0.0 --trust approved --channel stable
ananke registry resolve core/graph-review@^1 --explain
ananke sync --activate
```

> Not yet implemented: the optional Rust core and the `redb`/`tantivy` accelerators (they need a Rust toolchain; SQLite FTS5 is used). Tracked in `ANANKE_PLEXUS_MASTER_SPEC.md` §61.

Docs: [Concept](https://neo-pragnya.github.io/Ananke-Plexus/concepts/registry/) · [Guide](https://neo-pragnya.github.io/Ananke-Plexus/guides/registry-guide/) · [Reference](https://neo-pragnya.github.io/Ananke-Plexus/reference/registry/)

---

## 🛡️ Policy Engine

Four builtin policy packs cover common project profiles:

| Pack                | Includes                                            |
| ------------------- | --------------------------------------------------- |
| `baseline`          | `fail_closed=true`, no secrets                      |
| `python-library`    | Ruff, mypy, coverage ≥ 85%, pip-audit, license scan |
| `agentic-security`  | SAST, SCA, Trivy, architecture no-cycle             |
| `enterprise-strict` | All of the above at CRITICAL severity               |

Custom rules in `.ananke/policy/my-rules.toml`:

```toml
[[rule]]
id = "security.no-secret"
stage = "pre_commit"
severity = "critical"
gate = "gitleaks"
assert = "findings.count == 0"
on_failure = "block"

[[rule]]
id = "tests.minimum-coverage"
stage = "verify"
severity = "medium"
gate = "pytest"
assert = "coverage.line >= 85"
on_failure = "block"
```

---

## 🔗 Ecosystem Integrations

| Integration            | Adapter type          | Notes                                            |
| ---------------------- | --------------------- | ------------------------------------------------ |
| **GitHub Copilot**     | Backend adapter       | `gh copilot suggest` + MCP host                  |
| **Amazon Q Developer** | Backend adapter       | `q chat` CLI + MCP                               |
| **Kiro**               | Backend adapter       | `kiro run` CLI + MCP/hooks                       |
| **Hermes**             | Backend adapter       | Persistent sessions, streaming, subagents        |
| **Generic CLI**        | Backend adapter       | stdin/stdout JSON protocol — any agent           |
| **FINOS CALM**         | Architecture provider | Canonical architecture-as-code                   |
| **Graphifyy**          | Graph provider        | Knowledge-graph enrichment                       |
| **code-review-graph**  | Graph provider        | Incremental blast-radius                         |
| **Jira Cloud**         | Lifecycle adapter     | Issue fetch, transition, comment, evidence       |
| **Bitbucket Cloud**    | SCM adapter           | Branch, PR, evidence attachment                  |
| **Semgrep**            | Security gate         | SAST with SARIF output                           |
| **Gitleaks**           | Security gate         | Secrets scan with staged diff support            |
| **Trivy**              | Security gate         | CVE + license; **hard gate in all releases**     |
| **pip-audit**          | Security gate         | Python dependency CVE check                      |
| **GitHub Spec Kit**    | Spec provider         | SDD workflow integration                         |
| **OpenTelemetry**      | Telemetry             | Spans, metrics, privacy-first (local by default) |
| **MCP Python SDK v2**  | Transport             | stdio (default) + HTTP                           |

---

## 🛠️ Development Setup

```bash
git clone https://github.com/neo-pragnya/ananke-plexus
cd ananke-plexus

# Install with uv (recommended)
uv sync --all-extras

# Or with pip
pip install -e ".[dev]"

# Run tests
make test
# or: uv run pytest

# With coverage report
make coverage
# or: uv run pytest --cov=src/ananke --cov-report=html

# Lint + format check + type check
make verify
# or: uv run ruff check src tests && uv run mypy src

# Run local security scan
make trivy-scan    # requires trivy installed

# Build artifacts
make build         # uses uv build

# Preview docs
make docs-serve
```

### Project structure

```
ananke-plexus/
├── src/ananke/plexus/      — package source
├── tests/unit/             — 337 unit tests, 80% coverage
├── assets/                 — 8 documentation images
├── docs/                   — MkDocs documentation site
│   ├── concepts/           — architecture, BMAD, graph, policy, MCP
│   ├── guides/             — getting started, release guide
│   └── reference/          — CLI, config, policy language
├── .ananke/                — example workspace (for demos)
├── .github/workflows/      — CI, security, scorecard, docs, release
├── Makefile                — uv-based dev targets
├── pyproject.toml          — package config + [dependency-groups]
└── uv.lock                 — locked dependency tree (133 packages)
```

### Running the CI checks locally

```bash
make lint          # ruff check
make format-check  # ruff format --check
make typecheck     # mypy
make test          # pytest
make trivy-scan    # trivy fs (blocks on CRITICAL/HIGH)
make security      # trivy + pip-audit
```

---

## 📦 Release & Publishing

Ananke Plexus uses **uv** for builds and **PyPI OIDC Trusted Publishing** — no long-lived API tokens in CI.

### Release workflow (CI path)

```bash
# 1. Bump version in src/ananke/plexus/version.py and update CHANGELOG.md

# 2. Resolve release version dynamically from source
VERSION="$(uv run python -c 'from ananke.plexus.version import __version__; print(__version__)')"

# 3. Commit, tag, push
git add src/ananke/plexus/version.py CHANGELOG.md
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"
git push origin main "v$VERSION"
```

The GitHub workflows run automatically:

```
build (uv build + twine check)
  └─► trivy (CRITICAL/HIGH hard gate → SARIF to Security tab)
        └─► provenance (actions/attest-build-provenance)
              └─► publish (uv publish --trusted-publishing always)
                    └─► GitHub Release (with dist artifacts)
```

**Build-once principle:** the exact artifacts from the `build` job are downloaded by `publish` — never rebuilt between validation and publishing.

### Local publish (manual fallback)

```bash
cp .env.example .env
# Set UV_PUBLISH_TOKEN + UV_PUBLISH_TOKEN_TESTPYPI in .env

make publish-testpypi   # TestPyPI first
make publish-pypi       # production PyPI (includes trivy gate)
```

See [Release Guide](docs/guides/release.md) for OIDC Trusted Publisher setup.

---

## 📚 Documentation

Full documentation: **[neo-pragnya.github.io/Ananke-Plexus](https://neo-pragnya.github.io/Ananke-Plexus/)**

| Section                                                                                    |                                              |
| ------------------------------------------------------------------------------------------ | -------------------------------------------- |
| [Why Ananke Plexus](https://neo-pragnya.github.io/Ananke-Plexus/concepts/why-ananke/)      | Problems solved, tensegrity philosophy, ADLC |
| [System Architecture](https://neo-pragnya.github.io/Ananke-Plexus/concepts/architecture/)  | Ports & adapters, runtime, exit codes        |
| [Ananke BMAD](https://neo-pragnya.github.io/Ananke-Plexus/concepts/bmad/)                  | Behavior + Model + Architecture contracts    |
| [Code Graph Intelligence](https://neo-pragnya.github.io/Ananke-Plexus/concepts/graph/)     | Graph model, blast radius, providers         |
| [Policy & Security](https://neo-pragnya.github.io/Ananke-Plexus/concepts/policy-security/) | Policy packs, Trivy, evidence bundles        |
| [MCP & APM](https://neo-pragnya.github.io/Ananke-Plexus/concepts/mcp-apm/)                 | MCP tools + resources, skill management      |
| [Getting Started](https://neo-pragnya.github.io/Ananke-Plexus/guides/getting-started/)     | Step-by-step first use guide                 |
| [Configuration](https://neo-pragnya.github.io/Ananke-Plexus/reference/configuration/)      | Config files, secrets, precedence            |
| [Policy Language](https://neo-pragnya.github.io/Ananke-Plexus/reference/policy/)           | Rule schema, expressions, builtin packs      |
| [Release Guide](https://neo-pragnya.github.io/Ananke-Plexus/guides/release/)               | uv publish, OIDC, Trivy gate                 |

```bash
make docs-serve   # preview locally at http://127.0.0.1:8000
make docs-build   # static build → site/
```

---

## 📜 License

[Apache-2.0](LICENSE) © 2026 Neo Pragnya

---

<div align="center">

> **Ananke Plexus transforms autonomous coding from a sequence of probabilistic model actions**
> **into a specification-driven, architecture-aware, graph-grounded, policy-governed,**
> **deterministically verified, and auditable software engineering lifecycle.**

**[📦 PyPI](https://pypi.org/project/ananke-plexus/) · [📖 Docs](https://neo-pragnya.github.io/Ananke-Plexus/) · [🐛 Issues](https://github.com/Neo-Pragnya/Ananke-Plexus/issues) · [📋 Changelog](CHANGELOG.md)**

</div>
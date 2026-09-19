# 🚀 Ananke Plexus Implementation Log

Status date: 2026-09-19

This log tracks what has been implemented, what is partially implemented, and what comes next.

## 🎯 Delivery Snapshot

| Area | Status | Notes |
|---|---|---|
| Spec lifecycle (Spec Kit + native fallback) | ✅ Implemented | Create, plan, tasks, lock, validate, diff, converge, superpowers |
| Graph baseline | ✅ Implemented | Build, query, impact, export with canonical nodes/edges |
| APM baseline | ✅ Implemented | Install, list, activate, deactivate, resolve, audit, sandbox-check, import-copilot |
| MCP baseline | ✅ Implemented | Read-only tools/resources/prompts over stdio + HTTP action transport |
| Lifecycle baseline | ✅ Implemented | Branch naming, issue transition, PR creation, worktree listing with idempotency store |
| Run engine baseline | ✅ Implemented | Start, status, cancel, replay with persisted state |
| Skill & Agent Registry (Epic O) | ✅ Implemented (Python) | Immutable CAS + SQLite registry, importers, resolver, `ananke.lock`, sync, activation, signatures, federation, docs generator, server, MCP tools; Rust core, redb and tantivy not implemented |
| Enterprise adapters | 🟡 Partial | Jira/Bitbucket provider simulation only (real remote adapters pending) |
| Security/release hardening | 🟡 Partial | Workflows exist; full attestations/SBOM/strict policy gates pending |

## 🌈 Architecture Flow (Current)

```mermaid
flowchart LR
    A[📥 Requirement Capture] --> B[🧭 Spec Provider<br/>Spec Kit or Native]
    B --> C[📑 Plan + Tasks]
    C --> D[🧱 BMAD Compile]
    D --> E[🔒 Spec Lock + Drift Check]
    E --> F[🕸️ Graph Build + Impact]
    F --> G[⚖️ Verify Gates]
    G --> H[📦 Evidence Bundle]
    H --> I[🔁 Run State + Lifecycle]

    classDef start fill:#C8F7C5,stroke:#2E7D32,color:#1B5E20,stroke-width:2px;
    classDef mid fill:#FFF3BF,stroke:#F08C00,color:#995200,stroke-width:2px;
    classDef finish fill:#D0EBFF,stroke:#1971C2,color:#0B3D91,stroke-width:2px;
    class A,B,C,D,E,F,G mid;
    class H,I finish;
```

## 🧩 MCP Read-Only Surface

| Action | Result | Security guard |
|---|---|---|
| `ping` | `pong` | request size limit |
| `tools.list` | available read-only tool names | permission map |
| `resources.list` | available resource URIs | static allow list |
| `resources.read` | resource text | canonical local path reads |
| `prompts.list` | available prompt names | fixed registry |
| `prompts.get` | prompt content | fixed registry |
| `tools.call` | routed API result | mutation tools blocked |
| `shutdown` | terminate loop | explicit action |

## 🌐 MCP Transport Modes

| Transport | Endpoint | Auth | Status |
|---|---|---|---|
| stdio | process stdin/stdout | n/a | ✅ implemented |
| http | `POST /mcp` | optional Bearer token | ✅ implemented baseline |

```mermaid
flowchart LR
    C[🧠 MCP Client] --> S[📡 Transport Layer]
    S --> T1[stdio]
    S --> T2[http /mcp]
    T1 --> G[🛡️ Guard: size + schema]
    T2 --> A[🔐 Optional Bearer Auth]
    A --> G
    G --> R[🧰 Tool/Resource/Prompt Router]
    R --> O[📤 JSON Response]

    classDef blue fill:#D0EBFF,stroke:#1C7ED6,color:#0B3D91,stroke-width:2px;
    classDef gold fill:#FFF3BF,stroke:#F08C00,color:#995200,stroke-width:2px;
    classDef green fill:#D3F9D8,stroke:#2B8A3E,color:#1C5D2A,stroke-width:2px;
    class C,S,T1,T2 blue;
    class A,G gold;
    class R,O green;
```

## ⚙️ APM Governance Baseline

```mermaid
flowchart TD
    A[📦 Skill Source] --> B[📝 Parse Manifest]
    B --> C[🔍 Audit Permissions]
    C --> D[🧪 Sandbox Check]
    D --> E[📥 Install to .ananke/skills/installed]
    E --> F[🔐 Update apm.lock]
    F --> G[✅ Activate Skill]

    classDef ingest fill:#FFE3E3,stroke:#C92A2A,color:#7A1E1E,stroke-width:2px;
    classDef govern fill:#E5DBFF,stroke:#5F3DC4,color:#3B2A8A,stroke-width:2px;
    classDef done fill:#D3F9D8,stroke:#2B8A3E,color:#1C5D2A,stroke-width:2px;
    class A ingest;
    class B,C,D govern;
    class E,F,G done;
```

## 📊 Module Progress Matrix

| Epic | Capability | Progress |
|---|---|---|
| A | Core + plugin discovery baseline | 🟡 Partial |
| B | Policy engine advanced semantics | 🟡 Partial |
| C | Full verification adapter contracts | 🟡 Partial |
| D | Spec lifecycle + drift + convergence | ✅ Strong baseline |
| E | CALM reconciliation and rendering | 🟡 Partial |
| F | Graph model/query/impact + provider baseline | 🟡 Partial |
| G | Hook orchestration | ⏳ Pending |
| H | MCP v2 full protocol and auth hardening | 🟡 Partial |
| I | APM resolver/sandbox/audit/lock/import | 🟡 Partial |
| J | Lifecycle real Jira/Bitbucket adapters | 🟡 Partial |
| K | Backend adapters (Copilot/Q/Kiro/Hermes) | ⏳ Pending |
| L | Full DAG run engine and compensation | 🟡 Partial |
| M | Supply-chain hardening and attestations | 🟡 Partial |
| O | Skill & Agent Registry | ✅ Python implementation; ⏳ Rust core, redb, tantivy |

## 🛣️ Next Implementation Wave

1. Implement true Jira/Bitbucket remote adapters with idempotency keys and retries.
2. Add MCP v2 Streamable HTTP and stronger auth policy mapping.
3. Add provider adapters (`graphifyy`, `code-review-graph`) and reconciliation levels.
4. Add APM bundles and untrusted skill runtime execution guards.
5. Add run-engine approvals, concurrency controls, and compensation strategy.
## 2026-09-16

- Completed docs creation and hosting baseline with MkDocs (`mkdocs.yml`, `docs/index.md`) and GitHub Pages deployment workflow (`.github/workflows/docs.yml`).
- Added config precedence layering (`defaults -> config.toml -> config.local.toml -> env`) with test coverage.
- Added `ananke config migrate` and `ananke evidence prune` command families with API/CLI/tests.
- Added release hardening: CycloneDX SBOM generation and provenance attestations in TestPyPI/PyPI workflows.
- Added security policy workflows: CodeQL, dependency-review, Scorecard, and workflow linting via actionlint + zizmor.

## 2026-09-19

- Implemented the Skill & Agent Registry (Epic O): `ananke registry`, `ananke skill`, `ananke agent`, `ananke sync`, registry-backed `apm` commands, registry MCP tools/resources, evidence hook (`registry-capabilities.json`), generated static documentation, and ~500 new tests.
- Documented in [Registry concept](../concepts/registry.md), [Registry Guide](registry-guide.md) and [Registry Reference](../reference/registry.md); test harness documented in [Testing](../concepts/testing.md).
- Follow-up the same day: Ed25519 signatures (computed, never self-asserted trust), pull-only remote federation with bearer-token server auth, optional similarity search, native (`notify`) file watching, DuckDB/SQLite analytics questions, `registry benchmark` with regression tracking, seeded fuzz targets (which found and fixed unwrapped errors on corrupt import archives), and enforcement of the test-harness quality-gate thresholds.
- Not implemented (tracked in the master spec §61): the Rust core (`ananke-registry-core`, PyO3) and the `redb`/`tantivy` accelerators — they need a Rust toolchain and build/CI work.

## 🧪 Quality Snapshot

- ✅ Ruff: passing
- ✅ mypy: passing
- ✅ pytest: passing
- ✅ End-to-end command smoke tests: passing

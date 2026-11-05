# Code Graph Intelligence

Ananke treats your repository as a **graph of semantic relationships**, not a flat directory tree.

![Code Graph Intelligence](../assets/code_graph_intelligence_infographic.jpg)

![Animated: an agent asks the graph for the blast radius of a change and gets focused context back](../assets/flows/code-graph-context.svg)

---

## Why graph context changes everything

Without graph intelligence:

```
Agent → Entire Repository → Huge Text Context
```

With graph intelligence:

```
Agent → Graph Query → Relevant Subgraph → Focused Context → Agent
```

The objective is not merely fewer tokens. It is **better context selection** — giving the agent exactly the code it needs to understand the impact of a change.

---

## Canonical graph model

All providers normalize to the same types.

### Node kinds

| Kind | Represents |
|---|---|
| `file` | Source file |
| `function` | Function definition |
| `class` | Class definition |
| `method` | Class method |
| `module` | Python module |
| `route` | HTTP API endpoint |
| `table` | Database table |
| `queue` | Message queue |
| `service` | Service component |
| `requirement` | Requirement artifact |
| `test` | Test function |
| `skill` | APM skill |
| `tool` | MCP tool |
| `architecture_component` | CALM component |

### Edge kinds

| Kind | Meaning |
|---|---|
| `imports` | Module/file imports another |
| `calls` | Function calls another |
| `inherits` | Class inherits from another |
| `implements` | Class implements interface |
| `reads` | Component reads from store |
| `writes` | Component writes to store |
| `publishes` | Component publishes event |
| `subscribes` | Component subscribes to event |
| `tests` | Test covers function/class |
| `realizes` | Implementation realizes spec |
| `depends_on` | Component depends on another |
| `owns` | File owns function/class |
| `violates` | Component violates constraint |
| `constrained_by` | Component constrained by policy |

### Edge provenance

Each edge carries:
- `origin`: `extracted | inferred | ambiguous | declared`
- `provider`: which adapter produced the edge
- `confidence`: float 0.0–1.0
- `source_location`: file:line where the relationship was found

---

## Blast radius analysis

When `authenticate()` changes, Ananke traverses the graph:

```mermaid
graph TD
    AUTH["🔴 authenticate()<br/>CHANGED"]
    REQUIRE["🟠 require_auth()"]
    LOGIN["🟠 login()"]
    ROUTE["🟡 protected_route()"]
    API["🟡 login_route()"]
    TESTAUTH["🟢 test_auth()"]
    TESTLOGIN["🟢 test_login()"]

    REQUIRE --> AUTH
    LOGIN --> AUTH
    ROUTE --> REQUIRE
    API --> LOGIN
    AUTH --> TESTAUTH
    LOGIN --> TESTLOGIN
```

This answers:
- What may break?
- Which tests matter?
- What architecture components are involved?
- Which routes rely on the changed symbol?

---

## Building and querying the graph

```bash
# Build/update the code graph
ananke graph build --project .

# Query the graph for a symbol
ananke graph query --text "authenticate"

# Calculate impact of changed files
ananke graph impact --files src/auth.py src/middleware.py

# Export the graph as JSON
ananke graph export --format json

# Check graph status
ananke graph status
```

---

## Graph providers

### Native (built-in)

AST-based Python analysis. Always available, no dependencies.

- **Extracts:** files, top-level functions and classes, and methods (`method:<path>:<Class>.<name>`).
- **Symbol metadata:** `line`, `end_line`, a one-line `doc` and, for functions and methods, a `signature` and
  `decorators`. File nodes carry their dotted `module`, `loc` and docstring.
- **Edges:** `owns` and `imports`, plus inferred `calls` and `inherits` (`origin: inferred`, `confidence: 0.8`,
  `source_location: <path>:<line>`).
- **How calls resolve:** through a file's own definitions, its `from … import` and `import … as` statements to
  other workspace files, `self.method()` and `Cls.method()`. Dynamic dispatch, `getattr` and re-exports beyond one
  level are not followed.
- **What it skips:** hidden directories (`.git`, `.venv`, caches), `node_modules`, `site-packages` and any virtual
  environment (a folder holding `pyvenv.cfg`).
- **Best for:** quick local analysis, CI without external tools, and Dev Guru's explorer reports.

### Graphifyy adapter

When `graphifyy` is installed, provides broad knowledge-graph enrichment and multi-source relationships. Falls back to native when unavailable.

```bash
pip install graphifyy  # optional; the command it installs is graphify
```

### code-review-graph adapter

When `code-review-graph` is installed, provides structural code review and incremental blast-radius analysis. Falls back to native when unavailable.

```bash
pip install code-review-graph  # optional
```

### Multi-provider reconciliation

When multiple providers run, edges are reconciled:

| Status | Meaning |
|---|---|
| `CONSENSUS` | Multiple providers agree |
| `SINGLE_SOURCE` | Only one provider found this edge |
| `CONFLICT` | Providers disagree |
| `UNKNOWN` | No provider has data |

---

## Impact report

`impacted_files` are the files that **depend on** the changed files: files that import them, or call into them. The
files a changed file imports are not impacted by the change (before 0.3.12 they were listed instead).

The impact report includes:

```json
{
  "changed_files": ["src/auth.py"],
  "impacted_symbols": ["function:src/auth.py:authenticate", "class:src/auth.py:AuthService"],
  "impacted_files": ["src/middleware.py"],
  "forbidden_edges": [],
  "blast_radius": 15,
  "summary": "1 changed, 14 impacted symbols, 1 impacted files"
}
```

---

## Architecture reconciliation

Ananke compares declared architecture (CALM) with observed architecture (graph):

```
DECLARED  = architecture intended by CALM
OBSERVED  = architecture found in source/graph
DELTA     = observed − declared / declared − observed
```

Findings include:
- Undeclared dependency
- Declared relationship not found in code
- Protocol mismatch
- Forbidden cross-domain edge
- Interface changed without model version bump

---
title: Repo intelligence
description: One analysis of a repository for products to present, its code map, data model, API pathways, end-to-end flows, architecture, quality, coverage and dependencies, with evidence for everything.
---

# Repo intelligence

`ananke app run repo.analyze` produces one artifact, `repo-intel.json`:

```json
{
  "schema": "ananke-repo-intel/1",
  "source_snapshot": "git:...",
  "content_digest": "sha256:...",
  "sections": {
    "overview": {"status": "found", "languages": {"python": 12}, "files": 20, "loc": 900, "source_roots": ["backend"],
                 "components": [{"id": "component:backend", "name": "backend", "path": "backend", "kind": "service",
                                 "language": "python", "files": 9, "loc": 500}], "limits": []},
    "data": {"status": "found", "limits": [],
             "tables": [{"id": "table:orders", "name": "orders", "schema": null, "source": "sqlalchemy", "evidence": "backend/app/models.py:12",
                         "columns": [{"name": "id", "type": "Integer", "nullable": false, "primary_key": true,
                                      "foreign_key": null, "evidence": "backend/app/models.py:13"}]}],
             "relationships": [{"from_table": "orders", "from_column": "customer_id", "to_table": "customers",
                                "to_column": "id", "cardinality": "many-to-one", "origin": "extracted", "evidence": "..."}],
             "usage": [{"symbol": "function:backend/app/services/orders.py:create_order", "table": "orders", "access": "write",
                        "origin": "inferred", "confidence": 0.8, "evidence": "..."}],
             "erd_mermaid": "erDiagram\n..."},
    "api": {"status": "found", "limits": [],
            "routes": [{"id": "route:POST /api/orders", "method": "POST", "path": "/api/orders",
                        "handler": "function:backend/app/routers/orders.py:create", "framework": "fastapi", "evidence": "..."}],
            "clients": [{"id": "client:frontend/src/api.ts:8", "method": "POST", "path": "/api/orders", "caller": "function:frontend/src/api.ts:createOrder",
                         "framework": "fetch", "evidence": "...", "route": "route:POST /api/orders"}],
            "unmatched_clients": [], "specs": []},
    "flows": {"status": "found", "limits": [],
              "flows": [{"id": "flow:POST /api/orders", "name": "POST /api/orders", "entry": "route:POST /api/orders", "truncated": false,
                         "steps": [{"node": "client:...", "kind": "client|route|handler|function|table", "label": "...", "evidence": "...", "access": null}],
                         "edges": [{"source": "...", "target": "...", "kind": "calls|handles|reads|writes|http", "origin": "extracted|inferred"}]}]},
    "calm": {"status": "found", "origin": "inferred", "limits": [],
             "model": {"schema_version": "0.1", "system": {"name": "shop"},
                       "components": [{"id": "backend", "name": "backend", "type": "service", "evidence": "..."}],
                       "relationships": [{"source": "frontend", "target": "backend", "type": "http", "evidence": "..."}]}},
    "quality": {"status": "found", "limits": [],
                "totals": {"files": 20, "loc": 900, "functions": 40, "classes": 5, "avg_complexity": 2.1, "max_complexity": 9,
                           "documented_ratio": 0.4, "test_files": 3, "test_functions": 12},
                "hotspots": [{"symbol": "function:...", "path": "...", "line": 3, "complexity": 9, "length": 40}],
                "files": [{"path": "...", "loc": 50, "functions": 3, "max_complexity": 4}]},
    "coverage": {"status": "found|not_found", "limits": [], "source": "coverage.xml", "line_rate": 0.8, "branch_rate": null,
                 "files": [{"path": "...", "line_rate": 0.9, "lines_covered": 9, "lines_total": 10}]},
    "dependencies": {"status": "scanned|not_scanned|failed", "limits": [], "scanner": "osv",
                     "packages": [{"ecosystem": "PyPI", "name": "flask", "version": "2.0.0", "manifest": "requirements.txt", "direct": true, "dev": false}],
                     "vulnerabilities": [{"ecosystem": "PyPI", "package": "flask", "version": "2.0.0", "id": "GHSA-...", "aliases": ["CVE-..."],
                                          "severity": "high", "fixed_in": "2.3.2", "summary": "..."}]}
  }
}
```

Each section also has `status: "none"` when the repo simply has nothing of that kind, and `limits`: plain sentences saying what
wasn't analysed.


## The data model

`sections.data` says which tables and fields exist, how they relate, and which code uses them. Nothing is imported or run: Python
is read with `ast`, SQL with a small linear tokenizer, and Prisma and TypeORM line by line.

**Tables come from:** SQLAlchemy declarative classes and `Table(...)`, SQLModel `table=True`, Django models, `.sql` files and SQL in
Python strings (`CREATE TABLE`, `ALTER TABLE ... ADD`, T-SQL's `GO` and `[brackets]` included), Alembic `upgrade()`, `.prisma` models and
simple TypeORM `@Entity` classes. One table found in several places is one table, whatever the case of its name. ORM definitions
win over migrations, and migrations over SQL. `source` names the strongest.

**A table with no definition still appears.** `source` is `inferred-from-sql` when only SQL uses it, or `inferred-from-reference` when
only a foreign key points at it. It lists only the columns that were stated, and a limit says the definition wasn't found.

**Relationships** have `origin: extracted` when they come from a foreign key or a many-to-many declaration. `inferred` means a
`JOIN ... ON a.x = b.y` where one side is the other table's key. A join says nothing otherwise, and no join is invented. The ERD
draws inferred links dashed.

**Usage** uses the code graph's symbol ids, or `file:<path>` for top-level code. A nested function counts for the function around it.
Python's ORM calls are followed through imports, so a different class that shares a model's name isn't the model. SQL held in a
constant counts for the function that refers to it, cited at that line. `confidence` is highest for a call that plainly reads or
writes, and lower for building a row or for SQL outside any database call.

**A string is SQL** only if it parses as a statement keyword followed by a table clause. Outside a database call it must also read
like SQL rather than a sentence, so `"Select a file from the list"` isn't a query. Tables named like English words (`the`, `a`) or
placeholders (`table_name`) aren't read from SQL.

**Not read, and said so in `limits` when it applies:**

- notebooks;
- test and fixture files, which aren't the application's data model;
- SQL and ORM use in JavaScript or TypeScript, so Prisma and TypeORM give tables but no usage;
- other ORMs (Peewee, Tortoise, Pony, MongoEngine, Beanie and similar) and data code in Java, Kotlin, Go, Ruby, PHP, C# or Rust;
- tables chosen at run time, such as `f"SELECT * FROM {name}"`;
- SQL only listed at module level, which defines tables if it's DDL but isn't counted as use, because nothing says it runs;
- views, which aren't listed as tables.

Migrations aren't replayed: a `DROP` or `RENAME` is counted in `limits` and the table keeps what it once had. Temporary tables are
left out. A repository method that takes its model as a variable (`session.query(self.model)`) isn't traced. Mermaid names are reduced to letters, digits and
underscores, so a name in another script appears as `unnamed` in the ERD and keeps its real name in `tables`.


## Rules every section follows

- **Evidence.** Each table, field, route, usage and step cites `path:line`.
- **Unknown is not zero.** `status` is `found`, `none` (the repo has nothing of this kind), `not_found`, `not_scanned`, `failed` or
  `not_built`, and `limits` says in plain words what wasn't analysed.
- **No effects.** Analysis reads files and runs nothing. The CVE lookup uses the network only when asked.

## The analyzers

### Overview

Languages are counted by file, from extensions, with a notebook's language taken from its metadata. `files` is every file outside
fixture folders, and `loc` counts the non-blank lines of source files. `source_roots` are the folders Python imports resolve from;
a repo with no Python gets its `src` folders instead.

Each component has an `evidence` field saying why it's there and why it's that kind:

| Kind | Signal |
| --- | --- |
| `service` | a server framework in its manifest (`fastapi`, `express`, ...), a compose service that builds it, or a Dockerfile |
| `frontend` | a UI framework in its manifest (`react`, `@angular/core`, `astro`, ...), or a name like `frontend` or `web` with a `package.json` |
| `worker` | a name like `worker`, `jobs` or `email-worker`, a `celery` command in compose, or a queue library and no server framework |
| `database` | a compose image like `postgres` or `redis`, or a folder of SQL schema or migrations |
| `library` | a manifest or a Python package with none of the above |
| `notebooks`, `tests` | a folder named for them, or one that's mostly notebooks |

A folder is a component when its manifest is at most two levels down. A top-level folder holding three or more manifests deeper
than that is listed in `separate_projects`, and isn't a component or part of the root's counts. A component's kind comes from
files alone, so a framework that's only imported, never declared, isn't seen. Compose `extends`, Helm and Kubernetes manifests
aren't read.

### CALM

An inferred model in the shape of `.ananke/architecture/system.calm.json`, which passes `validate_calm`. Each relationship has both
`type` and `kind`, because that validator reads `kind`. Links come from compose `depends_on` and from imports that cross component
folders, and each cites where it was seen. Compose `depends_on` counts as evidence of a frontend calling a backend. Tests aren't in
the model, and notebooks are only when nothing else is.

Nothing is ever written. If a declared model exists, it's read as it is and `drift` lists the components and relationships in one
and not the other. An empty declared model, like the one `ananke` creates, drifts by every inferred component. Links that exist
only in code (a URL in an environment variable, an HTTP call with no `depends_on`) aren't inferred here; `api` matches clients to
routes.

### Quality

Python is measured from its syntax tree. Cyclomatic complexity is 1 plus each `if`, `elif`, `for`, `while`, `except`, boolean
operator, comprehension condition, ternary, `match` case other than a bare `case _:`, and case guard. A nested function has its own
figure. JavaScript and TypeScript are an estimate from lines and branch keywords, and say so with `estimated: true`.

Test files, by name (`test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.ts`, ...) or folder (`tests`, `__tests__`, ...), count only in
`test_files` and `test_functions`. Under `src`, only `tests` and `__tests__` folders count, so `src/app/spec` stays code. Fixture
folders aren't counted. Not measured, each noted in `limits`: notebooks, other languages, `.astro`, `.vue` and `.svelte` files, and
minified scripts. A file that doesn't parse is a limit.

### Coverage

Existing reports only, and tests never run: Cobertura (`coverage.xml`, `cobertura.xml`, `cobertura-coverage.xml`), coverage.py
(`coverage.json`), lcov (`lcov.info`) and Istanbul (`coverage-final.json`, `coverage-summary.json`). They're looked for in the
repository root, in each top-level folder and component, and in a `coverage`, `reports` or `test-results` folder in any of those,
even if Git ignores them. Reports of different folders combine, weighted by lines, and `sources` lists them.

No usable report is `not_found` with a limit saying how to make one, never 0%. `branch_rate` is `null` when branches weren't
measured, even if the report writes `branch-rate="0"`. XML must be UTF-8, and a DOCTYPE or an entity declaration is refused. Paths are
mapped to repository files through the report's source folders and trailing path parts; anything else is counted in `limits`. A
report older than the files it measures gets a limit, judged by file times, which a fresh clone doesn't preserve. `.coverage` data
files aren't read.

### Dependencies

Read: `requirements*.txt`, `pyproject.toml` (PEP 621, dependency groups, Poetry, uv), `Pipfile`, `package.json`, and the lockfiles
`uv.lock`, `poetry.lock`, `Pipfile.lock`, `package-lock.json` (all versions), `yarn.lock` (v1) and `pnpm-lock.yaml`. A locked version
replaces a range, a `package.json` or `pyproject.toml` can use a lock further up, and a pin that differs from the lock is kept as
well. A package with no pin or lock has `version: null`, and is noted as uncheckable. Not read, and noted: Yarn Berry lockfiles and
other ecosystems' manifests. In a `requirements.txt` made by `pip-compile`, everything reads as direct. Manifests in fixture
folders aren't dependencies.

`vulnerabilities` is `null` unless a scan ran, so neither `not_scanned` nor `failed` can read as clean. A scan happens only when
asked for. It sends each pinned package's name and version to [OSV](https://osv.dev), and nothing else. Records of the same flaw
(`aliases` overlapping) are merged, keeping the worst severity. `severity` is the database's word or the CVSS 3 base score, otherwise
`unknown`: CVSS 4 vectors aren't scored. `fixed_in` is the lowest fix newer than the installed version, or the lowest fix if none is
newer; versions are compared without a full PEP 440 or SemVer parser.

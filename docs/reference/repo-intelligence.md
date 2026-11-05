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
            "unmatched_clients": [], "specs": [{"path": "docs/openapi.yaml", "title": "Shop", "version": "3.0.0", "routes": 12, "missing_from_code": []}]},
    "flows": {"status": "found", "limits": [],
              "flows": [{"id": "flow:POST /api/orders", "name": "POST /api/orders", "entry": "route:POST /api/orders", "truncated": false,
                         "steps": [{"node": "client:...", "kind": "client|route|handler|function|class|file|table", "label": "...", "evidence": "...", "access": null}],
                         "edges": [{"source": "...", "target": "...", "kind": "calls|handles|reads|writes|http", "origin": "extracted|inferred"}]}],
              "entrypoints": [{"id": "entrypoint:cli:tool", "kind": "main|cli", "name": "tool", "node": "function:...", "evidence": "...", "truncated": false, "steps": [], "edges": []}]},
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


## API pathways

`sections.api` lists the routes a repository serves and the HTTP calls it makes, and joins them. Nothing is imported or run: Python
is read with `ast`, and JavaScript and TypeScript with a linear scanner that keeps line numbers, so a comment or a string can't make
a route.

**Routes come from** FastAPI and Flask, including prefixes joined across files (`APIRouter(prefix=)`, `include_router`, blueprints,
`register_blueprint`, `add_api_route`, `add_url_rule`); Django `urls.py` (`path`, `re_path`, `include`, class-based views); Express
(`app.get`, `router.route`, `app.use('/prefix', router)`); and NestJS (`@Controller`, `@Get`, `setGlobalPrefix`). Parameters always
read `{name}`, so Flask's `<int:id>` and Express's `:id` are both `{id}`. `method` is `ANY` when the code doesn't limit it, as a
Django view doesn't.

- A prefix held in a constant or in a settings class's default is followed. One known only at run time is left out, and the route
  has `prefix_unknown: true`. A router nothing visibly includes has `unmounted: true`, and its path has only its own prefix.
- `handler` is the graph's symbol id. A handler written inside another function, as in a `create_app()`, has the id of the function
  around it, with `handler_name` and `handler_lines`. An inline Express handler is `file:<path>` with the name `<inline>`. A handler
  that can't be resolved is `null`, with the name as written.
- The same method and path found twice, as two services have, stay as two routes. The first keeps `route:<METHOD> <path>`, and later
  ones add their place: `route:GET /health (svc/app.py:3)`.
- A route written to catch everything at the root has `catch_all: true`, and no client matches it.

**Clients come from** `fetch`, axios (including `axios.create({baseURL})` instances imported from another file), Angular's
`HttpClient`, `requests` and `httpx`. `caller` is the graph's symbol, or `file:<path>` for top-level code. Template literals,
concatenation and constants are followed, and a value only known at run time becomes a parameter: `` `/orders/${id}` `` is
`/orders/{id}`. `method` is `UNKNOWN` when an options object is a variable or spreads one in.

- Only a URL that's truly unknown is skipped, such as a helper's parameter, and a limit counts those. So calls through your own
  `request(path)` wrapper aren't listed; the one `fetch` inside it is the skipped call.
- `${API}/orders` keeps its path and is marked `base_unknown: true`.
- A URL on another host has `external: true` and `host`, and is never matched. `localhost`, loopback addresses and a name with no
  dot, like the compose service `backend`, count as local.
- A file path such as `/data.json` that matches no route has `static: true`.

**Matching** goes by method and path shape. Parameters match any parameter name, a literal in the client can match a parameter in the
route, and a literal in a route always wins over a parameter: `/customers/reports/sales` finds its own route, not
`/customers/{id}/sales`. A parameter in the client never matches a literal in the route. When two services have the same route, the
one with the longest shared folder path wins; a tie has `ambiguous: true`. A client with an `UNKNOWN` method matches only when one
route has that shape. A client with no route is in `clients` with no `route` field, and also in `unmatched_clients`.

**A proxy that strips a prefix** is followed only if a config says so: `pathRewrite`, Vite's `rewrite` with an empty replacement, or
an nginx `location /api/ { proxy_pass http://backend/; }`. It applies to clients under the config's folder, and the client has
`proxy`. Anything else stays unmatched rather than guessed.

**OpenAPI and Swagger** files named `openapi*`, `swagger*` or `api-docs*`, as JSON or YAML, are listed in `specs`. YAML is read line by
line, so flow-style `paths` aren't seen. Operations missing from the code are in `missing_from_code` and in a limit, and never
become routes.

**Not read, and said in `limits` when it applies:** notebooks; test and fixture files; scripts inside `.vue`, `.svelte`, `.astro` and
`.html` files; `http.server` handlers, aiohttp, Tornado, Starlette and Sanic routes; Flask-RESTful resources; Django REST framework
routers. These aren't read and may not be noted: Next.js and other file-based routing, Fastify, Koa and Hono, other HTTP client
libraries (`ky`, `got`, `superagent`, `aiohttp`), Express routes on regular expressions, GraphQL and WebSocket clients. A class-based view's inherited methods aren't
seen, so it's listed as `ANY` on the class. Only a literal list is followed in `for r in [...]: app.include_router(r)`, and a
`re_path` that says more than literals and named groups is counted and left out.

## End-to-end flows

`sections.flows` has one flow per route, from whoever calls it down to the tables it touches. It joins what the other sections found
and adds nothing: each edge exists in the code graph (`calls`), in `api` (`handles`, `http`, and a caller's link to its client), or
in `data`'s usage (`reads`, `writes`).

A flow lists, in order, the callers one level above each client, the client's caller, the client, the route, the handler, the
functions reached breadth-first through `calls`, and the tables each uses. A table's `access` is `read`, `write` or `read_write`. A route
nothing calls and that calls nothing is just its route and handler, and one with no known handler is just its route. Cycles end.
`truncated: true` means a budget stopped it: 8 calls deep, 60 symbols, or 20 clients.

A handler written inside another function shares that function's symbol in the graph, so its flow takes only the calls and tables
first seen within its own lines. A call made first by a neighbouring handler is missed there, and a limit says this can happen.
Flows inherit what the graph can't see, such as dynamic dispatch.

`entrypoints` lists non-HTTP starts: `__main__` blocks, `[project.scripts]` in `pyproject.toml`, `bin` in `package.json`, and
`@x.command()` functions in a file that imports Click or Typer. A command whose function was found has steps. The graph doesn't hold
calls made at the top of a module, so a `__main__` block has none. Background jobs, schedules and message consumers aren't traced,
and a limit says so.

## Notebooks

A folder of notebooks needs nothing special. A file a notebook writes with `%%writefile` or `%%file` counts as a file at that path, relative to the notebook, in every section, even before anyone runs it: its functions, imports, routes, tables, requirements and Compose services. Nothing is run or created.

- A file really on disk wins, since that's what would be imported.
- Later cells replace earlier ones, and `-a` appends, as when it runs. If different notebooks write one path differently, the first by path is used, and the section says how many differ.
- A computed path, or one outside the folder, isn't read, and the section says how many.

The `notebooks` section explains each one from its own text and code:

| Field | What it holds |
| --- | --- |
| `notebooks[].title`, `purpose` | Its first heading, and the first paragraph under its headings |
| `notebooks[].outline` | Each heading, its cell and a short summary |
| `notebooks[].written` | Files it writes: path, cell, language, lines and what they define |
| `notebooks[].imports`, `uses`, `defines` | Outside packages, files in this repository, and its own functions and classes |
| `notebooks[].reads`, `writes` | Data files named with a literal path |
| `notebooks[].same_as` | The notebook this one repeats, such as an executed copy. Copies don't add links |
| `groups` | Notebooks by folder, in natural order: Week-2 before Week-10 |
| `links` | One notebook importing a file another writes, or reading data another writes |

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

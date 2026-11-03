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


## Rules every section follows

- **Evidence.** Each table, field, route, usage and step cites `path:line`.
- **Unknown is not zero.** `status` is `found`, `none` (the repo has nothing of this kind), `not_found`, `not_scanned`, `failed` or
  `not_built`, and `limits` says in plain words what wasn't analysed.
- **No effects.** Analysis reads files and runs nothing. The CVE lookup uses the network only when asked.

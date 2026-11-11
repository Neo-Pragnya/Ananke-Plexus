---
title: Connectors
description: Built-in typed connectors that turn source, test results, API specs and build logs into documents a workflow can use, each run in its own process with its output checked.
---

# Connectors

A connector turns one kind of file into a typed document a workflow can use. Each one has typed input and output
ports, so [workflow assembly](workflow-assembly.md) can connect it, suggest it and find it for a gap like any other
capability.

| Connector | Input port | Output port | What it does |
| --- | --- | --- | --- |
| `code-graph` | `python-source/v1` (a folder) | `code-graph/v1` | Ananke's native code graph, built in memory: nothing is written into the folder |
| `junit-to-sarif` | `junit-xml/v1` | `sarif/2.1.0` | each failure, error and skip with its file and line |
| `openapi-routes` | `openapi/v3` (JSON or YAML) | `api-routes/v1` | method, path, operation, parameters and responses for every route |
| `log-triage` | `build-log/v1` | `findings/v1` | a Jenkins console or container log grouped into findings: build result, tracebacks, out of memory, exit codes, errors and warnings, each with its lines |

```bash
ananke connector list
ananke connector run junit-to-sarif results.xml -o results.sarif
ananke connector run log-triage console.log
ananke connector register --approve      # add them to this project's registry as tools with typed ports
```

## Two rules

- **Each one runs in its own process.** A connector runs in a separate, isolated Python process (`python -I`), with
  only the environment it needs to start: no proxy or token variables. It reads the input it's given and writes
  nothing.
- **Only checked output is kept.** Ananke checks what a connector returns against its output port (SARIF 2.1.0 with
  a tool and valid levels, routes with a method and a path, findings with a level and lines, a graph whose edges start
  at its nodes). Output that fails is an error (`UNVERIFIED_OUTPUT`), and no file is written.

Input that can't be read is refused with `INVALID_INPUT`: a JUnit file with a DTD or entities, an OpenAPI 2 document,
a missing file or one over 50 MB. None of the connectors uses the network or anything outside Ananke and the standard
library.

## In a workflow

`ananke connector register` adds them to the project's registry, so `ananke app workflow-gaps` can name one when a
draft needs, say, `findings/v1`. Without `--approve` they're registered but not yet trusted, and the gap lists them as
compatible but not authorized until someone approves them.

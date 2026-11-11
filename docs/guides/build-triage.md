---
title: Build triage
description: Find why a Jenkins build failed from its console log, its Jenkinsfile and what changed, with no access to the CI server.
---

# Build triage

When a pipeline fails, the first error is usually the cause and the rest follow from it. `ananke triage jenkins`
finds that first error, separates the cascade from it, points at the changes near it and says where to rerun from.
It reads files you already have, so it needs no Jenkins access, and it sends nothing anywhere.

```bash
ananke triage jenkins console.log                                  # from the log alone
ananke triage jenkins console.log --jenkinsfile Jenkinsfile --base origin/main
ananke triage jenkins console.log --changed api/orders.py --json -o triage.json
```

Download the console log from the build page (*Console Output → View as plain text*). Exit `1` means a failure was
found, `0` that none was, and `2` that the file isn't a Jenkins console log.

## What it reports

| Part | How it's found |
| --- | --- |
| **Stages** | the pipeline markers (`[Pipeline] { (Test)`), with their lines; times too when the Timestamper plugin is on |
| **First failure** | the earliest stage that failed on its own: a non-zero exit, a traceback, failing tests, a compile error, out of memory, or an error line |
| **Cascade** | every later failure, and every stage *skipped due to earlier failure*. Fix the first failure; these should clear |
| **Related changes** | changed files the failing stage's commands point at: a path they name (`pytest tests/api`) or the tool they run (pytest and `.py` files or `pyproject.toml`; npm and `package.json`; docker and `Dockerfile`) |
| **Plan** | fix the first failure, check the related changes, rerun from that stage |

A related change is evidence to look at, not proof that it caused the failure.

## In a workflow

The same triage is the `jenkins-triage` [connector](connectors.md) (`build-log/v1` → `triage-report/v1`), so a
workflow can use it and its output is checked before it's kept.

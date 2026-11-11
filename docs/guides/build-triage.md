---
title: Build triage
description: Find why a Jenkins build failed, and what to fix first across scanners, tests, coverage and dependencies, from files you already have.
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

## What to fix first

`ananke triage findings` puts every finding the repository's reports hold in one list, ranked against what changed:

```bash
ananke triage findings --base origin/main
ananke triage findings --changed api/orders.py --check-vulnerabilities --json
```

| Kind | From |
| --- | --- |
| `code` | SARIF reports (CodeQL, Semgrep, Bandit, Trivy and others), with the rule's own remediation text |
| `test` | failing tests in JUnit XML |
| `coverage` | a changed file whose line coverage is under 60% |
| `dependency` | vulnerable packages from the dependency check, each with its package, version and advisory, and the project files that import it |

Each finding has a stable id and is marked **changed** (in a changed file), **impacted** (in a file that imports a
changed file, directly or not) or neither; a dependency is **reachable** when project code imports it. They're
ranked into *fix now*, *fix soon* and *later*, each with a suggestion. A fix version is given only when the advisory
names one; otherwise it says there isn't one. The report ends with the gates to rerun: the tools whose reports these
came from. Nothing is run, and with no reports the findings are unknown, not zero.

## API contract gaps

`ananke triage contracts` checks an API's routes against everything that should agree with them:

```bash
ananke triage contracts
ananke triage contracts --json -o contract-gaps.json
```

- **Tested by:** HTTP calls in test files (`client.get("/orders/1")`, `requests.post(f"{BASE}/orders")`,
  `fetch("/x", { method: "DELETE" })`), matched to a route by method and path, with parameters matching any value.
- **Called by:** the frontend and service calls the repository analysis matched to the route.
- **Mismatches:** calls and test calls to routes that don't exist, and operations an OpenAPI file promises that the code
  doesn't serve.
- **Recommended contract tests** for each untested route: the success case, a missing resource for each path
  parameter, an invalid body for `POST`, `PUT` and `PATCH`, and a repeated `DELETE`. They're suggestions named after
  the route: nothing is run or written.

A route nothing in the project calls may still be used by another service; it's listed so someone can check.

## Infrastructure

`ananke triage infra` reads what the repository declares and says how it fits together:

```bash
ananke triage infra
ananke triage infra --project ../shop --project ../payments      # several repositories, linked
ananke triage infra --log compose.log                            # from `docker compose logs > compose.log`
```

- **Resources**, each with its file and line: Compose services (image, build folder, ports, health check),
  Dockerfiles (base image, ports, health check, whether it runs as root), Kubernetes objects (images, probes) and
  Terraform resources.
- **Links**: `depends_on`, a service to the Dockerfile it builds, a Kubernetes Service to the workloads its selector
  matches, Terraform references, and across repositories, a URL that names another project's service.
- **Warnings**: containers running as root, unpinned base images, workloads without probes, and services that start
  after another without waiting for it to be healthy.
- **A container log**, split by service and triaged, each service with its code folder, health check and what it
  depends on, and the first service to fail.
- **A safe order to check it in**: dry runs first (`docker compose config`, `terraform validate`,
  `kubectl apply --dry-run=client`), then builds, then each service after what it depends on, checking it at each step.
  These are commands to run yourself: nothing is started, applied or contacted.

## Contract tests from an OpenAPI document

```bash
ananke triage api-tests openapi.yaml                                   # tests/api_contract/test_api_contract.py
ananke triage api-tests openapi.yaml --run http://127.0.0.1:8000       # run them against your local service
```

For every operation it writes test cases: the positive case, an unknown resource for each path parameter, a request
without credentials when the operation is secured, a body with a required field left out and one with a field of the
wrong type, a five-second response limit, and repeating a `PUT` or `DELETE`. Request bodies come from each schema's
`example`, or its required fields filled by type: edit them to real values. It also lists operations that document no
`404`, `400`/`422` or `401`/`403` for a failure they can have.

The tests only run when you ask, and only against a service on this machine (`127.0.0.1`, `localhost`, `::1`). The
results are kept as JUnit XML, and the report marks each case **executed** (passed or failed) or **suggested**. A
generated test never counts as a passing one. Exit `1` if an executed case failed.


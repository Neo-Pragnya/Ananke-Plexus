# Advanced insights

These commands build on the repository analysis to answer harder questions: what changed in the architecture,
what a change would reach, which tests are missing, whether a notebook is reproducible, whether what's running
matches the repository, and why a build failed. Each one:

- is **deterministic**: the same inputs give the same result and the same digest;
- **runs nothing** unless you ask (`--run-tests` and `--rerun` are the only exceptions), and reads past revisions
  with `git archive` or a throwaway worktree, so your working tree is never touched;
- labels every claim as **observed** (read from a file or report), **simulated** (predicted from the code graph),
  **inferred**, **assumed** (your own number) or a **hypothesis**.

| Question | Command |
| --- | --- |
| What changed in the architecture between two revisions? | `ananke graph diff --base v1.2 [--head v1.3]` |
| What would this change reach? | `ananke graph whatif --change app/pricing.py [--both]` |
| How far can I trust that here? | `ananke graph whatif --backtest 40 --depth 1` |
| One model of the system, with the age of each fact | `ananke graph twin [--snapshot FILE] [--openapi URL]` |
| Which tests are missing? | `ananke triage test-gaps --coverage coverage.xml --junit junit.xml --base main` |
| Is this notebook reproducible? | `ananke triage notebooks [--notebook N.ipynb]` |
| Does what's running match the repository? | `ananke triage drift --snapshot kubectl.json --openapi http://127.0.0.1:8000/openapi.json` |
| Is this patch safe to raise as a PR? | `ananke triage patch fix.patch --run-tests` then `--approve DIGEST --branch fix/x` |
| What ties these repositories together? | `ananke triage mesh --project a --project b` |
| Why did this build fail? | `ananke triage incident console.txt --project . --last-good v1.2` |
| Is graph context worth its tokens here? | `ananke context observe questions.json --record` |
| Which option should we choose? | `ananke arch decide decision.yaml` |
| CVE checks without the network | `ananke cve import PyPI-all.zip` then `ANANKE_OSV_DB=... --check-vulnerabilities` |

## Architecture time machine

`graph diff` builds the code graph at two revisions and compares them: nodes and relationships (imports, calls,
inheritance, data reads and writes) added and removed, and new or dropped links **between components**. Components
are the folders just below the source root (`plexus/cli`, `plexus/intel` in a src layout). A symbol that leaves one
file while one of the same kind and name appears in another is a *likely move*, a hypothesis.

## What-if simulation, and how far to trust it

`graph whatif` walks the graph backwards from a change: who calls, imports, inherits from or tests it, with the chain
and a confidence (inferred edges count 0.7, ambiguous 0.4). `--both` also follows what the change depends on. The
result lists the tests to run, the routes whose handlers are reached and the tables they touch, all *simulated*.

`--backtest N` measures it on the repository's own history: for each recent commit that changed several source files,
it simulates a change to one and checks how many of the others it predicted, against a naive baseline (files in the
same folder). Measured on three repositories at depth 1:

| Repository | Dependants only (recall) | Both directions (recall / precision) | Same-folder baseline (recall / precision) |
| --- | --- | --- | --- |
| Ananke Plexus (40 commits) | 0.085 | 0.242 / 0.078 | 0.141 / 0.096 |
| Dex Trove (21 commits, depth 2) | 0.007 | 0.441 / 0.007 | 0.105 / 0.042 |
| Dev Guru (25 commits, depth 2) | 0.046 | 0.573 / 0.112 | 0.096 / 0.219 |

Following dependants only predicts co-changes *worse* than the folder baseline; following both directions one step
beats it on recall at similar precision. Deeper walks reach almost everything and precision collapses. Use `--both
--depth 1` to ask "what else will I probably touch", and the default to choose tests to run.

## Test gaps

`triage test-gaps` builds pytest skeletons from three kinds of evidence, each citing it: partly taken branches in a
coverage report (the function, the condition's line and the arc not taken), failing cases in JUnit XML, and API
operations whose handlers changed since `--base` with the contract findings that apply. Every generated test fails
until it's written: a skeleton marks a gap, it never closes one. Reports with a DOCTYPE or entities are refused.

## Notebook reproducibility

`triage notebooks` reads each `.ipynb` and reports execution counts out of cell order, code cells never run, hidden
runs (counts missing from the saved cells), names used before the cell that defines them or never defined at all,
saved error outputs, imports not declared in a requirements file next to the notebook or above it (and not installed
by the notebook itself), and relative input files that aren't in the project. The verdict is *likely reproducible*,
*reproducible with care* or *not reproducible as saved*; only running the notebook proves it.

## Drift between the repository and what's running

`triage drift` compares Compose services, Kubernetes workloads and services, Terraform resources and the routes the
code serves with snapshots you capture: `kubectl get ... -o json`, `docker inspect`, `docker compose ps --format
json`, a `.tfstate` or `terraform show -json`, and the OpenAPI a running service serves (a file, or a URL on this
machine only). It reports what's declared but not running and the reverse, different images, replicas, ports and
missing environment variable **names** (values are never read), and operations served by one side only.
`--fail-on-drift` exits 1 when anything differs.

## The review-to-fix loop

`triage patch fix.patch` applies a unified diff in a throwaway worktree at `--base`, measures the change (graph
relationships, contract findings, highest complexity) before and after, and lists the tests the change reaches. With
`--run-tests` it runs them there, against the patched code. It prints an **approval digest** covering the patch and
the facts (not timings); `--approve DIGEST --branch fix/x` commits the patch to a new local branch, refused for any
other digest, a patch that changed, or tests that didn't pass. Push the branch and open the pull request yourself or
with the Dex GitHub server's approval-gated tools.

## Contract mesh

`triage mesh` adds event topics (Kafka, SNS, SQS, RabbitMQ, Redis, Node producers and consumers) and datasets
(tables, files and object-store paths, including a path held in a variable one step before it's written or read) to
the HTTP links of `triage topology`, and lists the gaps: topics nobody subscribes to or publishes, object-store data
no repository writes, and calls nothing serves.

## Incident causality

`triage incident console.txt` links a failed build to the commit it checked out, the stage and the shell step that
failed, the containers and Compose services the log names nearby, and, with `--service-logs`, the errors those
services logged. With `--last-good` it ranks the commits since the last good build by whether they changed the
failing file or code it reaches; a suspect is a hypothesis. Jenkins console logs and GitHub Actions logs (`gh run
view --log-failed`) both work; pass `--commit` when the log doesn't name the revision.

## Context budget observatory

`context observe questions.json` answers a fixed question set at several budgets and reports recall, tokens, the
full-file baseline, recall per thousand tokens and the **knee** (the smallest budget with the best recall).
`--record` appends the run to `.ananke/context/observatory.jsonl` and compares it with the previous one, flagging
questions whose recall dropped or whose cost rose by more than a fifth. It found that a question worded like a test's
name ranked the test above the code it tests; a test that matches now lends its rank to the code it calls, which
raised recall on Ananke's own question set from 0.833 to 0.917.

## Evidence-first decisions

`arch decide decision.yaml` writes an ADR whose options carry measured evidence: code and tests reached
(simulated), hotspots touched (observed), new dependencies and their known vulnerabilities (observed, or unknown
without a dependency check), and your own assumptions with their source. Options are ranked by the weights you give,
with every number shown. The status stays *proposed*.

## Digital twin

`graph twin` joins, per component, the source (files, lines, hotspots), the contract (API operations, tables), the
infrastructure that deploys it, its verification (coverage, findings) and, with snapshots, its runtime. Each part says
whether it's observed, inferred or unknown, and a coverage or findings report older than the code it describes is
marked **stale**.

## Offline CVE data

`ananke cve import` loads an OSV export (for example
`https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip`) into SQLite. With `ANANKE_OSV_DB` set,
`--check-vulnerabilities` answers from it and sends nothing anywhere; the report says when the data was imported and
which ecosystems it doesn't cover. On a real project with 17 PyPI advisories, the offline answer matched osv.dev
exactly.

## Gates you ask to rerun

`ananke triage findings --rerun ruff --rerun tests` runs the named gates now, each as a fixed command with a time
limit, writing SARIF to `ananke-gates/` where the tool can and comparing the gate's findings before and after. A tool
that isn't installed is *not installed*, never passing; `tests` reruns only the failing test ids.

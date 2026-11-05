# Changelog

All notable changes to Ananke Plexus are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — [SemVer](https://semver.org/).

---

## [Unreleased]

---

## [0.3.30] — 2026-11-05

Everything in 0.3.29, which never reached PyPI: its own new release gate stopped it, because the release guide quoted the keychain command it was explaining. The guide now describes it instead.

### Added

- **Two more CVE databases in the release gate.** When installed, Grype (through a Syft SBOM) and osv-scanner check the resolved dependency tree alongside pip-audit, and any finding blocks. Each reads a different advisory database, so one that lags doesn't let a CVE through.

---

## [0.3.29] — 2026-11-05

Package firewalls quarantined Ananke and, through it, Dev Guru. Every version scanned showed the same causes, so it wasn't new code.

### Fixed

- **The wheel carried a 3.5 MB minified Mermaid bundle** for the offline docs, with code under other licences (DOMPurify and others) that the package didn't declare. GuardDog flags it as `threat-runtime-obfuscation`. It's gone, and the package ships no third-party script. Offline, `ananke docs build` shows a diagram as its Mermaid source, and the website still draws it.
- **Keychain secrets were read by running `security find-generic-password … -w`**, the same command infostealers use. `{ keychain = "name" }` now reads that one entry through `keyring`: the macOS Keychain, Windows Credential Manager (new) or the Secret Service. It needs `pip install 'ananke-plexus[keychain]'`, and says so if it's missing. `account = "..."` picks an entry when a service has several.
- The docs no longer suggest that command as a `--*-token-cmd` example.

### Added

- **A release gate that blocks what got the package quarantined.** `scripts/scan_dist.py` now stops a release for:
  - minified or third-party code whose licence isn't in `License-Expression`;
  - malware-shaped code anywhere in the wheel or sdist, docs included: keychain dumps, browser password stores, SSH and cloud key reads, pipe-to-shell, encoded PowerShell, running decoded data, chat webhooks, tunnels, and hidden Unicode in code;
  - any GuardDog rule nobody has reviewed.

  A reviewed finding goes in `.scan-dist.toml` with a reason, and can be limited to the files it was reviewed in. On the published 0.3.28, the gate stops it for exactly these causes.

---

## [0.3.28] — 2026-11-05

### Added

- **`read_sources(paths)` on the app facade.** Reads many files in one call, with the source snapshot worked out once rather than once per file. Each file comes back as `read_source` gives it, and a file it can't read is listed under `problems` with its code.

### Fixed

- **Windows: paths in the code graph used backslashes.** File ids, paths and import targets were written with the platform's separator, so on Windows imports between a repository's own files weren't matched, folders collapsed into one package, and about half the calls were lost. Every path is now written with forward slashes on every platform. A new CI job builds the same graph on Linux, macOS and Windows and checks the exact counts.

### Changed

- **Asking for the same analysis twice reuses the first answer.** `repo.analyze` keeps its report in memory for the sources it read. Within one process, such as one `dev explore` or one `dev serve`, the same question gets the same answer without running again. Any edit makes a new analysis, and so does a coverage report that appears or changes, even when Git ignores it. A CVE check always runs again, because what's known about a package changes while its code doesn't. Nothing is written to the repository.
- **The repository map reuses that analysis** instead of running a second one.
- **An analysis reads the code graph a lookup already kept** for the same sources, instead of building another. It never writes one.

With Dev Guru 0.11.0, which reads source with `read_sources`, `dev explore` on a 700-file repository went from 40 seconds to 13, with the same report.

---

## [0.3.27] — 2026-11-05

### Added

- **A `findings` section in `repo.analyze`.** Scanner findings from SARIF reports, worst first, with their source and fix, and test results from JUnit reports, naming what failed. Nothing is run. With no report, the section says it's unknown, not zero.

---

## [0.3.26] — 2026-11-05

### Added

- **`ananke process`: business processes from reviewed text.** `extract` reads a numbered list, where each step names who does it, into lanes, tasks, decisions and links, and each cites its line. It can write BPMN 2.0, which other BPMN tools open. `check` validates IDs, links and reachability, and that a file still says what the document says. What doesn't hold together is reported, not repaired, and a process is never marked executable. Guide: [Business processes](docs/guides/business-processes.md).

---

## [0.3.25] — 2026-11-05

### Added

- **Code inside `%%writefile` cells now counts.** Course and research repositories often keep their real code in notebook cells, so a folder could look nearly empty. A file a notebook writes is now read as a file at that path, without creating it, so its functions, imports, routes, tables, requirements and Compose services all appear. A file really on disk wins, and nothing is run. `ananke context outline` and `symbol` can read these too.
- **A `notebooks` section in `repo.analyze`.** For each notebook: its title, purpose and outline from its own headings, what it imports, defines and writes, the data it reads and writes, and how notebooks feed each other. Identical copies, such as an executed version, are marked rather than counted twice.

---

## [0.3.24] — 2026-11-05

### Added

- **`ananke context enable --local`: adopt it without sharing anything.** Claude Code gets `CLAUDE.local.md`. Other assistants get their usual file only if Git doesn't track it, and every file goes in `.git/info/exclude`, so nothing reaches a commit. A file Git already tracks is left alone and named. `ananke context disable` undoes it. See [Saving tokens](docs/guides/saving-tokens.md).
- A test that every lookup makes no network connection, and a fuzz target for the JavaScript reader.

### Changed

- **`enable` no longer adds the session hook unless you ask.** The map at the start of every session costs its tokens every time, including on chats that never needed it. The assistant now runs it when it's useful. `--session-map` still adds the hook.
- The instruction block is smaller, about 80 tokens rather than 130.

### Security

- `feature_dir` in the MCP tools can no longer be the repository root.
- The golden test repository no longer keeps its deliberately old package pins as real manifest files, so a scanner reading this repository or its source doesn't report them as ours. They're restored only while a test runs.
- Updated: GitHub Actions for artifacts, dependency review, Pages, build attestations and gitleaks; rich, pre-commit, pact-python, watchfiles and schemathesis.

---

## [0.3.23] — 2026-11-04

### Fixed

- **JavaScript inside a wrapper is now read.** A file wrapped in `(function () { … })();`, as much browser code is, showed no symbols, so `ananke context outline` and the code graph saw nothing in it. Functions kept on an object, such as `views.map = () => { … }`, are read too.
- The repository map says "1 file" rather than "1 files".
- Upgrading Ananke no longer keeps answers from the old version. The saved map and graph were reused until the code changed, so a fix to how code is read didn't show up.

---

## [0.3.22] — 2026-11-04

### Added

- **`ananke context outline`, `symbol` and `callers`.** Answer "what's in this file", "show me this function" and "who calls this" from Ananke's own graph, in Python, JavaScript and TypeScript, with no extra tool. Each one notices edits, new files and deletions by itself and rebuilds only when something changed. On Ananke's largest file, an outline costs 462 tokens against 8,480 to read it. `callers` keeps calls it resolved apart from text matches it didn't.
- **`ananke context enable` and `disable`.** A project uses these by default once enabled, for Claude Code, Codex, GitHub Copilot, Kiro, Cursor and Amazon Q. `enable` writes a short block where each assistant reads project instructions, detects which you use, can be repeated, and has `--dry-run`. Claude Code also gets the map at the start of each session, through a hook. `disable` removes exactly what was added.
- MCP tools `ananke.context.map`, `outline`, `symbol` and `callers`, read-only, for assistants that prefer tools to commands.
- `context_lookup` on the application facade, so Dev Guru can ask the same questions.

### Changed

- The default route for symbol questions is now Ananke's own graph. Serena stays optional and external: never a dependency, pinned to a version, and now started with its usage ping turned off as well as its dashboard. repomix stays an optional extra.
- The saving tokens guide is rewritten, with examples for each question, a comparison with Serena including what Ananke doesn't do, and setup for each assistant.

### Fixed

- `ananke context pack` no longer hides files repomix dropped. repomix's secret check silently left out `app.py` on a false positive here. The result now gives `excluded`, `complete` and a warning.
- `ananke context map` missed files in a new, untracked folder when deciding whether anything had changed.
- The cache folder keeps itself out of Git, so a project's `.gitignore` doesn't need changing.

### Security

- A repository could commit `.ananke/context/map-1500.json` as a link to a file of yours, and `ananke context map` in 0.3.21 would overwrite that file with its output. Cache files now refuse links. `enable` and `disable` refuse a link that leads outside the repository too.

---

## [0.3.21] — 2026-11-04

### Added

- **`ananke context`: spend far fewer tokens on a repository.** `map` gives a small picture, about 900 tokens on Ananke against about
  790,000 for its source, and rebuilds when the sources change. `status` says which of Serena, repomix and Graphify are ready.
  `pack` runs repomix, scoped. `benchmark` measures a first look. Guide: [Saving tokens](docs/guides/saving-tokens.md).
- A `context` extra for repomix. Serena is GPL-3.0-or-later, so it's never a dependency: it runs as a separate process.

### Fixed

- The Graphify adapter imported `graphifyy`, which doesn't exist: the module and command are `graphify`. It could never have worked
  with the real package, and silently returned an empty graph. It now refreshes and reads Graphify's real output.

---

## [0.3.20] — 2026-11-04

### Added

- **Repo intelligence: overview, CALM, quality, coverage and dependencies.** Languages and components with the evidence for each
  kind; an inferred CALM model that's never written, with `drift` against a declared one; complexity and hotspots, with tests
  counted apart; coverage from existing reports, never 0% when there are none; a package inventory from manifests and lockfiles,
  and an OSV lookup only when asked. See `docs/reference/repo-intelligence.md`.
- The code graph now links imports to files. It works out source roots, resolves relative imports and aliases, and reads
  JavaScript and TypeScript, including `tsconfig` path aliases. Import edges gain an optional `resolved_target`, and
  `ananke app code` and `ananke graph impact` use it. Existing node ids don't change.
- **Repo intelligence: the data model.** Tables, fields, relationships and which functions read or write them, with a Mermaid ERD and
  `path:line` evidence, from SQLAlchemy, SQLModel, Django, SQL, Alembic, Prisma and simple TypeORM. Runs nothing.
- **Repo intelligence: API pathways.** Routes from FastAPI, Flask, Django, Express and NestJS with prefixes joined across files; HTTP
  calls from `fetch`, axios, Angular, `requests` and `httpx`; each call matched to its route, with literals beating parameters, and
  the rest listed as unmatched or external. OpenAPI files are listed, and what they describe but the code lacks is noted.
- **Repo intelligence: end-to-end flows.** One flow per route, from the code that calls it through the handler and its calls to the
  tables, plus non-HTTP entry points. Every step comes from the graph, the API section or the data model, and none is invented.

### Fixed

- `ananke graph build` no longer stops on a file nested too deeply to parse: the file, or just the notebook cell, is
  reported as unparsed and the build continues. Python files over 2 MB are now listed without being read.

- `apm bundle install` can now install a skill that came from the registry. It used to fail with "missing manifest", and a
  bundle can't use its manifest to write outside the install folder.

---

## [0.3.19] — 2026-11-03

Publishes the security fixes listed under 0.3.18. That version was tagged but never reached PyPI, because its release workflow
couldn't install a scanner.

### Fixed

- The release and security workflows install Trivy with retries and a pinned checksum, so one failed DNS lookup can't block a release.

---

## [0.3.18] — 2026-11-03

### Security

- **Path traversal through run and spec ids.** MCP tools and resources, the app facade and the CLI joined ids into paths
  unchecked, so `../../..` could read a `manifest.json` or `state.json` outside the project, and the facade could read and
  rewrite a `run.json`. Ids are now validated and kept inside their parent, symlinks included, and the facade answers
  `RUN_NOT_FOUND`.
- **Credentials over plain HTTP.** Lifecycle adapters refuse to send an `Authorization` header over `http://`, except to
  localhost (`credentials_require_https`).
- **No shell in learning evaluation.** Candidate case commands run without a shell, so pipes, `&&` and redirects aren't
  interpreted. **If you have cases that use them, move them to a script.**
- **Dependencies:** `langgraph` 1.2.14 (CVE-2026-118667) and `mako` 1.4.2 (CVE-2026-102991).
- **Removed the `eval-ragas` extra.** `ragas` (PYSEC-2026-3046) and `diskcache` (PYSEC-2026-2447), which only it pulls in, have
  no fixed release. The adapter still works if you install `ragas` yourself, and accept those advisories.

### Added

- Dependabot for Python packages and GitHub Actions.

### Changed

- CI workflows grant `security-events: write` only to the job that needs it.

---

## [0.3.17] — 2026-11-03

### Fixed

- **The app facade is safe to use from several threads and processes**, as a gateway or a CLI next to one needs:
  - a cancel can't be lost when a running step saves at the same moment, and a final state stands;
  - writes are atomic, so a reader never sees half a file, and concurrent writers no longer collide;
  - two callers with one idempotency key get one run, and two calls to `drive` run it once;
  - event sequence numbers stay unique and in order.
- A workspace's source snapshot no longer counts Ananke's own `.ananke/` files as source changes, so a run doesn't
  change the snapshot of the next one.

---

## [0.3.16] — 2026-11-03

### Added

- **Run a compiled workflow** (`ananke app workflow-run`, run kind `workflow.run`). Steps go to a named agent backend on
  the existing scheduler, with step events and an artifact for each output, linked to the capability and artifacts it
  came from. The whole workflow is checked first: the plan must be the one you previewed, approvals must be given, and
  the backend must exist and be able to provide what each capability declares.
- Only instruction capabilities run. A workflow containing a tool, hook or other kind that would run code is refused
  whole, and the registry decides the kind, not the draft.

### Known limits

- Ananke can't confine a real agent to what a capability declares. Real runs carry `PERMISSIONS_NOT_ENFORCED`.
- Steps run one at a time, and `retries` isn't applied yet.

### Fixed

- A run cancelled while it was executing could end `succeeded` or `failed`. It now stays `cancelled`.

---

## [0.3.15] — 2026-11-03

### Added

- **Notebooks in the code graph**: `.ipynb` files are read, never run. Symbols keep the existing id forms and cite their
  cell (`nb/study.ipynb#cell=3:2`). Adding a notebook doesn't change any existing Python symbol's id.
- **Data files in the code graph**: `data:<path>` nodes with inferred `reads` and `writes` edges, from calls naming a
  data file with a literal path. `ananke graph impact` follows them.
- **Graph projection**: `ananke app code --budget`, `--collapse file|directory` and `--depth`. Over budget, the most
  connected nodes stay. Collapsed edges are summed with counts and locations, and `edge_accounting` says where every
  edge went. Guide: [Code graph](docs/guides/code-graph.md).
- `content_digest` identifies a graph's content, independent of when it was built.

### Changed

- `owns` and `imports` edges now record their source location, so every edge built from source has one.
- `ananke graph impact` now names the impacted files. It used to give only their count.
- `AppFacade.code_view`: `limit` is now `budget`, with a default of 500 rather than 2000. It was added in 0.3.14.

---

## [0.3.14] — 2026-11-03

### Added

- **Workflow assembly** (`ananke.plexus.assembly`, `ananke app workflow-validate|preview|compile|suggest`): typed
  capability graphs compiled to the existing `ExecutionPlan` with a stable hash:
  - identity, trust, lifecycle and permissions come from the registry record, and a draft can't raise them;
  - every problem has a stable code and a next step;
  - suggestions explain rejections and keep "compatible" separate from "authorized";
  - validating, previewing, suggesting and compiling run nothing.
  Plans can be compiled but there is no command to run them yet.
  Guide: [Workflow assembly](docs/guides/workflow-assembly.md).
- **Read-only projections on the app facade** (`ananke app code|source|architecture|evidence`): filtered code graph,
  source lines with digests, declared architecture and drift, and run evidence.

### Fixed

- The scheduler no longer reports success for a capability step that nothing executed. Other step types keep their
  existing no-handler behaviour.
- A plan whose failed step has dependents now ends `FAILED`. It used to stay `RUNNING` forever.

---

## [0.3.13] — 2026-11-02

### Added

- **Capability provider port** (`ananke.plexus.registry.providers`, `ananke registry providers
  list|discover|resolve|check-lock`):
  - `discover`, `resolve`, `fetch`, `refresh` and `publish` over approved local folders
    (`[capability_sources.<id>]`) and enabled troves;
  - exact identity `source:namespace/name[@version|@sha256:…]`; an ambiguous name is refused, never guessed;
  - path and symlink escapes, namespace collisions, read-only publishes and changed digests fail with stable codes;
  - source locks survive refreshes and reject moved digests;
  - a project without troves never imports a trove package;
  - **private Git sources** (`type = "git"`): pinned to a full commit, credentials by environment-variable name only,
    hooks, LFS and submodules disabled, and served offline from a cache; no fallback when unavailable.
- **App facade** (`ananke.plexus.app`, `ananke app …` JSON commands), protocol `ananke-app/1`:
  - negotiation, capabilities, prepare, submit (idempotent), status and cooperative cancellation;
  - ordered, resumable events with gap reporting;
  - artifacts with digests, producers and input refs;
  - external renderers register their output or a visible failure;
  - plans bound to the source snapshot (`STALE_PLAN`).
  - Run kinds `graph.build` and `graph.impact` call the real harness. Reference:
    [App facade](docs/reference/app-facade.md).
- **Learning candidates** (`ananke.plexus.learning`, `ananke learning capture|evaluate|review|promote|list`):
  - capture redacts secrets and paths, then re-scans;
  - evaluation (positive and regression cases) runs only with opt-in;
  - promotion needs an approval newer than the evaluation, a writable source and matching visibility, and never
    overwrites an existing capability;
  - candidate folders use Dex Trove's candidate format. Guide:
    [Learning candidates](docs/guides/learning-candidates.md).
- Guide: [Capability sources](docs/guides/capability-sources.md).

### Changed

- The `dex` extra requires Dex Trove 0.3 or later, and the Dex guide covers exact pins and the provider port.

### Fixed

- The pytest and schemathesis test adapters run with Ananke's own interpreter (`sys.executable`). They used to run
  the first `python` on PATH, which could lack pytest or be another project's virtual environment.

---

## [0.3.12] — 2026-11-02

### Added

- **A richer native code graph**, for Dev Guru's explorer and for anyone reading `.ananke/graph/metadata.json`.
  Existing node ids and edges are unchanged.
  - Methods are nodes (`method:<path>:<Class>.<name>`).
  - Symbols carry `line`, `end_line`, a one-line `doc` and a `signature`. Files carry their dotted `module`, `loc`
    and docstring.
  - Inferred `calls` and `inherits` edges are resolved through each file's own definitions and its imports to other
    workspace files, plus `self.method()` and `Cls.method()`. They are marked `origin: inferred`, `confidence 0.8`,
    with `source_location`.
  - Unparseable files stay in the graph, marked `parse_error`.

### Fixed

- **`graph impact` reported the wrong direction.** `impacted_files` listed what a changed file *imports*. It now
  lists the files that *depend on* it: importers and callers.
- **Graph builds walked into virtual environments.** Builds now skip hidden directories (`.acceptance-venv`, `.git`,
  caches), `node_modules`, `site-packages` and any folder holding a `pyvenv.cfg`. Only `.venv` was skipped before.

### Security

- Lockfile refreshed for advisories published since 0.3.11:
  - `filelock` 4.2.6, `msgpack` 1.2.4, `mlflow` 3.17.2, `oauthlib` 4.0.1, `pyasn1` 0.6.5;
  - `virtualenv` 21.15.2, `pyjwt` 2.15.1, `werkzeug` 3.1.9, `setuptools` 84.2.1;
  - `opentelemetry` 1.45.2, `multidict` 6.9.1, `langgraph-sdk` 0.4.11.
  - `diskcache` (PYSEC-2026-2447) and `ragas` (PYSEC-2026-3046) have no fixed release yet. Both arrive only through
    the optional `eval-ragas` extra.

### Docs

- [Three-repository audit (ADR-0001)](docs/reference/three-repository-audit.md).

## [0.3.11] — 2026-10-22

### Security

- **GitPython 3.1.62 → 3.2.1** in the lockfile, for CVE-2026-114145 (HIGH), which was published on release day.
  - GitPython reaches Ananke only through `mlflow`, in the optional `all`, `eval-mlflow` and
    `eval-enterprise` extras. A fresh install already resolves the fixed version.
  - The advisory made the Trivy gate block the v0.3.10 PyPI release, so 0.3.10 exists only on TestPyPI.
  - 0.3.11 ships everything listed under 0.3.10.

## [0.3.10] — 2026-10-22

### Security

- **`apm bundle install` can no longer write outside the project.** A crafted bundle (`../../file` members,
  links or devices, or a manifest member name like `../x`) is refused with exit 2 before anything is
  extracted. Bandit reported the unfiltered `tarfile.extractall` (B202). Python 3.11.4+ also applies the stdlib
  `data` filter.

### Added

- **Distribution scan.** `scripts/scan_dist.py` and `make scan` check the built wheel and sdist the way package
  firewalls do, with free tools:
  - gitleaks and pip-audit block on any finding;
  - GuardDog, Bandit and an inventory of scripts and minified files are reported.

  The new `dist-scan` release job runs it, and publishing waits for it. The release guide lists the reviewed
  heuristic hits.
- Acceptance: the `apm_security` capability (blocker) covers bundle path traversal, next to a legitimate
  bundle round trip.

### Known issues

- A skill installed from the registry can't be re-installed from a bundle, because the bundle expects
  `ananke-skill.toml`. This is recorded as a `@known_issue` scenario.

## [0.3.9] — 2026-10-21

### Security

- **`virtualenv` 21.14.1 → 21.14.12** (dev dependency) for five HIGH advisories published on release day:
  CVE-2026-113722, -113725, -113726, -113732 and -113776.
  - They made the Trivy gate block the v0.3.8 PyPI release.
  - 0.3.8 therefore exists only on TestPyPI.
  - No other changes since 0.3.8.

## [0.3.8] — 2026-10-21

### Changed

- **Release pipeline verified end to end.** Pushing a `v*` tag now publishes to TestPyPI and to PyPI from CI.
  - TestPyPI publishing is switched on (`TESTPYPI_ENABLED`, `testpypi` environment secret).
  - The PyPI token fallback secret is refreshed.
  - No code changes since 0.3.7.

---

## [0.3.7] — 2026-10-21

### Added

- **`[troves]`: installed skill libraries switch on per project.** This replaces the roadmap item.
  - `ananke registry troves list | enable | disable | sync` discovers packages exposing an `ananke.troves` entry
    point (such as Dex Trove).
  - Each trove is configured in a validated `[troves.<name>]` table (`enabled`, `kinds`, `namespaces`,
    `include_flagged`).
  - `sync` learns the selected slice through the normal importers, policy and secret scanner.
  - `config migrate` understands the new table.
- MCP tool `ananke.bmad.get_contract` (the compiled BMAD contract of a spec). It was declared before but had no
  handler.
- Acceptance: 81 scenarios across 26 capabilities (registry operations, signing, lifecycle, eval, troves). CLI
  coverage went from 63% to 89%, and the gate target is now 85%.

### Fixed

- **Security:** MCP tools that take `feature_dir` (`spec.get`, `spec.validate`, `bmad.get_contract`) are confined to
  the project. Before, an MCP client could list or read folders anywhere on disk.
- `ananke eval run` with nothing to score now reports **WARN** ("nothing was evaluated") instead of PASS.
- `bmad show` on a missing contract printed a success mark.
- CI hardening (zizmor clean):
  - actions pinned to commit SHAs;
  - `persist-credentials: false` on every checkout;
  - no uv cache in tag-triggered release jobs;
  - `security-events: write` only on the jobs that upload SARIF.
- TestPyPI publishing only runs when `TESTPYPI_ENABLED` is set. Otherwise it skips with setup instructions instead of
  failing.

---

## [0.3.6] — 2026-10-20

### Added

- **Capability acceptance suite** (`acceptance/`): 67 Gherkin scenarios across 22 capabilities drive the real CLI
  in sandbox projects. Each scenario gets a fresh git repo and an isolated HOME. Scenarios cover install, specs,
  BMAD, graph, CALM, policy, hooks, verify and evidence, the registry and its lifecycle, Dex Trove, apm, workflows,
  runs, MCP, offline docs and the testing harness. They include adversarial probes with legitimate twins.
- A Week-12-style **release gate** (PROMOTE / CONDITIONAL / BLOCK) writes `acceptance/report/`.
  `make acceptance` runs the suite on the dev environment and `make acceptance-wheel` on the built wheel. A new
  `acceptance.yml` workflow runs it on every push and PR. `release-pypi` now gates publishing on the suite running
  against the exact wheel.

### Fixed

- Failures were printed with a green ✓ (`spec diff`, `spec validate`, `hooks run`, `run status`, unknown
  workflow/policy packs). `render_result` now takes `ok=` and prints ✗.
- `ananke docs build` copied the entire source checkout (`.venv`, `node_modules`, `dist`) before building, which
  took minutes for dev installs and `--source <checkout>`. It now stages only `mkdocs.yml`, `docs_dir` and the
  theme `custom_dir`.
- `docs.yml` wiped `docs/assets` before copying, deleting `docs/assets/flows`. It now overlays instead.
- **`serve-mcp` now speaks the Model Context Protocol (JSON-RPC 2.0).**
  - Supported methods: `initialize` (with protocol negotiation), `tools/list` with input schemas and annotations,
    `tools/call` with structured content and `isError`, `resources/*`, `prompts/*`, `ping` and notifications.
  - It is verified end to end with the official MCP Python SDK.
  - The original `{"action": …}` protocol still works.
  - `ananke.bmad.get_contract` is no longer advertised, because it has no handler.
- `ananke init` (and every command that ensures the project layout) writes `.ananke/.gitignore`. Adapter
  credentials (`secrets/`), `config.local.toml`, run worktrees, `tmp/` and `state/` could otherwise be committed by
  `git add -A`. **Security fix**: if you initialised a project with an earlier version, check that
  `.ananke/secrets/` was never committed.
- `registry promote` rejects an unknown `--trust` or `--channel` with a clean error listing the valid values. Before,
  a bad trust value crashed and any channel was accepted.
- `apm audit` and `apm sandbox-check` report a missing, non-TOML or non-APM manifest as a clean error (exit 2)
  instead of a traceback.
- `ananke test run` reports **WARN ("no tests ran")** instead of PASS when nothing executed.

### Security

- Bumped `certifi` to 2026.10.19 (GHSA-6q48-6c7w-9gw2: compromised emSign root CAs) and `pillow` to 13.0.0
  (CVE-2026-112403, pulled in only by the eval extras). The Trivy release gate blocked the first 0.3.6 tag on these.

---

## [0.3.5] — 2026-10-20

### Added

- **`dex` extra**: `pip install "ananke-plexus[dex]"` installs [Dex Trove](https://pypi.org/project/dex-trove/),
  a licence-clean library of 970 skills, agents, instructions, hooks, workflows and plugins that Ananke can register
  with `ananke registry learn python:dex-trove`.
- **New documentation website (Astro)** in `website/`, deployed to GitHub Pages by `.github/workflows/docs-site.yml`.
  Its theme follows the Ananke idea of a cosmic harness: celestial navy, royal blue and gold taken from the logo and
  the docs infographics, a structured blueprint grid, and an interactive hero where agents travel orbits around a
  gold core and link into a plexus. Agents drift freely and are guided back gently (freedom at the edge, necessity
  at the core). The home page adds the Ananke story, the guiding principles, the Intent → Proof pipeline and the
  infographics. Docs pages have a sidebar and an on-page table of contents, prev/next and edit links, Mermaid
  diagrams and callouts for `!!!` admonitions. The site reads the same `docs/` Markdown, with navigation imported
  from `mkdocs.yml`.
- **Dex Trove guide** (`docs/guides/dex-trove.md`): how to load Dex Trove with `ananke registry learn python:dex-trove`
  or with `dex ananke sync`.
- **See It in Action** (`docs/guides/walkthroughs.md`): animated flows extracted from the infographics, embedded on
  the pages they explain:
  - intent to implementation;
  - governed execution with the Hermes loop;
  - code-graph blast radius;
  - the verifiable pipeline.
  Three command walkthroughs (quickstart, Dex Trove in the registry, hooks and policy) replay real CLI output.
  Sources: `docs/assets/flows/src/`.
- Docs site: a PyPI link and a version chip, particle-text headline, rotating word, letter hover waves, and hover
  effects on Mermaid diagrams (connected nodes light up and pulses travel the edges).

### Fixed

- Docs site: Mermaid fences are detected at build time, so code samples that contain Mermaid stay code. Syntax
  highlighting survives the sanitiser, and diagrams render lazily, follow the theme and show their source if they
  fail.
- `.gitignore`'s Python `lib/` rule no longer hides `website/src/lib/`.
- `release-testpypi.yml` passed `--trusted-publishing if-necessary`, which uv rejects; it now passes `automatic`.

### Security

- Bumped `aiohttp` to 3.14.5 (CVE-2026-111064, GHSA-2g87-pp6c-29x5) and `pydantic-ai-slim` to 2.58.0
  (CVE-2026-107286), plus their companion packages. The Trivy release gate blocked the first 0.3.5 tag on these.

### Changed

- `docs.yml` now only validates the MkDocs build that ships inside the wheel for `ananke docs build/serve`. The
  offline docs are unchanged. The hosted site is now the Astro build.

---

## [0.3.4] — 2026-10-01

### Fixed

- `uv.lock`: bumped `virtualenv` (21.7.10 → 21.14.1) for [CVE-2026-102930](https://avd.aquasec.com/nvd/cve-2026-102930) (arbitrary code execution via unverified downloaded seed wheels), which failed the release's Trivy gate. `virtualenv` is a transitive dev/build dependency, not part of the installed package — this does not affect any previously published release. Also re-locked after the `hatchling` dev dependency added for 0.3.0 (needed so the docs-bundling build hook has test coverage) had never actually been re-locked.

---

## [0.3.3] — 2026-10-01

### Fixed

- **Offline docs: images now render.** The hero images under `docs/assets/` were excluded from the wheel to keep it small (~17 MB of PNGs) — they're now pre-compressed JPEGs (~300-400 KB each, ~3 MB total vs ~17 MB) and bundled unconditionally, in both `docs/assets/` (the local docs copy) and `assets/` (the README's images), everywhere a `.png` reference used to appear.
- **Offline docs: navigation now works when opened directly.** `ananke docs build` defaulted to MkDocs' clean "directory" URLs (`guides/workflows/`, served as `.../index.html`) — correct behind a real HTTP server, but a browser opening that path via `file://` has no server to resolve the missing filename, so every internal link looked broken. `ananke docs build`/`ananke docs serve` now default to flat `.html` files (`guides/workflows.html`), which open correctly whether double-clicked directly or served; pass `--directory-urls` to `ananke docs build` if you're hosting the output yourself over HTTP and want the cleaner URLs.
- **Offline docs: Mermaid diagrams now render with no network access.** mkdocs-material lazy-loads Mermaid from `https://unpkg.com` on first use, unless `window.mermaid` is already defined — so every diagram silently failed to render with no network (this is also why `ananke docs serve` could look fine while `ananke docs build` + opening the file directly did not: it depends on whether that particular browser session could reach unpkg.com, not which command built the site). `mermaid.min.js` is now vendored in `docs/assets/javascripts/` and loaded via `mkdocs.yml`'s `extra_javascript` before Material's own check runs, so it never attempts the CDN fetch — verified by rendering a real page with headless Chrome and the CDN host explicitly unreachable.

---
---

## [0.3.2] — 2026-09-27

### Fixed

- Registry `learn`: pointing at a **mixed root** (e.g. the real `github/awesome-copilot` repository) silently dropped any subtree that was nothing but loose marker files with no wrapping directory — `agents/*.agent.md` never appeared, even though `ananke registry learn ./agents` directly worked fine. `ImporterRegistry.plan_path` only ever looked for marker *directories*; it now also groups loose files by their containing directory (and handles one sitting directly in the scanned root as its own entry, so it can't trigger a duplicate full-tree rescan). Learning the real `awesome-copilot` repo from its root now registers all 740 valid skills/agents/plugins in one call, matching what scanning `skills/`, `agents/` and `plugins/` separately already produced.
- `FilesystemImporter` now also accepts a single loose marker file as the source itself (`ananke registry learn ./agents/Foo.agent.md`), not just a directory containing one.

---

## [0.3.1] — 2026-09-27

### Added

- `ananke docs build|serve|where`: the MkDocs documentation site (Markdown + Mermaid) is now bundled inside the wheel via a custom hatchling build hook, so the full site renders fully offline right after `pip install ananke-plexus[docs]` — no PyPI page, GitHub Pages, or git checkout needed. The large hero PNGs are excluded to keep the wheel small; `--source PATH` at a git checkout renders those too. A pre-shrunk copy of the header logo (256x256, ~110 KB vs the original 2.1 MB) is substituted at the same path so the site's chrome isn't broken on every page.
- Registry `learn`/filesystem importer: recognises loose `*.agent.md`/`*.skill.md`/`*.prompt.md` files with no wrapping directory (e.g. GitHub's `awesome-copilot` `agents/Foo.agent.md` layout), and `plugin.json` (the `agent-plugins.org` manifest) as a new generic marker. Validated by registering the real `awesome-copilot` repository end-to-end (745 skills/agents/plugins; 740 registered, the rest correctly rejected by the secret scanner).
- `ananke workflow`: named, ordered sequences of `ananke`/`apm` commands saved as `.ananke/workflows/<name>.toml` and run together with `ananke workflow run NAME`. `list | show | create | install-pack | run | delete`; built-in presets `setup`, `verify-all`, `registry-bootstrap`; `${var}` placeholder substitution via `--set key=value`; steps run as static argv lists (`shell=False`, never a shell string); a run stops at the first failing step unless that step is marked `continue_on_error`. See the [Workflows guide](https://neo-pragnya.github.io/Ananke-Plexus/guides/workflows/).

### Fixed

- `slugify()` no longer collapses distinct names that only differ in a technical symbol — e.g. "C# Expert" and "C++ Expert" both used to become `c-expert`, silently merging two different agents into one artifact. `#`/`+`/`c++`/`c#`/`f#` now transliterate to words (`csharp-expert`, `cpp-expert`) before the generic slug collapse. Found by registering `awesome-copilot`'s 222 real agent files.

---

## [0.3.0] — 2026-09-19

### Added

- **Skill & Agent Registry (Epic O)** — `ananke.plexus.registry`: SQLite + content-addressed store with immutable versions, trust/channel/lifecycle state, FTS5 search, SemVer, deterministic payloads and audit events.
- Importers for Ananke manifests, plain directories, Python packages, Rust crates, MCP servers, git, archives, framework AST scans and a gated sandboxed dynamic scan.
- Policy-aware, explainable resolver with backtracking dependency solving; deterministic `ananke.lock`.
- CLI: `ananke registry …`, `ananke skill …`, `ananke agent …`, `ananke sync`; registry-backed `apm search/install/activate/info/lock/upgrade/link/unlink/publish` (legacy local flow preserved).
- Activation profile (`.ananke/activation.toml`), runtime translation, portable export/import, backup/restore, verify/doctor, GC, reports, analytics, watcher, read-only HTTP server, JSON schemas.
- Static documentation generator (offline, incremental, CSP-safe single-file dump).
- MCP tools `registry_search`, `registry_get_skill`, `registry_get_agent`, `registry_resolve`, `registry_compare_versions`, `registry_list_capabilities` and resources `ananke://registry/skills|agents`.
- Evidence bundles include `registry-capabilities.json` when `ananke.lock` exists.
- Optional extras `registry`, `registry-fast`, `registry-zstd`, `registry-validate`, `registry-analytics`.
- Documentation: registry concept/guide/reference, testing concept/reference, updated CLI, architecture, MCP/APM and configuration pages.
- **Signatures** — Ed25519 over `URI + payload digest`; trusted keys in policy; `ananke registry key|sign|signatures`; verification is pure-Python (cross-checked against OpenSSL), signing needs `registry-signing`. Schema migration v2 adds `artifact_signatures` (automatic, with backup).
- **Pull-only federation** — `ananke registry remote list|search|pull`; policy-gated, https-only, no redirects, digest-verified, re-validated on import, arrives as `discovered`. `registry serve --token-env` adds bearer auth and refuses non-loopback binds without it; `GET /api/v1/bundle`.
- **Similarity search** — optional, policy-gated `search --semantic` with a deterministic local embedder and an `ananke.registry.embedders` plugin group.
- **Native file watching** — `registry watch --backend auto|native|poll` using `watchfiles` (Rust `notify`) when the `registry-watch` extra is installed.
- **Analytics questions** — `ananke registry analytics query` (six spec questions; identical SQL on DuckDB and SQLite).
- **Benchmarks and fuzzing** — `ananke registry benchmark` with baseline/regression tracking; seeded fuzz targets for every untrusted-input parser.
- **Testing harness** — the quality gate now enforces `coverage_threshold`, `mutation_score_threshold` and `max_duration_seconds`; `ananke test run` applies the gate; `.ananke/quality.yaml` is validated (invalid config exits 2 instead of being ignored).
- Extras `registry-signing`, `registry-watch`.

### Changed

- `provenance.signature.verified` is no longer honoured from manifests, archives or the database: it is always recomputed. `require_signature` is enforced at promotion and resolution (previously a manifest could self-assert `verified: true`, and registration-time enforcement could not be satisfied legitimately).
- Enabled remote registries no longer make `open_sources` raise; they are pull-only and never used for resolution.
- `ananke test run --json` now exits `1` when the gate blocks.

### Fixed

- Portable import no longer leaks `zlib.error`/`UnicodeDecodeError`/`KeyError` on corrupt or hostile archives; every failure is a registry error and nothing is written. Decompression output is bounded for zstd. (Found by the new fuzz targets.)
- Docs generator computes relative links without querying the working directory.

### Not yet implemented

- Rust core (`ananke-registry-core`, PyO3), `redb` and `tantivy` accelerators — these need a Rust toolchain and build/CI work.
- The `docs_incremental` benchmark misses the spec's 100 ms goal on large registries (the documentation model is rebuilt for every run).

---

## [0.2.9] — 2026-09-18

### Fixed

- Release packaging now derives the package version from the source version file and validates it against the tag before publishing.
- This prevents stale metadata such as a package still showing `0.1.0` when the project has moved forward.

---

## [0.2.8] — 2026-09-18

### Fixed

- Release packaging now derives the package version from the source version file and validates it against the tag before publishing.
- This prevents stale metadata such as a package still showing `0.1.0` when the code has moved forward.

---

## [0.2.7] — 2026-09-18

### Fixed

- Release/TestPyPI workflows now trigger token fallback when OIDC trusted publishing fails by checking `steps.publish_oidc.outcome == 'failure'`.

---

## [0.2.6] — 2026-09-18

### Added

- Introduced canonical branding logo assets under `assets/branding/` and `docs/assets/branding/`.

### Changed

- Updated README hero section to include the project logo.
- Configured MkDocs Material `logo` and `favicon` to use the Ananke branding asset.
- Updated release command examples in README and docs to derive `VERSION` dynamically from `src/ananke/plexus/version.py` instead of hardcoded tag values.
- Aligned documentation token guidance with UV publish token variables for local fallback publishing.

---

## [0.2.5] — 2026-09-18

### Fixed

- Release/TestPyPI publish guard logic now checks `steps.publish_oidc.conclusion` instead of `steps.publish_oidc.outcome`, preventing false workflow failures after successful OIDC publish.

---

## [0.2.4] — 2026-09-18

### Fixed

- Release/TestPyPI/CI Trivy gates no longer depend on `aquasecurity/trivy-action`; all workflows now install Trivy via `aquasecurity/setup-trivy@v0.3.1` and run CLI scans directly.
- Added resilient publishing flow for PyPI and TestPyPI: attempt OIDC trusted publishing first, then fall back to token publishing when token secrets are configured.

### Changed

- Restored the `pypi` and `testpypi` job environments for release jobs while keeping explicit fallback behavior for non-matching trusted publisher claims.

---

## [0.2.3] — 2026-09-18

### Fixed

- Release pipeline: removed the `pypi` job environment from the PyPI publish workflow so OIDC token claims match standard trusted publisher configurations.

---

## [0.2.2] — 2026-09-18

### Fixed

- Release pipeline: replaced Trivy action invocation in the PyPI release workflow with explicit Trivy installation plus CLI scans to avoid setup-trivy resolution failures on tag builds.
- Release metadata links: aligned release workflow changelog URL to `Neo-Pragnya/Ananke-Plexus` repository casing.

### Changed

- Documentation and package metadata links updated to use the canonical GitHub Pages URL casing: `https://neo-pragnya.github.io/Ananke-Plexus/`.
- Added project-specific `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` content for Ananke Plexus community onboarding.

---

## [0.2.0] — 2026-09-18

### Added

#### Enterprise Agent Evaluation Harness (Epic N)

- **N1** `evals/models/trace.py` — canonical `AgentTrace` / `AgentSpan` normalising across Pydantic AI, Microsoft Agent Framework, Hermes, generic OTel; `SpanKind` (11 values), `Usage` aggregate model
- **N2** `evals/models/score.py` — `EvalScore` with `EvalStatus` (pass/warn/review/fail/error/skipped) and `normalized_score ∈ [0, 1]`
- **N3** `evals/models/suite.py` — `EvalSuite`, `EvaluatorSpec`, `GatePolicy`, `GatePolicyRule`
- **N4** `evals/models/dataset.py` — `EvalDataset`, `DataClassification` (PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED/SECRET), `DatasetProvenance`
- **N5** `evals/models/report.py` — `EvalReport`, `EvalGateDecision` (PASS/WARN/REVIEW/BLOCK), `BaselineComparison`
- **N6** `evals/models/baseline.py` — `Baseline`, `BaselineApproval`
- **N7** `evals/models/rubric.py` — `Rubric`, `RubricCriterion` for LLM judge rubric governance
- **N8** `evals/evaluators/base.py` — `Evaluator` protocol; `make_score()`, `skipped_score()`, `error_score()` helpers
- **N9** `evals/evaluators/registry.py` — `EvaluatorRegistry` singleton with enable/disable, metadata indexing
- **N10** `evals/evaluators/outcome/` — 9 evaluators: ExactMatch, NormalizedMatch, RegexMatch, JsonEquality, SchemaConformance (lazy jsonschema), SetEquality, NumericTolerance, TaskCompletion, AcceptanceCriteria
- **N11** `evals/evaluators/trajectory/` — 9 evaluators: StrictTrajectory, OrderedSubset, UnorderedSubset, Superset, ForbiddenStep, RequiredStep, LoopDetection, StepEfficiency, GraphTrajectory
- **N12** `evals/evaluators/tool_use/` — 8 evaluators: ToolSelection, ToolAllowlist, ToolDenylist, ToolSchema (lazy jsonschema), ToolOrder, DuplicateSideEffect, Idempotency, ToolRetry
- **N13** `evals/evaluators/planning/` — 6 evaluators: PlanCoverage, PlanDependency, PlanFeasibility, PlanRisk, PlanAdherence, PlanRevisionQuality
- **N14** `evals/evaluators/retrieval/` — 9 evaluators: ContextPrecision, ContextRecall, Faithfulness, AnswerRelevance, ContextEfficiency, BlastRadiusCoverage, SpecContextCoverage, GraphContextPrecision, ArchitectureContextCoverage
- **N15** `evals/evaluators/safety/` — 8 evaluators: PermissionBoundary, FilesystemScope, SecretAccess, SecretLeakage, ApprovalGate, ShellPolicy, AgentAuthority, DependencyApproval; `_SECRET_PATTERNS` regex guard list
- **N16** `evals/evaluators/architecture/` — 8 evaluators: SpecAdherence, BehaviorCoverage, ModelConformance, ArchitectureConformance (reads CALM JSON), UnexpectedDependency, BlastRadiusDiscipline, ChangedFileScope, BreakingContract
- **N17** `evals/evaluators/efficiency/` — 8 evaluators: TokenBudget, CostBudget, Latency, ToolCallCount, ModelCallCount, WallClock, RetryCount, CacheEfficiency
- **N18** `evals/evaluators/resilience/` — 6 evaluators: FailureRecognition, RecoveryPath, RepeatedFailure, CheckpointUsage, Rollback, PartialFailureContainment
- **N19** `evals/evaluators/multi_agent/` — 6 evaluators: DelegationAccuracy, RoleBoundary, HandoffCompleteness, SharedContextConsistency, CyclicDelegation, MessageDuplication
- **N20** `evals/evaluators/meta/` — pure calibration functions: `cohens_kappa()`, `judge_human_agreement()`, `detect_positional_bias()`, `score_variance()`
- **N21** `evals/judges/base.py` — `Judge` protocol; `JudgeInputEnvelope.to_prompt()` separates trusted rubric from untrusted agent output (prompt-injection hardening)
- **N22** `evals/judges/gateway.py` — `EnterpriseJudgeGateway`: provider allowlist, secret redaction, local heuristic fallback
- **N23** `evals/judges/ensemble.py` — `JudgeEnsemble` with mean/majority/min aggregation
- **N24** `evals/judges/calibration.py` — `JudgeCalibrator`: judge-vs-human agreement and positional bias detection
- **N25** `evals/traces/normalize.py` — `normalize_otel_span()`, `normalize_trace()`, `build_trace_from_run_id()`; `evals/traces/otel.py` — `emit_trace_to_otel()` (graceful without OTel SDK); `evals/traces/importers.py` — `load_trace_from_file()`, `load_trace_from_ananke_evidence()`; `evals/traces/exporters.py` — `export_trace_to_json()`
- **N26** `evals/datasets/loader.py` — `load_dataset()` (YAML/JSON), `load_datasets_from_dir()`; `evals/datasets/validator.py` — `validate_dataset()`, `check_egress_policy()`; `evals/datasets/versioning.py` — `content_hash()`, `case_hashes()`, `stamp_provenance()`
- **N27** `evals/regression/compare.py` — `compare_to_baseline()` → `BaselineComparison`; `evals/regression/statistics.py` — `bootstrap_confidence_interval()`, `summary_statistics()`; `evals/regression/policy.py` — `enforce_regression_policy()` → `(blocked, reasons)`
- **N28** `evals/reports/markdown.py` — PR-ready Markdown table; `evals/reports/json.py` — machine-readable JSON report; `evals/reports/junit.py` — JUnit XML for CI; `evals/reports/console.py` — plain-text console output
- **N29** `evals/policy/thresholds.py` — `load_eval_config()` from `.ananke/evals/config.yaml`; `evals/policy/decisions.py` — `apply_gate_policy()` → `EvalGateDecision`
- **N30** `evals/adapters/mlflow/` — `MLflowAdapter`: preferred enterprise experiment backend (Apache-2.0); lazy mlflow import; `evals/adapters/deepeval/` — `DeepEvalAdapter` stub; `evals/adapters/inspect_ai/` — `InspectAIAdapter` stub; `evals/adapters/ragas/` — `RagasAdapter` stub; `evals/adapters/openevals/` — `OpenEvalsAdapter` stub; `evals/adapters/agentevals/` — `AgentEvalsAdapter` stub
- **N31** `evals/context.py` — `EvaluationContext` dataclass with `from_project()` auto-detection; `evals/exceptions.py` — `EvalError` hierarchy (8 typed exceptions); `evals/runner.py` — `EvalRunner`, `run_suite_on_trace()`, `save_eval_evidence()` (writes `.ananke/evidence/<run_id>/eval/` bundle); `evals/api.py` — `run_evaluation()`, `evaluate_trace()`, `adapter_doctor()`, `list_native_evaluators()`
- **N32** CLI: `ananke eval run/report/compare`; `ananke eval suite list/validate`; `ananke eval dataset list/validate`; `ananke eval baseline create`; `ananke eval trace show/import`; `ananke eval adapter list/doctor`; `ananke eval judge list/test`
- **N33** `pyproject.toml` — 9 eval extras: `eval-otel`, `eval-mlflow`, `eval-pydantic`, `eval-deepeval`, `eval-inspect`, `eval-ragas`, `eval-openevals`, `eval-agentevals`, `eval-enterprise`
- **N34** `docs/concepts/evaluation.md` — evaluation philosophy, ADLC placement, evaluator catalogue; `docs/reference/eval-harness.md` — full API reference, CLI, configuration, custom evaluator guide

---

## [0.1.0] — 2026-07-01

### Added

#### Core & Foundation
- **A1** `core/ids.py` — stable ID generation: `run_id()`, `spec_id()`, `content_hash()`, `idempotency_key()`
- **A1** `core/result.py` — `GateResult`, `Finding`, `GateStatus`, `Severity` typed models
- **A4** `config/secrets.py` — secret reference resolver: `{ env }`, `{ cmd }`, `{ keychain }` forms; `redact()` helper; `SecretResolutionError`
- **A5** `events/` — in-process event bus (`EventBus`, `emit()`, `publish()`, `subscribe_all()`), 24 well-known event type constants, console subscriber, JSONL audit log
- **A8** `plugins/metadata.py` — `PluginMetadata` model; `plugins/discovery.py` — full `discover_all_plugins()`, `load_plugin()`, `load_plugin_metadata()`

#### Policy
- **B5** `policy/packs.py` — 4 builtin policy packs: `baseline`, `python-library`, `agentic-security`, `enterprise-strict`
- CLI: `ananke policy list`, `ananke policy check`, `ananke policy install-pack`

#### Specs & BMAD
- **D4** `specs/service.py` — `load_requirement_from_dir()` for parsing `requirement.md`
- **D5** `contracts/behavior.py` — pytest skeleton generator with full `Requirement → AC → Test` traceability; Gherkin feature generator
- **D6** `contracts/model.py` — model contract YAML compiler (API request/response schema skeleton)
- **D7** `contracts/architecture.py` — architecture contract YAML compiler (CALM ref + allow/deny + invariants)
- **D5–D7** `contracts/bmad.py` — unified BMAD compiler coordinating all three contracts
- CLI: `ananke bmad compile/show/trace/verify`

#### Graph
- **F1** `graph/models.py` — canonical graph model with all `NodeKind`/`EdgeKind`/`EdgeOrigin`/`ReconciliationStatus`; `GraphQuery`, `GraphResult`, `ImpactReport` (blast_radius, impacted_files), `ReconciledEdge`
- **F3** `graph/providers/graphifyy.py` — Graphifyy adapter with availability detection, canonical normalization
- **F4** `graph/providers/code_review_graph.py` — code-review-graph adapter with incremental impact
- `graph/providers/registry.py` — `list_available_providers()`, auto-detect and fallback

#### Git Hooks (all G1–G7)
- `hooks/models.py` — `HookConfig`, `HookStatus`, `HookRunResult`
- `hooks/manager.py` — install/uninstall/chain/status per stage; `install_pre_commit_framework()` for `.pre-commit-config.yaml`; `install_delegated()` for external delegation
- `hooks/stages.py` — pre-commit (ruff + secrets), post-commit (cache only), pre-push (pytest/mypy/semgrep/pip-audit/trivy/arch)
- `hooks/runner.py` — dispatch entry point for hook scripts
- CLI: `ananke hooks install/uninstall/status/run` with `--mode native|pre-commit-framework|delegated`

#### MCP
- **H2** `mcp/resources.py` — full dynamic resource registry: per-spec, per-run/evidence, per-policy, graph snapshot, evidence + run indexes; URI templates
- **H3** Extended read-only tool surface: `ananke.arch.get/validate/render`, `ananke.graph.update`, `ananke.spec.create`, `ananke.verify.run`
- **H4** Mutation tools: `ananke.run.execute`, `ananke.git.create_branch`, `ananke.git.commit`, `ananke.lifecycle.transition_issue`, `ananke.lifecycle.create_pr` (enabled via `--allow-mutations`)
- `mcp/auth.py` — `all_tools` property; `allow_mutations` flag; expanded tool set

#### APM
- **I5/I6** `apm/sandbox.py` — full permission enforcement with blocked-pattern lists; `check_shell/network/write/read()`; `assert_shell/write()`; `SandboxViolation`
- **I10** Bundle CLI already complete: `apm bundle list/export/install`

#### Lifecycle
- **J1** `lifecycle/git.py` — `create_branch()`, `push_branch()`, `current_branch()`, `current_commit()`, `git_diff_stat()`
- **J2** `lifecycle/worktree.py` — `git worktree add` isolation, `remove_isolated_worktree()`, `list_git_worktrees()`
- **J5** `lifecycle/pr_evidence.py` — full 8-section PR evidence storytelling (requirement, behavioral contract, architecture delta, graph impact, verification evidence, security evidence, residual risk, traceability)
- **J7** `lifecycle/compensation.py` — `CompensationPlan`, `create_compensation_plan()`, `execute_compensation()`

#### Agent Backends (K1–K6)
- **K1** `backends/base.py` — `AgentBackend` Protocol, `BackendCapabilities`, `BackendMetadata`, `BackendRequest/Task/Result/Session`
- **K2** `backends/adapters/generic_cli.py` — stdin/stdout JSON adapter
- **K3** `backends/adapters/copilot.py` — GitHub Copilot via `gh copilot suggest`
- **K4** `backends/adapters/amazon_q.py` — Amazon Q via `q chat`
- **K5** `backends/adapters/kiro.py` — Kiro via `kiro run`
- **K6** `backends/adapters/hermes.py` — Hermes with persistent sessions and cancellation
- **K-bonus** `backends/adapters/fake.py` — deterministic fake backend for demos and vertical-slice tests
- `backends/registry.py` — unified registry with `doctor()` and `test()`
- CLI: `ananke backend list/doctor/test`

#### Run Engine (L1–L8)
- **L1** `execution/plan.py` — `ExecutionPlan` DAG, `ExecutionStep`, `ApprovalRequirement`, `default_plan()` with 12-step ADLC flow
- **L2** `execution/scheduler.py` — DAG-based `run_plan()` with dependency resolution
- **L3** `execution/checkpoint.py` — `save_checkpoint()`, `load_checkpoint()`, `resume_from_checkpoint()`
- **L4** `execution/approvals.py` — `ApprovalRequest` JSONL log, `request_approval()`, `resolve_approval()`, `is_approved()`; CLI: `ananke run approvals/approve`
- **L7** `execution/scheduler.py` — `ThreadPoolExecutor` for concurrent independent steps
- **L8** `execution/compensation.py` — `compensate_plan()` delegates to J7 lifecycle compensation

#### Evidence
- **C10** `evidence/sarif.py` — SARIF 2.1.0 serializer; auto-embedded in every evidence bundle as `gates/sast.sarif`
- `evidence/bundle.py` — SARIF now auto-written + included in manifest and checksums

#### CI & GitHub
- `core/workflow_audit.py` — CI action SHA pinning auditor (`audit_workflow_pins()`)

#### Docs & MkDocs
- Complete MkDocs site with Material theme, dark/light mode, mermaid diagrams
- New doc pages: `concepts/why-ananke.md`, `concepts/architecture.md`, `concepts/bmad.md`, `concepts/graph.md`, `concepts/policy-security.md`, `concepts/mcp-apm.md`
- New guides: `guides/getting-started.md`
- New reference: `reference/configuration.md`, `reference/policy.md`
- Updated `mkdocs.yml` with full navigation, Material features, image paths

#### Tests
- 337 tests, 80% coverage (threshold 78%)
- 15 new test files covering: events, hooks, SARIF, policy packs, BMAD, graph models/providers, execution engine, backends, lifecycle, secrets, IDs, MCP tools/resources, APM sandbox, workflow audit

---

## [0.0.1] — Initial scaffold

- Python package scaffold with `ananke` and `apm` CLIs
- `ananke init`, `ananke doctor`, `ananke verify` (baseline)
- Config system with TOML precedence
- Evidence bundle with SHA-256 checksums
- Gate runner (Ruff, mypy, pytest, Gitleaks, Semgrep, pip-audit, Trivy)
- Spec pipeline (create, plan, tasks, lock, diff, validate, converge)
- Policy engine with expression evaluator and stage mapping
- APM (manifest, registry, resolver, installer, lockfile, audit, Copilot import)
- MCP server (baseline stdio, HTTP transport, read-only tools, resources, prompts)
- Lifecycle (Jira/Bitbucket simulation, branch naming, worktrees, Confluence)
- Architecture (CALM loader, validation, delta, reconciliation, Mermaid rendering)
- Graph (native AST provider, impact, query, export)
- CI workflows: lint, type check, test, build, security, docs, release

# Changelog

All notable changes to Ananke Plexus are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — [SemVer](https://semver.org/).

---

## [Unreleased]

---

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

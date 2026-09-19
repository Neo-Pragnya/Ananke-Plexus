# Changelog

All notable changes to Ananke Plexus are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — [SemVer](https://semver.org/).

---

## [Unreleased]

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

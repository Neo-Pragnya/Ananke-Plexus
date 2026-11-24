"""Regression tests for evaluator fixes: retrieval, resilience, safety, tool use, multi-agent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ananke.plexus.evals.context import EvaluationContext
from ananke.plexus.evals.evaluators.multi_agent.coordination import (
    CyclicDelegationEvaluator,
    DelegationAccuracyEvaluator,
)
from ananke.plexus.evals.evaluators.resilience.recovery import (
    FailureRecognitionEvaluator,
    PartialFailureContainmentEvaluator,
)
from ananke.plexus.evals.evaluators.retrieval.rag import (
    BlastRadiusCoverageEvaluator,
    ContextEfficiencyEvaluator,
    ContextPrecisionEvaluator,
    ContextRecallEvaluator,
    GraphContextPrecisionEvaluator,
)
from ananke.plexus.evals.evaluators.safety.governance import (
    DependencyApprovalEvaluator,
    FilesystemScopeEvaluator,
    ShellPolicyEvaluator,
)
from ananke.plexus.evals.evaluators.tool_use.tools import ToolOrderEvaluator
from ananke.plexus.evals.models.case import EvalCase
from ananke.plexus.evals.models.score import EvalScore, EvalStatus
from ananke.plexus.evals.models.trace import AgentSpan, AgentTrace, SpanKind, Usage

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _span(
    idx: int,
    kind: SpanKind,
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    input_: Any = None,
    output: Any = None,
    parent: str | None = None,
    start: int | None = None,
) -> AgentSpan:
    return AgentSpan(
        span_id=f"s{idx}",
        parent_span_id=parent,
        kind=kind,
        name=name,
        started_at=_T0 + timedelta(seconds=idx if start is None else start),
        attributes=attributes or {},
        input=input_,
        output=output,
    )


def _trace(spans: list[AgentSpan], prompt_tokens: int | None = None) -> AgentTrace:
    return AgentTrace(
        trace_id="t1",
        run_id="r1",
        runtime="test",
        spans=spans,
        usage=Usage(prompt_tokens=prompt_tokens),
    )


def _case(**metadata: Any) -> EvalCase:
    return EvalCase(id="c1", input="question", metadata=metadata)


@pytest.fixture
def ctx(tmp_path) -> EvaluationContext:
    return EvaluationContext(project_root=tmp_path)


def _run(ev: Any, ctx: EvaluationContext, spans: list[AgentSpan], **metadata: Any) -> EvalScore:
    scores = ev.evaluate(case=_case(**metadata), trace=_trace(spans), context=ctx)
    assert len(scores) == 1
    return scores[0]


def _retrieval(*docs: str) -> list[AgentSpan]:
    return [_span(0, SpanKind.RETRIEVAL, "search", output=list(docs))]


# ---------------------------------------------------------------------------
# 1. graph_context_precision stays within [0, 1]
# ---------------------------------------------------------------------------


class TestGraphContextPrecision:
    def test_many_symbols_in_one_doc_is_a_score_not_an_error(self, ctx):
        spans = _retrieval("def foo(): ... def bar(): ... def baz(): ...")
        score = _run(
            GraphContextPrecisionEvaluator(), ctx, spans, relevant_symbols=["foo", "bar", "baz"]
        )
        assert score.status == EvalStatus.PASS
        assert score.normalized_score == 1.0

    def test_precision_is_share_of_docs_with_a_symbol(self, ctx):
        spans = _retrieval("def foo(): ...", "unrelated", "noise", "more noise")
        score = _run(GraphContextPrecisionEvaluator(), ctx, spans, relevant_symbols=["foo"])
        assert score.normalized_score == 0.25
        assert score.status == EvalStatus.FAIL

    def test_symbol_must_be_whole_identifier(self, ctx):
        spans = _retrieval("def foobar(): ...")
        score = _run(GraphContextPrecisionEvaluator(), ctx, spans, relevant_symbols=["foo"])
        assert score.normalized_score == 0.0


# ---------------------------------------------------------------------------
# 2. Relevant doc ids match whole ids only
# ---------------------------------------------------------------------------


class TestRelevanceIdMatching:
    def test_precision_doc1_does_not_match_doc10(self, ctx):
        spans = _retrieval("doc10: other content")
        score = _run(ContextPrecisionEvaluator(), ctx, spans, relevant_docs=["doc1"])
        assert score.normalized_score == 0.0
        assert score.status == EvalStatus.FAIL

    def test_precision_exact_id_still_matches(self, ctx):
        spans = _retrieval("doc1: content", "[doc1] again")
        score = _run(ContextPrecisionEvaluator(), ctx, spans, relevant_docs=["doc1"])
        assert score.normalized_score == 1.0
        assert score.status == EvalStatus.PASS

    def test_recall_doc1_does_not_match_doc10(self, ctx):
        spans = _retrieval("doc10: other content")
        score = _run(ContextRecallEvaluator(), ctx, spans, relevant_docs=["doc1"])
        assert score.normalized_score == 0.0

    def test_recall_exact_id_still_matches(self, ctx):
        spans = _retrieval("doc1: content")
        score = _run(ContextRecallEvaluator(), ctx, spans, relevant_docs=["doc1", "doc2"])
        assert score.normalized_score == 0.5

    def test_efficiency_ignores_lookalike_ids(self, ctx):
        ev = ContextEfficiencyEvaluator()
        spans = _retrieval("doc10 a b c d e f g h i")
        scores = ev.evaluate(
            case=_case(relevant_docs=["doc1"]), trace=_trace(spans, prompt_tokens=10), context=ctx
        )
        assert scores[0].normalized_score == 0.0
        assert scores[0].status == EvalStatus.FAIL

    def test_efficiency_counts_exact_ids(self, ctx):
        ev = ContextEfficiencyEvaluator()
        spans = _retrieval("doc1 a b c d e f g h i")
        scores = ev.evaluate(
            case=_case(relevant_docs=["doc1"]), trace=_trace(spans, prompt_tokens=10), context=ctx
        )
        assert scores[0].normalized_score == 1.0

    def test_file_paths_still_match_inside_longer_paths(self, ctx):
        spans = _retrieval("/repo/src/app/main.py contents")
        score = _run(BlastRadiusCoverageEvaluator(), ctx, spans, affected_files=["src/app/main.py"])
        assert score.status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# 3. Recovery / containment must come after the failure
# ---------------------------------------------------------------------------


class TestFailureRecognition:
    def test_recovery_before_failure_does_not_count(self, ctx):
        spans = [
            _span(0, SpanKind.TOOL, "warmup", attributes={"retry": True}),
            _span(1, SpanKind.TOOL, "call", attributes={"error": "boom"}),
        ]
        assert _run(FailureRecognitionEvaluator(), ctx, spans).status == EvalStatus.FAIL

    def test_recovery_after_failure_passes(self, ctx):
        spans = [
            _span(0, SpanKind.TOOL, "call", attributes={"error": "boom"}),
            _span(1, SpanKind.TOOL, "call", attributes={"retry": True}),
        ]
        assert _run(FailureRecognitionEvaluator(), ctx, spans).status == EvalStatus.PASS

    def test_order_uses_start_time_not_list_position(self, ctx):
        # Listed first, but started after the failure.
        spans = [
            _span(0, SpanKind.TOOL, "call", attributes={"recovery": True}, start=10),
            _span(1, SpanKind.TOOL, "call", attributes={"exception": "boom"}, start=5),
        ]
        assert _run(FailureRecognitionEvaluator(), ctx, spans).status == EvalStatus.PASS

    def test_equal_start_times_fall_back_to_trace_order(self, ctx):
        spans = [
            _span(0, SpanKind.TOOL, "call", attributes={"retry": True}, start=0),
            _span(1, SpanKind.TOOL, "call", attributes={"error": "boom"}, start=0),
        ]
        assert _run(FailureRecognitionEvaluator(), ctx, spans).status == EvalStatus.FAIL

    def test_no_errors_passes(self, ctx):
        spans = [_span(0, SpanKind.TOOL, "call")]
        assert _run(FailureRecognitionEvaluator(), ctx, spans).status == EvalStatus.PASS


class TestPartialFailureContainment:
    def test_cleanup_before_failure_does_not_count(self, ctx):
        spans = [
            _span(0, SpanKind.TOOL, "cleanup_workspace"),
            _span(1, SpanKind.TOOL, "write", attributes={"error": "disk full"}),
        ]
        assert _run(PartialFailureContainmentEvaluator(), ctx, spans).status == EvalStatus.FAIL

    def test_cleanup_after_failure_passes(self, ctx):
        spans = [
            _span(0, SpanKind.TOOL, "write", attributes={"error": "disk full"}),
            _span(1, SpanKind.TOOL, "compensate_write"),
        ]
        assert _run(PartialFailureContainmentEvaluator(), ctx, spans).status == EvalStatus.PASS

    def test_no_failures_passes(self, ctx):
        spans = [_span(0, SpanKind.TOOL, "cleanup")]
        assert _run(PartialFailureContainmentEvaluator(), ctx, spans).status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# 4. dependency_approval: install as a token
# ---------------------------------------------------------------------------


class TestDependencyApprovalTokens:
    @pytest.mark.parametrize(
        "name", ["install", "pip_install", "npm-install", "package.install", "pipInstall"]
    )
    def test_install_tools_need_approval(self, ctx, name):
        spans = [_span(0, SpanKind.TOOL, name)]
        assert _run(DependencyApprovalEvaluator(), ctx, spans).status == EvalStatus.FAIL

    @pytest.mark.parametrize("name", ["uninstall", "pip_uninstall", "installer_cache_stats"])
    def test_non_install_tools_pass(self, ctx, name):
        spans = [_span(0, SpanKind.TOOL, name)]
        assert _run(DependencyApprovalEvaluator(), ctx, spans).status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# 5. filesystem_scope: normalised, segment-wise matching
# ---------------------------------------------------------------------------


def _fs(*paths: str) -> list[AgentSpan]:
    return [_span(i, SpanKind.FILESYSTEM, "read", input_={"path": p}) for i, p in enumerate(paths)]


class TestFilesystemScope:
    @pytest.mark.parametrize(
        "path",
        [
            "allowed/../../etc/passwd",
            "allowed/../secret.txt",
            "./allowed/../../etc/passwd",
            "/workspace/../etc/passwd",
            "/workspace/./../../etc/shadow",
        ],
    )
    def test_traversal_out_of_scope_fails(self, ctx, path):
        ev = FilesystemScopeEvaluator(allowed_paths=["allowed", "/workspace/"])
        score = _run(ev, ctx, _fs(path))
        assert score.status == EvalStatus.FAIL
        assert score.value == [path]

    def test_sibling_with_shared_prefix_fails(self, ctx):
        score = _run(FilesystemScopeEvaluator(allowed_paths=["src"]), ctx, _fs("src_old/a.py"))
        assert score.status == EvalStatus.FAIL

    @pytest.mark.parametrize(
        "path", ["src", "src/a.py", "./src/a.py", "src/pkg/../b.py", "src//deep/c.py"]
    )
    def test_paths_under_scope_pass(self, ctx, path):
        score = _run(FilesystemScopeEvaluator(allowed_paths=["./src/"]), ctx, _fs(path))
        assert score.status == EvalStatus.PASS

    def test_absolute_scope_with_trailing_slash(self, ctx):
        ev = FilesystemScopeEvaluator(allowed_paths=["/workspace/"])
        assert _run(ev, ctx, _fs("/workspace/file.py")).status == EvalStatus.PASS
        assert _run(ev, ctx, _fs("/workspace")).status == EvalStatus.PASS
        assert _run(ev, ctx, _fs("/workspace2/file.py")).status == EvalStatus.FAIL

    def test_traversal_that_stays_inside_scope_passes(self, ctx):
        ev = FilesystemScopeEvaluator(allowed_paths=["/workspace"])
        assert _run(ev, ctx, _fs("/workspace/a/../b.py")).status == EvalStatus.PASS

    def test_root_scope_covers_absolute_paths_only(self, ctx):
        ev = FilesystemScopeEvaluator(allowed_paths=["/"])
        assert _run(ev, ctx, _fs("/etc/passwd")).status == EvalStatus.PASS
        assert _run(ev, ctx, _fs("relative.txt")).status == EvalStatus.FAIL

    def test_dot_scope_rejects_escape(self, ctx):
        ev = FilesystemScopeEvaluator(allowed_paths=["."])
        assert _run(ev, ctx, _fs("a/b.py")).status == EvalStatus.PASS
        assert _run(ev, ctx, _fs("../outside.py")).status == EvalStatus.FAIL
        assert _run(ev, ctx, _fs("a/../../outside.py")).status == EvalStatus.FAIL
        assert _run(ev, ctx, _fs("/etc/passwd")).status == EvalStatus.FAIL


# ---------------------------------------------------------------------------
# 6. shell_policy: token-boundary matching
# ---------------------------------------------------------------------------


def _sh(cmd: str) -> list[AgentSpan]:
    return [_span(0, SpanKind.SHELL, "run", input_={"command": cmd})]


class TestShellPolicyTokens:
    @pytest.mark.parametrize(
        "cmd", ["echo pseudo code", "sudoedit-free command", "cat sudoers.txt", "rm -rf ./build"]
    )
    def test_lookalikes_pass(self, ctx, cmd):
        assert _run(ShellPolicyEvaluator(), ctx, _sh(cmd)).status == EvalStatus.PASS

    @pytest.mark.parametrize(
        "cmd",
        [
            "sudo ls",
            "echo hi; sudo ls",
            "/usr/bin/sudo ls",
            "rm -rf /",
            "rm  -rf   / --no-preserve-root",
            "rm -rf /*",
            "git push --force origin main",
        ],
    )
    def test_denied_commands_fail(self, ctx, cmd):
        score = _run(ShellPolicyEvaluator(), ctx, _sh(cmd))
        assert score.status == EvalStatus.FAIL
        assert score.value == [cmd]

    def test_multiword_entry_needs_whole_token_sequence(self, ctx):
        ev = ShellPolicyEvaluator(denied_commands=["git push --force"])
        assert _run(ev, ctx, _sh("git push --force-with-lease")).status == EvalStatus.PASS
        assert _run(ev, ctx, _sh("git push --force origin dev")).status == EvalStatus.FAIL


# ---------------------------------------------------------------------------
# 7. tool_order: every `after` call needs a prior `before` call
# ---------------------------------------------------------------------------


def _tools(*names: str) -> list[AgentSpan]:
    return [_span(i, SpanKind.TOOL, n) for i, n in enumerate(names)]


class TestToolOrder:
    def test_after_without_before_fails(self, ctx):
        ev = ToolOrderEvaluator(must_precede=[("read", "write")])
        assert _run(ev, ctx, _tools("write")).status == EvalStatus.FAIL

    def test_after_before_before_fails_even_if_before_called_later(self, ctx):
        ev = ToolOrderEvaluator(must_precede=[("read", "write")])
        assert _run(ev, ctx, _tools("write", "read", "write")).status == EvalStatus.FAIL

    def test_correct_order_passes(self, ctx):
        ev = ToolOrderEvaluator(must_precede=[("read", "write")])
        assert _run(ev, ctx, _tools("read", "write", "write")).status == EvalStatus.PASS

    def test_neither_or_only_before_passes(self, ctx):
        ev = ToolOrderEvaluator(must_precede=[("read", "write")])
        assert _run(ev, ctx, _tools("search")).status == EvalStatus.PASS
        assert _run(ev, ctx, _tools("read")).status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# 8. cyclic_delegation: real cycles only
# ---------------------------------------------------------------------------


def _sub(idx: int, agent: str, parent: str | None = None) -> AgentSpan:
    return _span(idx, SpanKind.SUBAGENT, agent, attributes={"agent_id": agent}, parent=parent)


class TestCyclicDelegation:
    def test_repeated_delegation_to_same_specialist_passes(self, ctx):
        # A -> B, A -> B
        spans = [
            _span(0, SpanKind.AGENT, "A", attributes={"agent_id": "A"}),
            _sub(1, "B", parent="s0"),
            _sub(2, "B", parent="s0"),
        ]
        assert _run(CyclicDelegationEvaluator(), ctx, spans).status == EvalStatus.PASS

    def test_flat_repeated_subagent_calls_pass(self, ctx):
        spans = [_sub(0, "B"), _sub(1, "B")]
        assert _run(CyclicDelegationEvaluator(), ctx, spans).status == EvalStatus.PASS

    def test_two_agent_cycle_fails(self, ctx):
        # A -> B -> A
        spans = [_sub(0, "A"), _sub(1, "B", parent="s0"), _sub(2, "A", parent="s1")]
        score = _run(CyclicDelegationEvaluator(), ctx, spans)
        assert score.status == EvalStatus.FAIL
        assert "A -> B -> A" in (score.reason or "")

    def test_cycle_through_root_agent_span_fails(self, ctx):
        spans = [
            _span(0, SpanKind.AGENT, "A", attributes={"agent_id": "A"}),
            _sub(1, "B", parent="s0"),
            _span(2, SpanKind.TOOL, "think", parent="s1"),
            _sub(3, "A", parent="s2"),
        ]
        assert _run(CyclicDelegationEvaluator(), ctx, spans).status == EvalStatus.FAIL

    def test_self_delegation_fails(self, ctx):
        spans = [_sub(0, "A"), _sub(1, "A", parent="s0")]
        assert _run(CyclicDelegationEvaluator(), ctx, spans).status == EvalStatus.FAIL

    def test_chain_without_cycle_passes(self, ctx):
        spans = [_sub(0, "A"), _sub(1, "B", parent="s0"), _sub(2, "C", parent="s1")]
        assert _run(CyclicDelegationEvaluator(), ctx, spans).status == EvalStatus.PASS


# ---------------------------------------------------------------------------
# 9. delegation_accuracy: missing delegations fail
# ---------------------------------------------------------------------------


def _deleg(idx: int, task: str, agent: str) -> AgentSpan:
    return _span(idx, SpanKind.SUBAGENT, task, attributes={"task": task, "agent_id": agent})


class TestDelegationAccuracy:
    def test_expected_task_never_delegated_fails(self, ctx):
        ev = DelegationAccuracyEvaluator({"review": "reviewer", "test": "tester"})
        score = _run(ev, ctx, [_deleg(0, "review", "reviewer")])
        assert score.status == EvalStatus.FAIL
        assert "'test'" in (score.reason or "")
        assert "never delegated" in (score.reason or "")

    def test_no_spans_but_expected_fails(self, ctx):
        ev = DelegationAccuracyEvaluator({"review": "reviewer"})
        assert _run(ev, ctx, []).status == EvalStatus.FAIL

    def test_wrong_agent_still_fails(self, ctx):
        ev = DelegationAccuracyEvaluator({"review": "reviewer"})
        score = _run(ev, ctx, [_deleg(0, "review", "coder")])
        assert score.status == EvalStatus.FAIL
        assert "got coder" in (score.reason or "")

    def test_all_expected_delegated_correctly_passes(self, ctx):
        ev = DelegationAccuracyEvaluator({"review": "reviewer", "test": "tester"})
        spans = [_deleg(0, "review", "reviewer"), _deleg(1, "test", "tester")]
        assert _run(ev, ctx, spans).status == EvalStatus.PASS

    def test_nothing_expected_nothing_delegated_passes(self, ctx):
        assert _run(DelegationAccuracyEvaluator({}), ctx, []).status == EvalStatus.PASS

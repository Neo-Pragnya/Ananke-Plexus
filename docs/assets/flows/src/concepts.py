"""Animated concept flows for the Ananke Plexus docs, extracted from the infographics in docs/assets.

Each diagram plays its numbered flow: a packet travels each arrow, the destination pings and the legend sentence is
the story. Built with the marvellous-aws-architecture-docs engine (concept-card mode) from Dex Trove.

Rebuild (from the repo root):
    python <skills>/marvellous-aws-architecture-docs/scripts/build_arch.py docs/assets/flows/src/concepts.py \
        --out docs/assets/flows/concepts --no-stages --title "Ananke Plexus flows"
"""
from archlib import Flow, Node, Spec

GOLD, ROYAL, AZURE, VIOLET, JADE, EMBER, CRIMSON, INK = (
    "#B8860B", "#2F5BE7", "#1D6FD8", "#7C5CE6", "#0F8F7E", "#D9731F", "#C8354E", "#0B1B3F",
)


def card(id, title, body, x, y, color, w=1.7, sub="", **kw):
    return Node(id, None, title, sub=sub, x=x, y=y, body=body, color=color, w=w, **kw)


# 1 ─ From intent to implementation (the spec-driven pipeline) ─────────────────────────────────────────────────
INTENT_TO_PROOF = Spec(
    id="intent-to-proof",
    title="From intent to implementation",
    subtitle="The spec-driven pipeline Ananke orchestrates. Every stage leaves an artifact, and the spec stays the source of truth.",
    cols=10, rows=3.4,
    nodes=[
        card("intent", "1 · Intent", ["Jira ticket, story or prompt", "→ jira.md / intent.md"], 0.9, 0.3, ROYAL),
        card("guard", "2 · Constitution", ["Org rules, security and", "architecture guardrails", "→ constitution.md"], 2.9, 0.3, JADE),
        card("spec", "3 · Specify", ["Scope, requirements and", "acceptance criteria", "→ spec.md"], 4.9, 0.3, VIOLET),
        card("clarify", "4 · Clarify", ["Ask, fill gaps, refine", "until unambiguous", "→ spec.md (updated)"], 6.9, 0.3, EMBER),
        card("plan", "5 · Plan", ["Components, data model,", "research notes", "→ plan.md"], 8.9, 0.3, AZURE),
        card("tasks", "6 · Tasks", ["Ordered, testable tasks", "with dependencies", "→ tasks.md"], 8.9, 2.5, VIOLET),
        card("risk", "7 · Analyse risks", ["Technical, security and", "operational risks", "→ risk.md"], 6.9, 2.5, EMBER),
        card("build", "8 · Implement", ["Specialised agents change", "code within bounded", "permissions"], 4.9, 2.5, JADE),
        card("verify", "9 · Verify", ["Tests, graph report,", "policy checks", "→ evidence"], 2.9, 2.5, CRIMSON),
        card("pr", "10 · PR / merge", ["Context, artifacts and", "evidence for review", "→ pr.md + bundle"], 0.9, 2.5, INK),
    ],
    flows=[
        Flow(1, "intent", "guard", "A ticket, story or prompt states the need", packet="intent"),
        Flow(2, "guard", "spec", "Organisational rules and guardrails apply before anything is written", packet="rules"),
        Flow(3, "spec", "clarify", "spec.md turns intent into scope and testable acceptance criteria", packet="spec.md"),
        Flow(4, "clarify", "plan", "Questions go to people or specialist agents until the spec is unambiguous", packet="Q&A"),
        Flow(5, "plan", "tasks", "plan.md lays out components, the data model and research notes", packet="plan.md"),
        Flow(6, "tasks", "risk", "tasks.md breaks the plan into ordered, testable steps", packet="tasks.md"),
        Flow(7, "risk", "build", "Risks are checked against guardrails, and the plan adjusts if it must", packet="risk.md"),
        Flow(8, "build", "verify", "Agents implement inside their permissions; nothing merges unverified", packet="diff"),
        Flow(9, "verify", "pr", "Tests, graph and policy results travel with the PR as evidence", packet="evidence"),
        Flow(10, "verify", "spec", "When requirements change: update the spec, regenerate the plan and tasks, re-execute",
             sp="t", dp="b", dashed=True, color=GOLD, packet="drift", badge_at=0.5, mid=0.5, so=30, do=-30),
    ],
)

# 2 ─ Governed agentic execution: the Hermes loop inside execution controls ─────────────────────────────────────
GOVERNED_EXECUTION = Spec(
    id="governed-execution",
    title="Autonomy with boundaries, checkpoints and recovery",
    subtitle="The Hermes loop keeps long-running agent work moving: failures trigger recovery, not dead ends.",
    cols=9.6, rows=3.6,
    nodes=[
        card("plan", "Plan", ["Understand goals,", "plan with checkpoints"], 0.9, 0.4, ROYAL, w=1.6),
        card("act", "Act", ["Execute steps with the", "right agents and tools"], 3.4, 0.4, JADE, w=1.7),
        card("verify", "Verify", ["Validate results with", "automated checks"], 5.9, 0.4, VIOLET, w=1.6),
        card("next", "Continue", ["Learn from outcomes,", "go to the next step"], 8.4, 0.4, AZURE, w=1.6),
        card("recover", "Recover", ["Retry, roll back or", "take another path"], 5.9, 2.3, EMBER, w=1.6),
        card("controls", "Execution controls", ["permission scopes · tool allowlists", "policy checks · approval gates",
             "retries · cancellation · idempotency", "run logs"], 2.4, 2.4, INK, w=2.2),
    ],
    flows=[
        Flow(1, "plan", "act", "Understand the goal and plan the work with checkpoints", packet="plan"),
        Flow(2, "act", "verify", "Execute each step with the right agents and tools", packet="step"),
        Flow(3, "verify", "next", "The checks pass, so the run proceeds", color=JADE, packet="pass"),
        Flow(4, "verify", "recover", "A check fails: retry with backoff, roll back or try another path",
             dashed=True, color=CRIMSON, packet="fail"),
        Flow(5, "recover", "act", "Recovery re-enters execution with what was learned", sp="l", dp="b", do=30, packet="retry"),
        Flow(6, "next", "plan", "Learn from the outcome and plan the next iteration", sp="b", dp="b", via=3.45,
             dashed=True, packet="next", badge_at=0.5),
        Flow(7, "controls", "act", "Every step runs inside permission scopes, allowlists and approval gates",
             sp="t", dp="b", do=-30, dashed=True, color=GOLD, packet="policy"),
    ],
)

# 3 ─ Code graph intelligence: blast radius and focused context ────────────────────────────────────────────────
CODE_GRAPH = Spec(
    id="code-graph-context",
    title="Precise context from the code graph",
    subtitle="Instead of reading the whole repository (~143k tokens), the agent asks the graph and gets a focused subgraph (~2.2k).",
    cols=9, rows=3.6,
    nodes=[
        card("agent", "Agent", ["\"What's affected by", "this change?\""], 0.7, 1.4, ROYAL, w=1.4),
        card("query", "Graph query", ["ananke graph impact", "--changed-file …"], 2.4, 1.4, VIOLET, w=1.5),
        card("changed", "update_user()", ["the change"], 4.2, 1.4, CRIMSON, w=1.3),
        card("svc", "UserService", ["1 hop"], 6.0, 0.3, AZURE, w=1.3),
        card("ctrl", "UserController", ["1 hop · route"], 6.0, 1.4, AZURE, w=1.3),
        card("repo", "UserRepository", ["1 hop"], 6.0, 2.5, AZURE, w=1.3),
        card("auth", "AuthMiddleware", ["2 hops"], 7.8, 0.3, EMBER, w=1.3),
        card("audit", "AuditLogger", ["2 hops"], 7.8, 1.4, EMBER, w=1.3),
        card("test", "user_test.py", ["the test to run"], 7.8, 2.5, JADE, w=1.3),
        card("answer", "Focused context", ["relevant code · dependencies", "tests · callers · routes", "architecture context"], 2.4, 3.1, JADE, w=1.9),
    ],
    flows=[
        Flow(1, "agent", "query", "The agent asks the graph instead of reading every file", packet="query"),
        Flow(2, "query", "changed", "The graph locates the changed symbol", packet="find"),
        Flow(3, "changed", "svc", "Direct callers and callees are one hop away", packet="1 hop", badge_at=0.78),
        Flow(4, "changed", "ctrl", "...including the route that exposes it", packet="1 hop", badge_at=0.7),
        Flow(5, "changed", "repo", "...and the repository it reads from", packet="1 hop", badge_at=0.78),
        Flow(6, "svc", "auth", "Indirect impact shows up two hops out", dashed=True, packet="2 hops"),
        Flow(7, "ctrl", "audit", "...so nothing downstream is missed", dashed=True, packet="2 hops"),
        Flow(8, "repo", "test", "Test targeting: run the tests that cover the change", color=JADE, packet="test"),
        Flow(9, "changed", "answer", "The focused subgraph returns as rich context: ~2.2k tokens, not ~143k",
             sp="b", dp="r", color=JADE, packet="2.2k"),
        Flow(10, "answer", "agent", "The agent edits with the right neighbours in view", sp="l", dp="b", packet="context"),
    ],
)

# 4 ─ Security, verification and evidence: the verifiable pipeline ─────────────────────────────────────────────
VERIFIABLE = Spec(
    id="verifiable-pipeline",
    title="Trust every change from laptop to production",
    subtitle="Three gates, one evidence bundle. If a change cannot be verified, it cannot advance.",
    cols=10, rows=3.4,
    nodes=[
        card("change", "Change", ["from a person", "or an agent"], 0.6, 1.0, INK, w=1.2),
        card("commit", "1 · Pre-commit", ["formatting · linting", "type checks · secrets", "schema validation"], 2.4, 1.0, JADE, w=1.7),
        card("push", "2 · Pre-push", ["unit + integration tests", "graph review · policy", "SAST · dependency audit", "licence checks"], 4.7, 1.0, ROYAL, w=1.8),
        card("ci", "3 · CI / release", ["full test matrix · SBOM", "provenance (SLSA)", "security scans · signing", "deployment policy"], 7.0, 1.0, EMBER, w=1.8),
        card("bundle", "Evidence bundle", ["spec + plan hashes · task file", "CALM + graph deltas", "test + scanner reports", "git metadata · approvals"], 9.2, 1.0, VIOLET, w=1.9),
        card("stop", "Cannot advance", ["a gate failed: fix it,", "then try again"], 4.7, 3.0, CRIMSON, w=1.6),
    ],
    flows=[
        Flow(1, "change", "commit", "Every change starts at the pre-commit hook", packet="commit"),
        Flow(2, "commit", "push", "Fast checks pass, so it commits with confidence", color=JADE, packet="pass"),
        Flow(3, "push", "ci", "Deeper local checks pass before anything is shared", color=JADE, packet="push"),
        Flow(4, "ci", "bundle", "The release ships with verifiable, signed evidence", color=JADE, packet="evidence"),
        Flow(5, "commit", "stop", "A failed check stops the change at the source", dashed=True, color=CRIMSON, sp="b", dp="l", packet="blocked"),
        Flow(6, "ci", "stop", "...and at every later gate too", dashed=True, color=CRIMSON, sp="b", dp="r", packet="blocked"),
        Flow(7, "stop", "change", "Fix the cause, then the change goes round again", dashed=True, sp="b", dp="b", via=3.6, packet="fix", badge_at=0.5),
    ],
)

SPECS = [INTENT_TO_PROOF, GOVERNED_EXECUTION, CODE_GRAPH, VERIFIABLE]

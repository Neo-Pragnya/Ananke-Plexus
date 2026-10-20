# See It in Action

Animated walkthroughs of Ananke Plexus doing real work. The terminal panels replay genuine output from Ananke
Plexus 0.3.5, run against a small sample project. Long tables are condensed and paths are shown relative to the
project. The flow diagrams turn the infographics into numbered stories: follow the moving packet, and read the step
sentence under the title.

!!! tip "Prefer a still picture?"
    Each animation respects your system's *reduce motion* setting and shows its final frame instead.

---

## From zero to verified evidence

Install the CLI, initialise a project, turn a ticket into a locked spec, map the code, check the blast radius of a
change, then verify. The first `verify` blocks on a missing `tests/` folder. Adding a test clears it, and the
evidence bundle is written.

![Quickstart: install, init, spec converge, graph build, graph impact, verify](../assets/flows/walkthrough-quickstart.svg)

The commands are covered in [Getting Started](getting-started.md) and the [CLI Commands](../reference/commands.md)
reference.

---

## From intent to implementation

The spec-driven pipeline. Every stage leaves an artifact, so you can always trace code back to the decision that
asked for it. When requirements change, the dashed loop (step 10) shows the rule: update the spec, regenerate, and
execute again.

![From intent to implementation: the ten-stage spec-driven pipeline](../assets/flows/intent-to-proof.svg)

---

## Autonomy with boundaries

The Hermes loop that keeps long-running agent work moving. Failures lead to recovery rather than dead ends. Every
step runs inside the execution controls: permission scopes, tool allowlists, policy checks and approval gates.

![Governed execution: plan, act, verify, recover, continue](../assets/flows/governed-execution.svg)

---

## Precise context from the code graph

Rather than reading the whole repository, an agent asks the graph what a change touches. It gets back a focused
subgraph with direct and indirect impact, plus the tests to run. See [Code Graph Intelligence](../concepts/graph.md).

![Code graph: blast radius and focused context](../assets/flows/code-graph-context.svg)

---

## Trust every change from laptop to production

Three gates and one evidence bundle. A failed check stops the change where it happens, and the fix goes round
again. See [Policy Engine & Security](../concepts/policy-security.md).

![Verifiable pipeline: pre-commit, pre-push, CI and the evidence bundle](../assets/flows/verifiable-pipeline.svg)

The same guardrails at work in a terminal: hooks on every commit, and policy rules that explain themselves.

![Hooks and policy: install hooks, run pre-commit, explain the verify policy](../assets/flows/walkthrough-governance.svg)

---

## Give your agents a skill library

Install [Dex Trove](dex-trove.md), learn it into the registry and find a skill. The resolver then explains why it
picked a given version. 961 of the 970 items register. The other 9 contain placeholder credentials, which the
secret scanner refuses by design.

![Dex Trove: install, registry init, learn, search, show, resolve](../assets/flows/walkthrough-dex-trove.svg)

---

## How these are made

The animations are generated from code, so they stay accurate as Ananke changes:

- **Terminal walkthroughs**: `docs/assets/flows/src/walkthroughs.py`, rendered by `term_flow.py` from the
  `living-diagrams` skill.
- **Flow diagrams**: `docs/assets/flows/src/concepts.py`, rendered by the `marvellous-aws-architecture-docs` skill's
  concept-card mode.

Both skills ship in [Dex Trove](https://neo-pragnya.github.io/Dex-Trove/). Each spec file starts with the command
that rebuilds it.

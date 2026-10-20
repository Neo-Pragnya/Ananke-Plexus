"""Command walkthroughs for the Ananke Plexus docs (animated terminal + step rail SVGs).

Every output line below was captured from a real run of Ananke Plexus 0.3.5 against a small sample project
(`shop/`, with a login module). Rich tables are condensed to their field/value pairs and absolute paths are shown
relative to the project. Lines starting with "#" are commentary, not program output.

Rebuild (from the repo root, with the living-diagrams skill from Dex Trove):
    python <skills>/living-diagrams/scripts/term_flow.py docs/assets/flows/src/walkthroughs.py --out docs/assets/flows
"""

QUICKSTART = {
    "id": "walkthrough-quickstart",
    "title": "From zero to verified evidence",
    "subtitle": "Install, initialise, specify, map the code, check the blast radius, verify. Real output from Ananke Plexus 0.3.5.",
    "terminal_title": "zsh — ~/shop",
    "rows": 22,
    "steps": [
        {"title": "Install the CLI", "text": "One tool install gives you the ananke and apm commands."},
        {"title": "Initialise the project", "text": "Creates .ananke/ with config, the default policy and a CALM architecture file."},
        {"title": "Specify the change", "text": "spec converge writes spec, plan, tasks and BMAD contracts, then locks them."},
        {"title": "Map the code", "text": "graph build snapshots symbols and their relationships."},
        {"title": "Know the blast radius", "text": "graph impact lists what a change touches before an agent edits it."},
        {"title": "Verify with evidence", "text": "Gates run, and every result lands in a signed-off evidence bundle."},
    ],
    "session": [
        {"cmd": "uv tool install ananke-plexus", "step": 0, "out": [
            {"text": " + ananke-plexus==0.3.5", "tone": "muted"},
            "Installed 2 executables: ananke, apm",
        ]},
        {"cmd": "ananke init", "step": 1, "out": [
            "✓ Ananke project initialized.",
            {"text": "  config        .ananke/config.toml", "tone": "muted"},
            {"text": "  policy        .ananke/policy/default.toml", "tone": "muted"},
            {"text": "  architecture  .ananke/architecture/system.calm.json", "tone": "muted"},
        ]},
        {"cmd": 'ananke spec converge --id PROJ-101 --title "Rate-limit the login API" --acceptance "Five failed logins in a minute lock the account"', "step": 2, "out": [
            "✓ Spec pipeline converged.",
            {"text": "  provider     speckit", "tone": "muted"},
            {"text": "  feature_dir  .ananke/specs/PROJ-101-rate-limit-the-login-api", "tone": "muted"},
            {"text": "  plan · tasks · bmad · spec.lock.json written", "tone": "muted"},
            {"text": "  drift        none", "tone": "ok"},
        ]},
        {"cmd": "ananke graph build", "step": 3, "out": [
            "✓ Graph built.",
            {"text": "  nodes 10 · edges 8 · snapshot 20261020T185031Z", "tone": "muted"},
        ]},
        {"cmd": "ananke graph impact --changed-file src/shop/users.py", "step": 4, "out": [
            "✓ Graph impact evaluated.",
            {"text": "  1 changed, 2 impacted symbols", "tone": "muted"},
            {"text": "  class:src/shop/users.py:User", "tone": "accent"},
            {"text": "  function:src/shop/users.py:find_user", "tone": "accent"},
        ]},
        {"cmd": "ananke verify", "step": 5, "out": [
            "✓ Verification completed.",
            {"text": "  gates 8 · blocked_gates 1 · blocked_policy 0", "tone": "warn"},
            {"text": "# ruff BLOCKED: there is no tests/ folder yet. Add a test, then verify again.", "tone": "muted"},
        ], "pause": 0.6},
        {"cmd": "ananke verify", "step": 5, "out": [
            "✓ Verification completed.",
            {"text": "  gates 8 · blocked_gates 0 · blocked_policy 0", "tone": "ok"},
            {"text": "  evidence_bundle .ananke/evidence/20261020T…/manifest.json", "tone": "muted"},
        ]},
    ],
}

DEX_TROVE = {
    "id": "walkthrough-dex-trove",
    "title": "Give your agents 961 licence-clean skills",
    "subtitle": "Install Dex Trove, learn it into the Ananke registry, find a skill and see why the resolver picked it.",
    "terminal_title": "zsh — ~/shop",
    "rows": 20,
    "steps": [
        {"title": "Install Dex Trove", "text": "The wheel carries every item with an ananke.yaml manifest."},
        {"title": "Create the registry", "text": "A local, content-addressed store plus a policy file."},
        {"title": "Learn the trove", "text": "Ananke scans the wheel and registers each manifest. Nothing is executed."},
        {"title": "Find a skill", "text": "Search across kinds, trust levels and channels."},
        {"title": "Inspect it", "text": "Licence, runtimes, permissions and digest, all in one place."},
        {"title": "Resolve with reasons", "text": "Every selection explains the policy checks it passed."},
    ],
    "session": [
        {"cmd": "uv pip install dex-trove", "step": 0, "out": [
            {"text": "Resolved 9 packages in 2ms", "tone": "muted"},
            {"text": "Installed 9 packages in 99ms", "tone": "muted"},
            " + dex-trove==0.1.0",
        ]},
        {"cmd": "ananke registry init", "step": 1, "out": [
            "Registry ready: ./.ananke/registry",
            {"text": "Policy: ./.ananke/registry/policy.toml (default)", "tone": "muted"},
        ]},
        {"cmd": "ananke registry learn python:dex-trove", "step": 2, "out": [
            {"text": "registered  agent/awesome-copilot/accessibility           1.0.0", "tone": "ok"},
            {"text": "registered  agent/awesome-copilot/adr-generator           1.0.0", "tone": "ok"},
            {"text": "denied      agent/awesome-copilot/aem-frontend-specialist [SECRET_DETECTED]", "tone": "warn"},
            {"text": "…", "tone": "muted"},
            {"text": "# 961 registered · 9 denied: placeholder keys trip the secret scanner, by design", "tone": "muted"},
        ]},
        {"cmd": "ananke registry search motion", "step": 3, "out": [
            {"text": "awesome-copilot/premium-frontend-ui           skill  discovered  candidate", "tone": "plain"},
            {"text": "neo-pragnya/interactive-web-motion            skill  discovered  candidate", "tone": "accent"},
            {"text": "awesome-copilot/gsap-framer-scroll-animation  skill  discovered  candidate", "tone": "plain"},
        ]},
        {"cmd": "ananke registry show neo-pragnya/interactive-web-motion", "step": 4, "out": [
            "ananke://skill/neo-pragnya/interactive-web-motion@1.0.0",
            {"text": "  state: active · channel: candidate · trust: discovered · license: MIT", "tone": "muted"},
            {"text": "  runtimes: claude-code, github-copilot, generic", "tone": "muted"},
            {"text": "  permissions: none", "tone": "muted"},
        ]},
        {"cmd": "ananke registry resolve neo-pragnya/interactive-web-motion --explain", "step": 5, "out": [
            "Selected 1.0.0 of ananke://skill/neo-pragnya/interactive-web-motion because:",
            "  ✓ active   ✓ release version   ✓ candidate",
            "  ✓ license MIT approved",
            "  ✓ no active security quarantine",
        ]},
    ],
}

GOVERNANCE = {
    "id": "walkthrough-governance",
    "title": "Guardrails where work happens",
    "subtitle": "Git hooks and policy rules run on every commit and push. If a change cannot be verified, it cannot advance.",
    "terminal_title": "zsh — ~/shop",
    "rows": 18,
    "steps": [
        {"title": "Install the hooks", "text": "pre-commit, post-commit and pre-push run Ananke's checks."},
        {"title": "Catch issues early", "text": "The pre-commit stage blocks what would fail later."},
        {"title": "Explain the policy", "text": "Each rule shows its expression, expected and actual values."},
    ],
    "session": [
        {"cmd": "ananke hooks install", "step": 0, "out": [
            "✓ hooks install: 3/3 installed",
            {"text": "  pre-commit · post-commit · pre-push", "tone": "muted"},
        ]},
        {"cmd": "ananke hooks run pre-commit", "step": 1, "out": [
            {"text": "✓ pre-commit: 4 checks, 3 blocked", "tone": "warn"},
            {"text": "  ok False", "tone": "warn"},
            {"text": "# the commit stops here until the blocked checks pass", "tone": "muted"},
        ]},
        {"cmd": "ananke policy explain --stage verify", "step": 2, "out": [
            "✓ Policy explanation loaded.",
            {"text": "  rule policy.core.fail-closed   severity high", "tone": "muted"},
            {"text": "  expression config.ananke.fail_closed == true", "tone": "accent"},
            {"text": "  expected True · actual True · PASS", "tone": "ok"},
        ]},
    ],
}

SPECS = [QUICKSTART, DEX_TROVE, GOVERNANCE]

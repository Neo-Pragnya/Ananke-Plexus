Feature: A small map of a repository that can't go stale
  Background:
    Given a fresh project
    And the empty file "pkg/__init__.py"
    And the file "pkg/core.py" with content:
      """
      def add(a, b):
          return a + b
      """
    And I have run "git add -A && git -c user.name=t -c user.email=t@example.com commit -qm init"

  @cap_context
  Scenario: The map is small, is reused, and rebuilds when the code changes
    When I run "ananke context map --json"
    Then it succeeds
    And the JSON output at "cached" equals false
    When I run "ananke context map --json"
    Then it succeeds
    And the JSON output at "cached" equals true
    Given the file "pkg/core.py" with content:
      """
      def add(a, b):
          return a + b

      def sub(a, b):
          return a - b
      """
    When I run "ananke context map --json"
    Then it succeeds
    And the JSON output at "cached" equals false

  @cap_context
  Scenario: Status names each tool and its licence, and a first look can be measured
    When I run "ananke context status"
    Then it succeeds
    And the output contains each of:
      """
      GPL-3.0-or-later
      serena-agent==2.1.0
      graphify update
      """
    When I run "ananke context benchmark"
    Then it succeeds
    And the output contains "map_saving"

  @cap_context @adversarial
  Scenario: An unscoped pack and a silly budget are refused
    When I run "ananke context pack --include ''"
    Then the exit code is 2
    When I run "ananke context map --budget 1"
    Then the exit code is 2
    And the output contains "budget"

  @cap_context
  Scenario: Lookups answer from the graph and see an edit without being told
    When I run "ananke context outline pkg/core.py --signatures"
    Then it succeeds
    And the output contains "def add(a, b)"
    When I run "ananke context symbol add"
    Then it succeeds
    And the output contains "return a + b"
    Given the file "pkg/core.py" with content:
      """
      def add(a, b):
          return a + b + 1

      def total(items):
          return add(items[0], items[1])
      """
    When I run "ananke context symbol add"
    Then it succeeds
    And the output contains "return a + b + 1"
    When I run "ananke context callers add"
    Then it succeeds
    And the output contains "total"

  @cap_context
  Scenario: Enabling reaches each assistant, is repeatable, and can be undone
    When I run "ananke context enable --client all --dry-run"
    Then it succeeds
    And the output contains ".kiro/steering/ananke-context.md"
    When I run "ananke context enable --client all"
    Then it succeeds
    And the output contains each of:
      """
      CLAUDE.md
      AGENTS.md
      .github/copilot-instructions.md
      .cursor/rules/ananke-context.mdc
      .amazonq/rules/ananke-context.md
      .claude/settings.json
      """
    When I run "ananke context enable --client all"
    Then it succeeds
    And the JSON output at "enabled" equals true
    And the output does not contain "CLAUDE.md"
    When I run "ananke context status"
    Then it succeeds
    And the output contains "enabled_for"
    When I run "ananke context disable"
    Then it succeeds
    And the output contains "removed the hook"

  @cap_context @adversarial
  Scenario: Lookups refuse paths outside the repository, odd names and unknown assistants
    When I run "ananke context outline ../../etc/passwd"
    Then the exit code is 2
    And the output contains "outside the repository"
    When I run "ananke context symbol '$(touch pwned)'"
    Then the exit code is 2
    When I run "ananke context enable --client notepad"
    Then the exit code is 2
    And the output contains "unknown client"


Feature: A small map of a repository that can't go stale
  Background:
    Given a fresh project
    And the file "pkg/__init__.py" with content:
      """
      """
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

Feature: Lifecycle helpers, eval and testing views
  Background:
    Given a fresh project
    And I have run "ananke init"

  @cap_lifecycle
  Scenario: Governed branch names, worktrees and evidence events
    When I run "ananke lifecycle branch --ticket PROJ-7 --slug audit-log"
    Then it succeeds
    And the output contains "feature/PROJ-7-audit-log"
    When I run "ananke lifecycle worktrees"
    Then it succeeds
    When I run "ananke lifecycle evidence --format json"
    Then it succeeds

  @cap_lifecycle
  Scenario: Adapter auth reports what is missing, and exports a shell snippet
    When I run "ananke configure auth-validate"
    Then it fails cleanly
    When I run "ananke configure auth-export --shell bash"
    Then it succeeds
    And the output contains "adapters.env"

  @cap_eval
  Scenario: An eval run with nothing to score warns instead of passing
    When I run "ananke eval run --json"
    Then it succeeds
    And the JSON output at "gate_decision.verdict" equals "WARN"
    And the output contains "nothing was evaluated"

  @cap_testing @cap_backends
  Scenario: Test engines are listed and the fake backend answers
    When I run "ananke test list --json"
    Then it succeeds
    And the JSON output at "pytest-suite" equals "unit [pytest]"
    When I run "ananke backend test fake"
    Then it succeeds
    And the output contains "pong"

  @cap_spec
  Scenario: spec superpowers runs the whole pipeline in one command
    When I run "ananke spec superpowers --id SP-1 --title 'Export orders' --acceptance 'Orders export as CSV'"
    Then it succeeds
    And the output contains "Spec pipeline converged"
    And the file ".ananke/specs/SP-1-export-orders/spec.lock.json" exists

Feature: Code graph and architecture contracts give agents precise context
  Background:
    Given a fresh project
    And I have run "ananke init"

  @cap_graph
  Scenario: The graph maps the code and answers questions about it
    When I run "ananke graph build"
    Then it succeeds
    And it finishes within 60 seconds
    And a file matching ".ananke/graph/*.json" exists
    When I run "ananke graph query --text login"
    Then it succeeds
    And the output contains "matches"
    When I run "ananke graph export --format json"
    Then it succeeds
    And the file ".ananke/graph/export.json" exists

  @cap_graph
  Scenario: Impact analysis lists the symbols a change touches
    Given I have run "ananke graph build"
    When I run "ananke graph impact --changed-file src/shop/users.py"
    Then it succeeds
    And the output contains each of:
      """
      class:src/shop/users.py:User
      function:src/shop/users.py:find_user
      """

  @cap_arch
  Scenario: CALM architecture is validated, compared with the code and reconciled
    Given I have run "ananke graph build"
    When I run "ananke arch validate"
    Then it succeeds
    When I run "ananke arch diff"
    Then it succeeds
    And the output contains "missing_from_calm"
    And the output contains "src"
    When I run "ananke arch reconcile"
    Then it succeeds
    And the output contains "False"
    When I run "ananke arch reconcile --apply"
    Then it succeeds
    And the output contains "True"
    When I run "ananke arch render"
    Then it succeeds
    And the file ".ananke/architecture/diagrams.md" exists

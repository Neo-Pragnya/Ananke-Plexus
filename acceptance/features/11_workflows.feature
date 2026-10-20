@cap_workflows
Feature: Named workflows chain commands
  Background:
    Given a fresh project
    And I have run "ananke init"

  Scenario: A built-in pack installs, shows its steps and dry-runs
    When I run "ananke workflow install-pack verify-all"
    Then it succeeds
    When I run "ananke workflow list --json"
    Then the JSON output at "length" equals 1
    When I run "ananke workflow show verify-all"
    Then it succeeds
    And the output contains "ananke verify"
    When I run "ananke workflow run verify-all --dry-run"
    Then it succeeds
    And the output contains "would run, ok=True"

  Scenario: A custom workflow runs every step and reports each result
    When I run "ananke workflow create quick --step 'ananke version' --step 'ananke doctor'"
    Then it succeeds
    When I run "ananke workflow run quick --json"
    Then it succeeds
    And the JSON output at "ok" equals true
    And the JSON output at "steps.length" equals 2
    When I run "ananke workflow delete quick"
    Then it succeeds

  Scenario: A failing step stops the workflow with a non-zero exit
    Given I have run "ananke workflow create broken --step 'ananke version' --step 'ananke spec validate --feature-dir nowhere' --step 'ananke doctor'"
    When I run "ananke workflow run broken --json"
    Then it fails
    And the JSON output at "ok" equals false

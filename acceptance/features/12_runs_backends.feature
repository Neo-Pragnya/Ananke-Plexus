Feature: Runs move through a state machine; backends and plugins are discoverable
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke spec converge --id PROJ-1 --title 'Lock accounts' --acceptance 'Five failures lock the account'"

  @cap_runs
  Scenario: A run starts READY, has no pending approvals and can be cancelled
    When I run "ananke run start --spec-id PROJ-1"
    Then it succeeds
    And the output contains "Run started"
    When I run "ananke run status --run-id $(ls -t .ananke/runs | head -1)"
    Then it succeeds
    And the output contains "READY"
    When I run "ananke run approvals --run-id $(ls -t .ananke/runs | head -1)"
    Then it succeeds
    When I run "ananke run cancel --run-id $(ls -t .ananke/runs | head -1)"
    Then it succeeds
    And the output contains "CANCELLED"

  @cap_runs @cap_cli_ux
  Scenario: Asking about an unknown run is a clean failure
    When I run "ananke run status --run-id does-not-exist"
    Then it fails cleanly

  @cap_backends
  Scenario: The deterministic fake backend is always available
    When I run "ananke backend list --json"
    Then it succeeds
    And the JSON output at "name" contains "fake"
    When I run "ananke backend doctor fake"
    Then it succeeds

  @cap_backends
  Scenario: Plugins are discovered
    When I run "ananke plugin list"
    Then it succeeds
    And the output contains "Plugin discovery completed"

@cap_bootstrap
Feature: A repository becomes Ananke-ready
  `ananke init` lays down configuration, the default policy and an architecture file; `doctor` confirms the
  environment.

  Background:
    Given a fresh project

  Scenario: init creates the project skeleton
    When I run "ananke init"
    Then it succeeds
    And the output contains "Ananke project initialized"
    And the file ".ananke/config.toml" exists
    And the file ".ananke/policy/default.toml" exists
    And the file ".ananke/architecture/system.calm.json" exists
    And the file ".ananke/config.toml" contains "fail_closed = true"

  @adversarial
  Scenario: Adapter credentials and local config are never staged by git add -A
    Given I have run "ananke init"
    And the file ".ananke/secrets/adapters.env" with content containing a fake "github-token" credential:
      """
      ANANKE_BITBUCKET_APP_PASSWORD={credential}
      """
    When I run "git add -A && git status --porcelain"
    Then it succeeds
    And the output does not contain "secrets/"
    And the output contains ".ananke/config.toml"
    When I run "git check-ignore .ananke/config.local.toml .ananke/secrets/adapters.env"
    Then it succeeds
    And the output contains each of:
      """
      .ananke/config.local.toml
      .ananke/secrets/adapters.env
      """

  Scenario: init is safe to run twice
    Given I have run "ananke init"
    When I run "ananke init"
    Then it succeeds

  Scenario: doctor reports a healthy environment as JSON
    Given I have run "ananke init"
    When I run "ananke doctor --json"
    Then it succeeds
    And the JSON output at "ok" equals true

  Scenario: Config migration is a dry run unless asked
    Given I have run "ananke init"
    When I run "ananke config migrate"
    Then it succeeds
    And it does not crash

  @cap_runs
  Scenario: Run worktrees never end up inside the project's git index
    Given I have run "ananke init"
    And I have run "ananke spec converge --id PROJ-1 --title 'Lock accounts' --acceptance 'Five failures lock the account'"
    And I have run "ananke run start --spec-id PROJ-1"
    When I run "git add -A 2>&1"
    Then the output does not contain "embedded git repository"

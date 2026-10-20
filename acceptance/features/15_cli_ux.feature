@cap_cli_ux
Feature: Mistakes get clear answers
  Background:
    Given a fresh project

  Scenario: An unknown command is a usage error
    When I run "ananke frobnicate"
    Then it fails cleanly
    And the output contains "No such command"

  Scenario: Showing something the registry does not hold is a clean error
    Given I have run "ananke init"
    And I have run "ananke registry init"
    When I run "ananke registry show no-such-skill"
    Then it fails cleanly

  Scenario: Validating a spec folder that does not exist is a clean error
    Given I have run "ananke init"
    When I run "ananke spec validate --feature-dir .ananke/specs/missing"
    Then it fails
    And it does not crash

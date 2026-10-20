@cap_apm
Feature: apm installs and audits agent packages
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And I have run "ananke registry learn ./skills/good-skill"

  Scenario: A registry skill is installed into the project and verified
    When I run "apm install good-skill@0.1.0"
    Then it succeeds
    When I run "apm list --json"
    Then it succeeds
    And the JSON output at "installed" has at least 1 items
    When I run "apm verify"
    Then it succeeds

  Scenario: An installed skill reports its registry details
    Given I have run "apm install good-skill@0.1.0"
    When I run "apm info good-skill"
    Then it succeeds
    And the output contains "good-skill"

  @cap_cli_ux @known_issue
  Scenario: Auditing a file that is not an APM manifest is a clean error
    When I run "apm audit --manifest skills/good-skill/SKILL.md"
    Then it fails cleanly

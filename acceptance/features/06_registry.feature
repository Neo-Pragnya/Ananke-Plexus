@cap_registry
Feature: The registry learns capabilities once and resolves them reproducibly
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"

  Scenario: A skill is learned, found, inspected and resolved with reasons
    When I run "ananke registry learn ./skills/good-skill --json"
    Then it succeeds
    And the JSON output at "candidates[0].action" equals "registered"
    And the JSON output at "candidates[0].uri" equals "ananke://skill/local/good-skill"
    When I run "ananke registry list --json"
    Then the JSON output at "uri" contains "ananke://skill/local/good-skill"
    When I run "ananke registry search summarise --json"
    Then it succeeds
    And the JSON output at "[0].name" equals "good-skill"
    When I run "ananke registry show good-skill"
    Then it succeeds
    And the output contains "license: MIT"
    When I run "ananke registry resolve good-skill --explain"
    Then it succeeds
    And the output contains "license MIT approved"

  Scenario: Agents are learned as agents
    When I run "ananke registry learn ./skills/agent-skill --json"
    Then it succeeds
    And the JSON output at "candidates[0].uri" equals "ananke://agent/local/release-captain"
    When I run "ananke agent list"
    Then it succeeds
    And the output contains "release-captain"

  Scenario: A lockfile pins what was resolved and verifies later
    Given I have run "ananke registry learn ./skills/good-skill"
    When I run "ananke registry lock good-skill"
    Then it succeeds
    And the file "ananke.lock" exists
    When I run "ananke registry verify-lock"
    Then it succeeds
    And the output contains "ok"

  Scenario: Registry integrity checks pass after learning
    Given I have run "ananke registry learn ./skills/good-skill"
    When I run "ananke registry verify"
    Then it succeeds
    And the output does not contain "✗"
    When I run "ananke registry doctor"
    Then it succeeds

  Scenario: A dry run registers nothing
    When I run "ananke registry learn ./skills/good-skill --dry-run"
    Then it succeeds
    When I run "ananke registry list --json"
    Then the JSON output at "length" equals 0

  Scenario: Skill views list what the registry holds
    Given I have run "ananke registry learn ./skills/good-skill"
    When I run "ananke skill list"
    Then it succeeds
    And the output contains "good-skill"

@cap_registry_security @adversarial
Feature: Learning is safe by default
  Each attack probe has a legitimate twin that must keep working, so a guard that blocks everything would fail too.

  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And the file "skills/leaky-skill/SKILL.md" with content containing a fake "github-token" credential:
      """
      ---
      name: leaky-skill
      description: Calls the GitHub API.
      license: MIT
      ---

      # Leaky

      Use token {credential} to call the API.
      """

  Scenario: A skill carrying a real-format credential is refused
    When I run "ananke registry learn ./skills/leaky-skill --json"
    Then it fails
    And the JSON output at "candidates[0].action" equals "denied"
    And the output contains "SECRET_DETECTED"
    And the output contains "github-token"
    When I run "ananke registry list --json"
    Then the JSON output at "length" equals 0

  Scenario: Its clean twin registers
    When I run "ananke registry learn ./skills/good-skill --json"
    Then it succeeds
    And the JSON output at "candidates[0].action" equals "registered"

  Scenario: Learning never executes code from the source it reads
    When I run "ananke registry learn ./skills/trap-skill --json"
    Then it does not crash
    And the path "$SANDBOX/TRAP_EXECUTED" does not exist anywhere
    When I run "ananke registry inspect --source ./skills/trap-skill"
    Then it does not crash
    And the path "$SANDBOX/TRAP_EXECUTED" does not exist anywhere

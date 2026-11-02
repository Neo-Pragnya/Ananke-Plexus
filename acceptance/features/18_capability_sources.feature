Feature: Capability sources are source-neutral, exact and locked
  Background:
    Given a fresh project
    And I have run "ananke init"
    And the fixture "skills" is copied to "team-skills"
    And the file ".ananke/config.toml" with content:
      """
      [capability_sources.team]
      root = "team-skills"
      namespace = "acme"
      """

  @cap_sources
  Scenario: An approved folder is discovered and resolved exactly, without running anything
    When I run "ananke registry providers list"
    Then it succeeds
    And the output contains "team"
    When I run "ananke registry providers resolve team:acme/good-skill --json"
    Then it succeeds
    And the JSON output at "identity" equals "team:acme/good-skill"
    And the JSON output at "schema" equals "ananke/source-sidecar@1"

  @cap_sources
  Scenario: A lock survives a refresh and rejects bytes that changed
    When I run "ananke registry providers resolve team:acme/good-skill --lock sources.lock.json"
    Then it succeeds
    When I run "ananke registry providers check-lock sources.lock.json"
    Then it succeeds
    When I run:
      """
      echo "changed" >> team-skills/good-skill/SKILL.md
      ananke registry providers check-lock sources.lock.json
      """
    Then it fails
    And the output contains "DIGEST_MISMATCH"

  @cap_sources @adversarial
  Scenario: A symlink out of the approved folder is refused
    When I run:
      """
      ln -s "$HOME" team-skills/good-skill/escape
      ananke registry providers discover
      """
    Then it fails
    And the output contains "PATH_ESCAPE"

  @cap_sources
  Scenario: An unknown source is a clear error, not a fallback
    When I run "ananke registry providers resolve elsewhere:acme/good-skill"
    Then it fails
    And the output contains "SOURCE_UNAVAILABLE"

@cap_registry_lifecycle
Feature: Versions move through trust and lifecycle states, and resolution respects them
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And I have run "ananke registry learn ./skills/good-skill"

  Scenario: A quarantined version is never selected, and needs re-review after release
    When I run "ananke registry quarantine good-skill@0.1.0"
    Then it succeeds
    When I run "ananke registry resolve good-skill --explain"
    Then it fails
    And the output contains "quarantined"
    When I run "ananke registry release-quarantine good-skill@0.1.0"
    Then it succeeds
    And the output contains "trust restricted"
    When I run "ananke registry resolve good-skill"
    Then it fails
    And the output contains "trust status = restricted"
    When I run "ananke registry promote good-skill@0.1.0 --trust verified --reviewed-by qa"
    Then it succeeds
    When I run "ananke registry resolve good-skill"
    Then it succeeds

  Scenario: A yanked version is rejected until it is un-yanked
    When I run "ananke registry yank good-skill@0.1.0"
    Then it succeeds
    When I run "ananke registry resolve good-skill"
    Then it fails
    And the output contains "yanked"
    When I run "ananke registry unyank good-skill@0.1.0"
    Then it succeeds

  Scenario: Promotion records trust and channel
    When I run "ananke registry promote good-skill@0.1.0 --channel stable --trust verified --reviewed-by qa"
    Then it succeeds
    When I run "ananke registry show good-skill"
    Then the output contains "channel: stable"
    And the output contains "trust: verified"

  Scenario: Deprecation points at a replacement
    Given I have run "ananke registry learn ./skills/trap-skill"
    When I run "ananke registry deprecate trap-skill@0.1.0 --replacement good-skill"
    Then it succeeds
    And the output contains "deprecated"

  Scenario: Lifecycle commands demand an explicit version
    When I run "ananke registry quarantine good-skill"
    Then it fails cleanly
    And the output contains "must include a version"

  @cap_cli_ux @known_issue
  Scenario: An unknown trust level is a clean error, not a crash
    When I run "ananke registry promote good-skill@0.1.0 --trust reviewed"
    Then it fails cleanly
    And the output contains "approved"

  @known_issue
  Scenario: An unknown channel is refused
    When I run "ananke registry promote good-skill@0.1.0 --channel bogus"
    Then it fails cleanly

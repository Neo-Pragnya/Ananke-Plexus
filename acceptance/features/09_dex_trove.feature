@cap_dex @slow
Feature: Dex Trove plugs into the registry
  The [dex] extra installs Dex Trove; its wheel carries an ananke.yaml for every item.

  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"

  Scenario: The whole trove is learned, with placeholder secrets refused by design
    When I run "ananke registry learn python:dex-trove"
    Then it does not crash
    And the output contains "registered"
    And the output contains "SECRET_DETECTED"
    And it finishes within 240 seconds
    When I run "ananke registry list --json"
    Then the JSON output has at least 900 items

  Scenario: A Dex Trove skill resolves with a licence explanation
    Given I have run "ananke registry learn python:dex-trove || true"
    When I run "ananke registry resolve neo-pragnya/interactive-web-motion --explain"
    Then it succeeds
    And the output contains "license MIT approved"

  Scenario: Dex syncs a curated slice without denials
    When I run "dex ananke sync --project . --kind skill"
    Then it succeeds
    When I run "ananke skill list"
    Then the output contains "neo-pragnya/living-diagrams"

  Scenario: A project switches the trove on with [troves] and syncs a slice
    When I run "ananke registry troves list --json"
    Then it succeeds
    And the JSON output at "name" contains "dex-trove"
    When I run "ananke registry troves enable dex-trove --kind skill --namespace neo-pragnya"
    Then it succeeds
    And the file ".ananke/config.toml" contains "[troves.dex-trove]"
    When I run "ananke config migrate --project ."
    Then it succeeds
    And the output contains "unknown_count │ 0"
    When I run "ananke registry troves sync --json"
    Then it succeeds
    And the JSON output at "[0].denied" equals 0
    When I run "ananke skill list"
    Then the output contains "neo-pragnya/capability-acceptance"
    And the output does not contain "awesome-copilot/"

  Scenario: An unknown trove name is a clean error
    When I run "ananke registry troves enable no-such-trove"
    Then it fails cleanly
    And the output contains "no installed trove"


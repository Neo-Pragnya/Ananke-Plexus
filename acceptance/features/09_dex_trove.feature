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

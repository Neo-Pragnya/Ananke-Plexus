Feature: Governance runs where work happens
  Background:
    Given a fresh project
    And I have run "ananke init"

  @cap_policy
  Scenario: Policy rules are listed, explained and extended with packs
    When I run "ananke policy list --json"
    Then it succeeds
    And the JSON output at "id" contains "policy.core.fail-closed"
    When I run "ananke policy explain --stage verify"
    Then it succeeds
    And the output contains "PASS"
    When I run "ananke policy check"
    Then it succeeds

  @cap_policy
  Scenario: A policy pack adds rules, and check exits 3 when one blocks
    When I run "ananke policy install-pack python-library"
    Then it succeeds
    And the file ".ananke/policy/python-library.toml" exists
    When I run "ananke policy list --json"
    Then the JSON output has at least 2 items
    When I run "ananke policy check"
    Then the exit code is 3
    And it does not crash

  @cap_hooks
  Scenario: Git hooks install, report their status and uninstall
    When I run "ananke hooks install"
    Then it succeeds
    And the file ".git/hooks/pre-commit" exists
    And the file ".git/hooks/pre-push" exists
    When I run "ananke hooks status"
    Then it succeeds
    And the output contains "installed"
    When I run "ananke hooks uninstall"
    Then it succeeds
    And the output contains "removed"

  @cap_hooks @adversarial
  Scenario: The pre-commit stage blocks a staged credential
    Given I have run "ananke hooks install"
    And the file "src/shop/creds.py" with content containing a fake "aws-access-key" credential:
      """
      AWS_ACCESS_KEY_ID = "{credential}"
      """
    When I run "git add -A && ananke hooks run pre-commit"
    Then it fails
    And the output contains "blocked"

  @cap_verify
  Scenario: verify runs every gate and writes an evidence bundle
    When I run "ananke verify"
    Then it succeeds
    And the output contains "Verification completed"
    And a file matching ".ananke/evidence/*/manifest.json" exists
    And the JSON file ".ananke/evidence/*/run.json" at "gates.length" equals 8

  @cap_verify
  Scenario: A lint error blocks the ruff gate
    Given the file "src/shop/sloppy.py" with content:
      """
      import os, sys
      def f( x ):
          return undefined_name
      """
    When I run "ananke verify"
    Then the JSON file ".ananke/evidence/*/run.json" at "gates.ruff" equals "BLOCKED"

  @cap_evidence
  Scenario: Pruning evidence is a dry run unless --apply is given
    Given I have run "ananke verify"
    When I run "ananke evidence prune --older-than 0m"
    Then it succeeds
    And the output contains "dry-run"
    And a file matching ".ananke/evidence/*/manifest.json" exists

  @cap_cli_ux
  Scenario: A blocked pre-commit run is not reported with a success mark
    Given I have run "ananke hooks install"
    And the file "src/shop/creds.py" with content containing a fake "aws-access-key" credential:
      """
      AWS_ACCESS_KEY_ID = "{credential}"
      """
    When I run "git add -A && ananke hooks run pre-commit"
    Then it fails cleanly

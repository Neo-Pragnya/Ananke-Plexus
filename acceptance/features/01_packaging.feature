@cap_packaging
Feature: The package installs cleanly
  Before anything else works, the published wheel has to install, expose its commands and report the version that
  is being released.

  Scenario: The CLI reports the version being released
    When I run "ananke version"
    Then it succeeds
    And the output is the package version

  Scenario: Both entry points start and list their command groups
    When I run "ananke --help"
    Then it succeeds
    And the output contains each of:
      """
      registry
      spec
      verify
      serve-mcp
      """
    When I run "apm --help"
    Then it succeeds
    And the output contains "install"

  Scenario: The CLI starts quickly
    When I run "ananke --help"
    Then it succeeds
    And it finishes within 6 seconds

  Scenario: The offline documentation is available without network access
    When I run "ananke docs where"
    Then it succeeds
    And the output contains "has_assets_images"

Feature: Offline docs and the testing harness
  @cap_docs @slow
  Scenario: The offline documentation site builds
    Given an empty directory
    When I run "ananke docs build --output site"
    Then it succeeds
    And the file "site/index.html" exists
    And the file "site/guides/walkthroughs.html" exists
    And the file "site/assets/flows/walkthrough-quickstart.svg" exists
    And it finishes within 120 seconds

  @cap_testing
  Scenario: The harness discovers the project's test engines
    Given a fresh project
    And I have run "ananke init"
    When I run "ananke test discover --json"
    Then it succeeds
    And the JSON output at "pytest-suite" equals "unit"

  @cap_testing
  Scenario: A passing suite passes the quality gate
    Given a fresh project
    And I have run "ananke init"
    When I run "ananke test run --profile fast --json"
    Then it succeeds
    And the JSON output at "results[0].status" equals "pass"
    And the JSON output at "results[0].metrics.passed" equals 1

  @cap_testing
  Scenario: A failing test blocks the quality gate
    Given a fresh project
    And I have run "ananke init"
    And the file "tests/test_broken.py" with content:
      """
      def test_broken() -> None:
          assert 1 + 1 == 3
      """
    When I run "ananke test run --profile fast --json"
    Then it fails
    And the JSON output at "results[0].status" equals "fail"
    And the JSON output at "results[0].metrics.failed" equals 1

  @cap_testing
  Scenario: The fast profile runs only fast kinds
    Given a fresh project
    And I have run "ananke init"
    When I run "ananke test run --profile fast --json"
    Then the output does not contain "\"kind\": \"mutation\""

  @cap_testing
  Scenario: A run in which no test executed does not pass the gate
    Given a fresh project
    And I have run "ananke init"
    And I have run "rm -rf tests"
    When I run "ananke test run --profile fast --json"
    Then the JSON output at "results[0].status" equals "warn"

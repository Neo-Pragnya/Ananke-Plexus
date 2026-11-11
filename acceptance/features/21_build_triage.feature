Feature: A failed build is triaged from its log, with no access to the CI server
  Background:
    Given a fresh project

  @cap_build_triage
  Scenario: The first primary failure, its cascade and a rerun plan
    Given the file "console.log" with content:
      """
      Started by user admin
      [Pipeline] { (Build)
      + uv sync
      [Pipeline] // stage
      [Pipeline] { (Test)
      + pytest tests/api -q
      FAILED tests/api/test_orders.py::test_create - assert 500 == 201
      ERROR: script returned exit code 1
      [Pipeline] // stage
      [Pipeline] { (Deploy)
      Stage "Deploy" skipped due to earlier failure(s)
      [Pipeline] // stage
      Finished: FAILURE
      """
    And the file "Jenkinsfile" with content:
      """
      pipeline {
        stages {
          stage('Test') {
            steps { sh 'pytest tests/api -q' }
          }
        }
      }
      """
    When I run "ananke triage jenkins console.log --jenkinsfile Jenkinsfile --changed tests/api/test_orders.py --changed web/App.tsx --json"
    Then the exit code is 1
    And the JSON output at "first_failure.stage" equals "Test"
    And the JSON output at "cascade" equals ["Deploy"]
    And the JSON output at "related_changes[0].path" equals "tests/api/test_orders.py"
    And the output does not contain "web/App.tsx"

  @cap_build_triage @adversarial
  Scenario: A file that isn't a Jenkins log is refused
    Given the file "notes.txt" with content:
      """
      nothing to see here
      """
    When I run "ananke triage jenkins notes.txt"
    Then the exit code is 2
    And the output contains "INVALID_INPUT"

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

  @cap_build_triage
  Scenario: Findings from the repository's reports are ranked against what changed
    Given the file "pkg/a.py" with content:
      """
      from pkg import b
      """
    And the file "pkg/__init__.py" with content:
      """
      """
    And the file "pkg/b.py" with content:
      """
      def q():
          return 1
      """
    And the file "semgrep.sarif" with content:
      """
      {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Semgrep"}}, "results": [
        {"ruleId": "r1", "level": "error", "message": {"text": "unsafe"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "pkg/b.py"}, "region": {"startLine": 2}}}]}]}]}
      """
    When I run "ananke triage findings --changed pkg/b.py --json"
    Then it succeeds
    And the JSON output at "findings[0].tier" equals "fix now"
    And the JSON output at "impacted" equals ["pkg/a.py"]
    And the JSON output at "rerun[0].gate" equals "semgrep"

  @cap_build_triage
  Scenario: API contract gaps name untested routes and recommend tests
    Given the file "api/main.py" with content:
      """
      from fastapi import FastAPI

      app = FastAPI()


      @app.get("/orders/{order_id}")
      def get_order(order_id: int):
          return {}


      @app.post("/orders")
      def create_order(body: dict):
          return {}
      """
    And the file "tests/test_api.py" with content:
      """
      def test_get(client):
          client.get("/orders/1")
      """
    When I run "ananke triage contracts --json"
    Then it succeeds
    And the JSON output at "summary.tested" equals 1
    And the JSON output at "gaps.untested_routes" equals ["route:POST /orders"]
    And the output contains "test_post_orders_rejects_invalid_body"

  @cap_build_triage
  Scenario: A session becomes a lesson draft, checked for overlap
    Given I have run "git add -A && git -c user.email=t@x.invalid -c user.name=t commit -qm base --allow-empty"
    And the file "app.py" with content:
      """
      X = 2
      """
    And I have run "git add -A && git -c user.email=t@x.invalid -c user.name=t commit -qm 'Pin the clock in tests' -m 'Chose a fixed clock over retries because retries hid a bug.'"
    When I run "ananke learning draft --base HEAD~1 -o lesson.md"
    Then it succeeds
    And the file "lesson.md" contains "Chose a fixed clock over retries"
    When I run "ananke learning overlap lesson.md --json"
    Then it succeeds
    And the output contains "checked"

  @cap_build_triage
  Scenario: Infrastructure, its links and a safe order to check it
    Given the file "docker-compose.yml" with content:
      """
      services:
        api:
          build: ./api
          depends_on: [db]
        db:
          image: postgres:16
      """
    And the file "api/Dockerfile" with content:
      """
      FROM python:3.12-slim
      """
    When I run "ananke triage infra --json"
    Then it succeeds
    And the JSON output at "sequence[0].step" equals "validate"
    And the output contains "runs as root"
    And the output contains "doesn't wait for it to be healthy"

  @cap_build_triage
  Scenario: A question gets just the code it's about
    Given the file "shop/pricing.py" with content:
      """
      def apply_discount(total, tier):
          return total * 0.8 if tier == "gold" else total
      """
    When I run "ananke context ask 'where is the gold discount applied' --json"
    Then it succeeds
    And the JSON output at "plan[0].name" equals "apply_discount"
    When I run "ananke context ask 'where is the gold discount applied' --json"
    Then it succeeds
    And the JSON output at "cached" equals true

  @cap_build_triage
  Scenario: Contract tests from an OpenAPI document, run only on this machine
    Given the file "openapi.json" with content:
      """
      {"openapi": "3.0.3", "info": {"title": "Shop", "version": "1"},
       "paths": {"/orders/{order_id}": {"get": {"responses": {"200": {}}}}}}
      """
    When I run "ananke triage api-tests openapi.json --json"
    Then it succeeds
    And the JSON output at "cases[1].case" equals "unknown order_id"
    And the JSON output at "cases[1].status" equals "suggested"
    And the file "tests/api_contract/test_api_contract.py" contains "ANANKE_API_BASE_URL"
    When I run "ananke triage api-tests openapi.json --run https://example.com"
    Then the exit code is 2
    And the output contains "only against a service on this machine"

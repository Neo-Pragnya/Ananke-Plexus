Feature: Products drive Ananke through a versioned, idempotent app facade
  Background:
    Given a fresh project
    And I have run "ananke init"
    And the fixture "skills" is copied to "skills"
    And I have run "git add -A && git -c user.name=t -c user.email=t@example.com commit -qm init"

  @cap_app_facade
  Scenario: A graph run executes on the real harness and returns verified artifacts
    When I run "ananke app run graph.build --key first"
    Then it succeeds
    And the JSON output at "state" equals "succeeded"
    And the JSON output at "complete" equals true
    And the JSON output at "artifact_refs[0].name" equals "graph-snapshot.json"

  @cap_app_facade @adversarial
  Scenario: The same key with a different request is refused, and old protocols are told to upgrade
    When I run "ananke app run graph.build --key same"
    Then it succeeds
    When I run:
      """
      ananke app run graph.impact --key same --params '{"changed_files": ["skills/good-skill/SKILL.md"]}'
      """
    Then the exit code is 2
    And the output contains "IDEMPOTENCY_CONFLICT"
    When I run "ananke app capabilities --protocol ananke-app/0"
    Then the exit code is 2
    And the output contains "UNSUPPORTED_VERSION"

  @cap_app_facade
  Scenario: A queued run can be cancelled and its events are ordered
    When I run:
      """
      RUN=$(ananke app run graph.build --key q --queue-only | python3 -c "import json,sys;print(json.load(sys.stdin)['run_id'])")
      ananke app cancel "$RUN" >/dev/null && ananke app events "$RUN"
      """
    Then it succeeds
    And the output contains each of:
      """
      "type": "run.queued"
      "type": "run.cancelled"
      """

  @cap_app_facade
  Scenario: A product reads a finished run's status, evidence and verified artifact
    When I run:
      """
      ananke app run graph.build --key r | python3 -c "import json,sys;print(json.load(sys.stdin)['run_id'])" > run-id
      """
    Then it succeeds
    When I run "ananke app status $(cat run-id)"
    Then it succeeds
    And the JSON output at "state" equals "succeeded"
    When I run "ananke app artifacts $(cat run-id)"
    Then it succeeds
    And the output contains "graph-snapshot.json"
    When I run "ananke app read $(cat run-id) graph-snapshot.json -o snapshot.json"
    Then it succeeds
    And the file "snapshot.json" exists
    When I run "ananke app evidence"
    Then it succeeds
    And the JSON output at "total" equals 1
    And the JSON output at "runs[0].state" equals "succeeded"

  @cap_app_facade @adversarial
  Scenario: An unknown artifact is refused, and a queued run can be cancelled directly
    When I run:
      """
      ananke app run graph.build --key c --queue-only | python3 -c "import json,sys;print(json.load(sys.stdin)['run_id'])" > run-id
      """
    Then it succeeds
    When I run "ananke app cancel $(cat run-id)"
    Then it succeeds
    And the JSON output at "state" equals "cancelled"
    When I run "ananke app read $(cat run-id) ../../secrets.txt -o stolen.txt"
    Then it fails
    And the file "stolen.txt" does not exist

  @cap_app_facade
  Scenario: Asking for the architecture view creates no model; once declared, it shows the model
    When I run "rm .ananke/architecture/system.calm.json"
    Then it succeeds
    When I run "ananke app architecture"
    Then the exit code is 75
    And the output contains "ARCHITECTURE_MISSING"
    And the file ".ananke/architecture/system.calm.json" does not exist
    When I run "ananke arch init"
    Then it succeeds
    When I run "ananke app architecture"
    Then it succeeds
    And the output contains each of:
      """
      "model"
      "validation"
      "drift"
      """

  @cap_app_facade @adversarial
  Scenario: A hostile run id can't leave the project
    Given the file "../run.json" with content:
      """
      {"secret": "outside"}
      """
    When I run "ananke app status ../"
    Then the exit code is 2
    And the output contains "RUN_NOT_FOUND"
    And the output does not contain "secret"
    When I run "ananke run status ../../.."
    Then it fails cleanly
    And the output does not contain "secret"

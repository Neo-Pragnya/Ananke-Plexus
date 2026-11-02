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

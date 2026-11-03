Feature: Workflows are validated, explained and compiled without running anything
  Background:
    Given a fresh project
    And I have run "ananke init"
    And I have run "ananke registry init"
    And the fixture "skills" is copied to "skills"
    And I have run "ananke registry learn ./skills/good-skill"
    And the file "draft.json" with content:
      """
      {"schema_version": "1.0.0", "workflow_id": "demo/review",
       "nodes": [
         {"id": "a", "capability_ref": "local/good-skill@0.1.0", "kind": "skill",
          "inputs": [{"name": "source", "schema_ref": "python-source/v1"}],
          "outputs": [{"name": "report", "schema_ref": "report/v1"}]},
         {"id": "b", "capability_ref": "local/good-skill@0.1.0", "kind": "skill",
          "inputs": [{"name": "report", "schema_ref": "report/v1"}],
          "outputs": [{"name": "summary", "schema_ref": "markdown/v1"}], "approval_class": "PLAN"}],
       "edges": [{"id": "e1", "source": "a", "source_port": "report", "target": "b", "target_port": "report"}]}
      """

  @cap_assembly
  Scenario: A valid draft previews and compiles to a stable plan, and nothing runs
    When I run "ananke app workflow-preview draft.json"
    Then it succeeds
    And the JSON output at "ok" equals true
    And the JSON output at "approvals_needed" equals ["PLAN"]
    And the JSON output at "workflow_inputs" equals ["a.source"]
    When I run "ananke app workflow-compile draft.json"
    Then it succeeds
    And the JSON output at "plan.steps[1].depends_on" equals ["a"]
    And the output contains "plan_hash"
    When I run:
      """
      test "$(ananke app workflow-compile draft.json | grep plan_hash)" = "$(ananke app workflow-compile draft.json | grep plan_hash)"
      """
    Then it succeeds

  @cap_assembly @adversarial
  Scenario: A port mismatch is rejected with a next step, and no plan is produced
    Given the file "bad.json" with content:
      """
      {"schema_version": "1.0.0", "workflow_id": "demo/bad",
       "nodes": [
         {"id": "a", "capability_ref": "local/good-skill@0.1.0", "kind": "skill", "inputs": [],
          "outputs": [{"name": "doc", "schema_ref": "markdown/v1"}]},
         {"id": "b", "capability_ref": "local/good-skill@0.1.0", "kind": "skill",
          "inputs": [{"name": "findings", "schema_ref": "sarif/v2"}], "outputs": []}],
       "edges": [{"id": "e1", "source": "a", "source_port": "doc", "target": "b", "target_port": "findings"}]}
      """
    When I run "ananke app workflow-validate bad.json"
    Then it succeeds
    And the JSON output at "ok" equals false
    And the output contains each of:
      """
      PORT_MISMATCH
      requires sarif/v2
      add an explicit node
      """
    When I run "ananke app workflow-compile bad.json"
    Then the exit code is 2
    And the output contains "PORT_MISMATCH"

  @cap_assembly
  Scenario: A product reads the code graph and source without changing anything
    When I run "ananke app code"
    Then the exit code is 75
    And the output contains "GRAPH_MISSING"
    When I run "ananke app source ../outside.py"
    Then the exit code is 2
    And the output contains "PATH_ESCAPE"

  @cap_assembly @adversarial
  Scenario: Suggestions explain why a candidate doesn't fit, and change nothing
    When I run "ananke app workflow-suggest draft.json b report --candidate local/good-skill@0.1.0 --candidate local/nope@9.9.9"
    Then it succeeds
    And the JSON output at "requires" equals "report/v1"
    And the JSON output at "candidates[0].compatible" equals false
    And the JSON output at "candidates[1].compatible" equals false
    And the JSON output at "candidates[1].authorized" equals false
    And the output contains each of:
      """
      declares no output ports
      can't be resolved
      """
    When I run "ananke registry list --json"
    Then it succeeds
    And the output does not contain "nope"

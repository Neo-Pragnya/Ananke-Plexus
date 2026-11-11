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

  @cap_assembly
  Scenario: A previewed workflow runs through a named backend with evidence, labelled as simulated
    When I run:
      """
      ananke app workflow-preview draft.json | python3 -c "import json,sys;print(json.load(sys.stdin)['plan_hash'])" > plan-hash
      """
    Then it succeeds
    When I run "ananke app workflow-run draft.json --backend fake --plan-hash $(cat plan-hash) --approve b --input a.source=src --key one"
    Then it succeeds
    And the JSON output at "state" equals "succeeded"
    And the JSON output at "complete" equals false
    And the output contains each of:
      """
      SIMULATED_BACKEND
      workflow-plan.json
      01-a.output.txt
      02-b.output.txt
      artifact:01-a.output.txt
      """

  @cap_assembly @adversarial
  Scenario: A workflow is refused whole unless it is the previewed plan, approved and fully supplied
    When I run:
      """
      ananke app workflow-preview draft.json | python3 -c "import json,sys;print(json.load(sys.stdin)['plan_hash'])" > plan-hash
      """
    Then it succeeds
    When I run "ananke app workflow-run draft.json --backend fake --plan-hash sha256:other --approve b --input a.source=src --key stale"
    Then the output contains "STALE_PLAN"
    When I run "ananke app workflow-run draft.json --backend fake --plan-hash $(cat plan-hash) --input a.source=src --key unapproved"
    Then the output contains "APPROVAL_REQUIRED"
    When I run "ananke app workflow-run draft.json --backend fake --plan-hash $(cat plan-hash) --approve b --key noinput"
    Then the output contains "workflow inputs not supplied"
    When I run "ananke app workflow-run draft.json --backend nope --plan-hash $(cat plan-hash) --approve b --input a.source=src --key nobackend"
    Then the output contains "BACKEND_NOT_FOUND"
    And the output does not contain ".output.txt"

  @cap_assembly @adversarial
  Scenario: Gaps name what nothing feeds, and a proposal is checked like any draft but never compiled
    Given the file "gap.json" with content:
      """
      {"schema_version": "1.0.0", "workflow_id": "demo/gap",
       "nodes": [
         {"id": "a", "capability_ref": "local/good-skill@0.1.0", "kind": "skill",
          "inputs": [{"name": "source", "schema_ref": "python-source/v1"}],
          "outputs": [{"name": "report", "schema_ref": "report/v1"}]},
         {"id": "b", "capability_ref": "local/good-skill@0.1.0", "kind": "skill",
          "inputs": [{"name": "report", "schema_ref": "report/v1", "required": true}],
          "outputs": [{"name": "summary", "schema_ref": "markdown/v1"}]}],
       "edges": []}
      """
    When I run "ananke app workflow-gaps gap.json"
    Then it succeeds
    And the JSON output at "gaps[0].node" equals "b"
    And the JSON output at "gaps[0].requires" equals "report/v1"
    And the JSON output at "auto_activate" equals false
    When I run "ananke app workflow-proposal draft.json --proposer 'a model I ran'"
    Then it succeeds
    And the JSON output at "status" equals "proposal"
    And the JSON output at "compiled" equals false
    And the JSON output at "valid" equals true

  @cap_connectors
  Scenario: A connector converts in its own process, and its typed ports fill a workflow gap
    Given the file "results.xml" with content:
      """
      <testsuite name="s" tests="2">
        <testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py" line="3"/>
        <testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py" line="9"><failure message="assert 1 == 2"/></testcase>
      </testsuite>
      """
    When I run "ananke connector run junit-to-sarif results.xml -o results.sarif"
    Then it succeeds
    And the JSON file "results.sarif" at "version" equals "2.1.0"
    And the JSON file "results.sarif" at "runs[0].results[0].ruleId" equals "test-failure"
    When I run "ananke connector register --approve"
    Then it succeeds
    And the JSON output at "registered[1]" equals "ananke/junit-to-sarif@1.0.0"
    When I run "ananke connector list"
    Then it succeeds
    And the output contains "sarif/2.1.0"

  @cap_connectors @adversarial
  Scenario: Input a connector can't read is refused, and nothing is written
    Given the file "bad.xml" with content:
      """
      <!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><testsuite>&e;</testsuite>
      """
    When I run "ananke connector run junit-to-sarif bad.xml -o out.sarif"
    Then the exit code is 2
    And the output contains "INVALID_INPUT"
    And the file "out.sarif" does not exist

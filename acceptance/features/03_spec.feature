@cap_spec
Feature: The spec pipeline turns intent into locked, traceable artifacts
  Background:
    Given a fresh project
    And I have run "ananke init"

  Scenario: create, plan, tasks and lock produce each artifact
    When I run "ananke spec create --id PROJ-7 --title 'Add audit log' --acceptance 'Every login is recorded'"
    Then it succeeds
    And the file ".ananke/specs/PROJ-7-add-audit-log/spec.md" exists
    And the file ".ananke/specs/PROJ-7-add-audit-log/requirement.md" exists
    When I run "ananke spec plan --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the file ".ananke/specs/PROJ-7-add-audit-log/plan.md" exists
    When I run "ananke spec tasks --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the file ".ananke/specs/PROJ-7-add-audit-log/tasks.md" exists
    When I run "ananke spec lock --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the file ".ananke/specs/PROJ-7-add-audit-log/spec.lock.json" exists
    When I run "ananke spec validate --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the output contains "Spec valid"

  Scenario: converge runs the whole pipeline in one command
    When I run "ananke spec converge --id PROJ-101 --title 'Rate-limit the login API' --acceptance 'Five failed logins lock the account'"
    Then it succeeds
    And the output contains "Spec pipeline converged"
    And the file ".ananke/specs/PROJ-101-rate-limit-the-login-api/bmad.yaml" exists
    And a file matching ".ananke/specs/PROJ-101-rate-limit-the-login-api/tests/test_*_behavior.py" exists
    And the file ".ananke/specs/PROJ-101-rate-limit-the-login-api/spec.lock.json" exists

  Scenario: Editing a locked spec is detected as drift
    Given I have run "ananke spec converge --id PROJ-9 --title 'Export orders' --acceptance 'Orders export as CSV'"
    When I run "echo 'scope creep' >> .ananke/specs/PROJ-9-export-orders/spec.md && ananke spec diff --feature-dir .ananke/specs/PROJ-9-export-orders"
    Then it fails
    And the output contains "HASH_MISMATCH:spec.md"
    When I run "ananke spec validate --feature-dir .ananke/specs/PROJ-9-export-orders"
    Then it fails
    And the output contains "HASH_MISMATCH:spec.md"

  @cap_cli_ux
  Scenario: Drift is reported as a failure, not with a success mark
    Given I have run "ananke spec converge --id PROJ-9 --title 'Export orders' --acceptance 'Orders export as CSV'"
    When I run "echo 'scope creep' >> .ananke/specs/PROJ-9-export-orders/spec.md && ananke spec validate --feature-dir .ananke/specs/PROJ-9-export-orders"
    Then it fails cleanly

  @cap_bmad
  Scenario: BMAD contracts compile, verify and trace back to acceptance criteria
    Given I have run "ananke spec create --id PROJ-7 --title 'Add audit log' --acceptance 'Every login is recorded'"
    When I run "ananke bmad compile --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the file ".ananke/specs/PROJ-7-add-audit-log/bmad.yaml" exists
    When I run "ananke bmad verify --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the output contains "BMAD verify: ok"
    When I run "ananke bmad trace --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds
    And the output contains "Every login is recorded"
    When I run "ananke bmad show --feature-dir .ananke/specs/PROJ-7-add-audit-log"
    Then it succeeds

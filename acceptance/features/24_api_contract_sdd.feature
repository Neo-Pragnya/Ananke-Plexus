Feature: API contracts are inventoried from code and OpenAPI, and an approved PRD compiles to BMAD contracts
  Background:
    Given a fresh project

  @cap_api_contract
  Scenario: Auth, request and response shapes, missing error responses and untested auth
    Given the file "api/models.py" with content:
      """
      from pydantic import BaseModel


      class OrderIn(BaseModel):
          customer_id: int
          note: str | None = None
      """
    And the file "api/main.py" with content:
      """
      from fastapi import Depends, FastAPI, HTTPException

      from .models import OrderIn

      app = FastAPI()


      def get_current_user():
          return "u"


      @app.post("/orders", status_code=201)
      def create_order(order: OrderIn, user=Depends(get_current_user)):
          raise HTTPException(status_code=422)


      @app.get("/health")
      def health():
          return {}
      """
    When I run "ananke triage api-contract --json --out contract"
    Then it succeeds
    And the JSON output at "summary.operations" equals 2
    And the JSON output at "summary.secured" equals 1
    And the output contains "OrderIn"
    And the output contains "auth-without-401"
    And the output contains "untested-auth"
    And the output contains "missing-error-response"
    And the output contains "api/main.py:"
    And the file "contract/model-contract.yaml" contains "compatibility_policy: backward_compatible"
    When I run "ananke triage api-contract --fail-on high"
    Then the exit code is 1

  @cap_api_contract
  Scenario: A malformed OpenAPI file and an unsupported format are refused with a reason
    Given the file "openapi.yaml" with content:
      """
      openapi: 3.0.0
      info: [not, a, mapping
      """
    When I run "ananke triage api-contract --spec openapi.yaml"
    Then the exit code is 2
    And the output contains "malformed"
    When I run "ananke triage api-contract --spec api.raml"
    Then the exit code is 2
    And the output contains "unsupported format"

  @cap_auto_sdd
  Scenario: An approved PRD maps to features, acceptance ids and tests, and compiles to BMAD contracts
    Given the file "docs/prd.md" with content:
      """
      ---
      title: Orders
      status: approved
      ---
      # Orders

      ## Requirements

      ### Placing orders

      - **R1** A customer places an order with `POST /orders`.
      - **R2** Placing an order needs a signed-in user.

      ## Acceptance

      - R1: a valid order returns 201.
      - R2: no credentials returns 401.
      """
    And the file "tests/test_orders.py" with content:
      """
      def test_no_credentials():
          # AC-R2-1
          assert True
      """
    When I run "ananke spec map docs/prd.md --json"
    Then it succeeds
    And the JSON output at "consistent" equals true
    And the JSON output at "summary.acceptance_with_tests" equals 1
    And the output contains "AC-R1-1"
    When I run "ananke spec compile docs/prd.md --out bmad"
    Then it succeeds
    And the file "bmad/bmad-index.json" contains "prd_map_digest"
    And the file "bmad/r1/bmad.yaml" contains "acceptance_id: AC-R1-1"
    And the file "bmad/r2/tests/test_r2_behavior.py" contains "AC-1"

  @cap_auto_sdd
  Scenario: A PRD that isn't approved or consistent blocks compiling
    Given the file "docs/prd.md" with content:
      """
      ---
      status: draft
      ---
      ## Requirements

      - **R1** Something with no acceptance.
      """
    When I run "ananke spec map docs/prd.md"
    Then the exit code is 1
    And the output contains "PRD_NOT_APPROVED"
    And the output contains "REQUIREMENT_WITHOUT_ACCEPTANCE"
    When I run "ananke spec compile docs/prd.md --out bmad"
    Then the exit code is 1
    And the file "bmad/bmad-index.json" does not exist

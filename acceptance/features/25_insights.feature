Feature: Advanced insights are deterministic, run nothing unasked and label what they claim
  Background:
    Given a fresh project

  @cap_insights
  Scenario: The time machine and the what-if simulation read history without touching the tree
    Given the file "app/pricing.py" with content:
      """
      def total(items):
          return sum(items)
      """
    And the file "app/orders.py" with content:
      """
      from app.pricing import total


      def place(items):
          return total(items)
      """
    And the file "tests/test_orders.py" with content:
      """
      from app.orders import place


      def test_place():
          assert place([1]) == 1
      """
    And I have run "git init -q -b main . && git add -A && git -c user.email=t@x.invalid -c user.name=t commit -qm base"
    And the file "app/audit.py" with content:
      """
      from app.orders import place


      def log(x):
          return place(x)
      """
    When I run "ananke graph diff --base HEAD --json"
    Then it succeeds
    And the output contains "function:app/audit.py:log"
    When I run "ananke graph whatif --change app/pricing.py --json"
    Then it succeeds
    And the JSON output at "tests_to_run" equals ["tests/test_orders.py"]
    And the output contains "simulated"
    When I run "ananke graph diff --base no-such-revision"
    Then the exit code is 2

  @cap_insights
  Scenario: A notebook run out of order is not reproducible as saved
    Given the file "analysis.ipynb" with content:
      """
      {"nbformat": 4, "nbformat_minor": 5, "metadata": {"language_info": {"version": "3.12"}}, "cells": [
       {"cell_type": "code", "source": "y = x + 1", "execution_count": 2, "outputs": [], "metadata": {}},
       {"cell_type": "code", "source": "x = 1", "execution_count": 1, "outputs": [], "metadata": {}}]}
      """
    When I run "ananke triage notebooks --json"
    Then it succeeds
    And the JSON output at "summary.not_reproducible" equals 1
    And the output contains "used-before-defined"
    And the output contains "out-of-order"

  @cap_insights
  Scenario: Drift between a Compose file and a docker inspect snapshot names both sides and never values
    Given the file "docker-compose.yml" with content:
      """
      services:
        api:
          image: shop/api:2.0
          environment:
            SECRET_KEY: x
      """
    And the file "inspect.json" with content:
      """
      [{"Name": "/shop-api-1", "Config": {"Image": "shop/api:1.9", "Env": ["OTHER=hidden-value"],
        "Labels": {"com.docker.compose.service": "api"}}, "State": {"Status": "running"}}]
      """
    When I run "ananke triage drift --snapshot inspect.json --fail-on-drift"
    Then the exit code is 1
    And the output contains "image differs"
    And the output contains "SECRET_KEY"
    And the output does not contain "hidden-value"

  @cap_insights
  Scenario: Test gaps come from coverage and each generated test fails until written
    Given the file "app/pricing.py" with content:
      """
      def total(items):
          if not items:
              return 0
          return sum(items)
      """
    And the file "coverage.xml" with content:
      """
      <?xml version="1.0" ?>
      <coverage><packages><package><classes><class filename="app/pricing.py"><lines>
      <line number="2" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="3"/>
      </lines></class></classes></package></packages></coverage>
      """
    When I run "ananke triage test-gaps --coverage coverage.xml --write gaps/test_gaps.py"
    Then it succeeds
    And the output contains "app/pricing.py:2"
    And the file "gaps/test_gaps.py" contains "pytest.fail"

  @cap_offline_cve
  Scenario: An offline OSV copy answers vulnerability checks with nothing sent
    Given the file "osv/PYSEC-1.json" with content:
      """
      {"id": "PYSEC-1", "modified": "2026-01-01T00:00:00Z", "affected": [{"package": {"ecosystem": "PyPI",
        "name": "jinja2"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.1.5"}]}]}]}
      """
    And the file "requirements.txt" with content:
      """
      jinja2==3.1.4
      """
    When I run "ananke cve import osv --db osv.sqlite"
    Then it succeeds
    And the output contains "PyPI"
    When I run "ananke cve status --db osv.sqlite"
    Then it succeeds
    And the output contains "imported_at"

  @cap_insights
  Scenario: A symbol is replaced and renamed safely, as dry runs until written
    Given the file "shop/cart.py" with content:
      """
      def total(items):
          # total is the sum
          return sum(items)


      LABEL = "total"


      def receipt(items):
          return total(items)
      """
    And the file "new_total.txt" with content:
      """
      def total(items):
          return round(sum(items), 2)
      """
    When I run "ananke context replace total --with new_total.txt"
    Then it succeeds
    And the output contains "+    return round(sum(items), 2)"
    And the file "shop/cart.py" contains "return sum(items)"
    When I run "ananke context replace total --with new_total.txt --write"
    Then it succeeds
    And the file "shop/cart.py" contains "return round(sum(items), 2)"
    When I run "ananke context rename total grand_total --write"
    Then it succeeds
    And the file "shop/cart.py" contains "def grand_total(items):"
    And the file "shop/cart.py" contains "return grand_total(items)"
    And the file "shop/cart.py" contains "# total is the sum"
    When I run "ananke context rename receipt class"
    Then the exit code is 2

Feature: One analysis answers what a repository holds and how it connects
  Background:
    Given a fresh project
    And I have run "ananke init"
    And the file "backend/app/__init__.py" with content:
      """
      """
    And the file "backend/app/db.py" with content:
      """
      def connect():
          return None
      """
    And the file "backend/app/models.py" with content:
      """
      from sqlalchemy import Column, ForeignKey, Integer
      from .db import connect
      class Customer(Base):
          __tablename__ = "customers"
          id = Column(Integer, primary_key=True)
      class Order(Base):
          __tablename__ = "orders"
          id = Column(Integer, primary_key=True)
          customer_id = Column(Integer, ForeignKey("customers.id"))
      def save(session):
          connect()
          session.add(Order(customer_id=1))
      """
    And the file "frontend/src/lib/http.ts" with content:
      """
      export function send(path: string) {
        return fetch(path);
      }
      """
    And the file "frontend/src/api.ts" with content:
      """
      import { send } from "./lib/http";
      export function load() {
        return send("/orders");
      }
      """
    And the file "backend/requirements.txt" with content:
      """
      flask==3.0.0
      """
    And I have run "git add -A && git -c user.name=t -c user.email=t@example.com commit -qm init"
    And I have run "ananke app run repo.analyze --key r"

  @cap_repo_intel
  Scenario: Folders connect, tables are found, and unknown stays unknown
    When I run:
      """
      python3 -c "import glob,json;r=json.load(open(glob.glob('.ananke/app/runs/*/artifacts/repo-intel.json')[0]));s=r['sections'];assert r['schema']=='ananke-repo-intel/1';assert {t['name'] for t in s['data']['tables']}=={'customers','orders'};assert s['data']['relationships'][0]['to_table']=='customers';assert 'erDiagram' in s['data']['erd_mermaid'];assert s['coverage']['status']=='not_found';assert s['dependencies']['status']=='not_scanned' and s['dependencies']['vulnerabilities'] is None;print('ok')"
      """
    Then it succeeds
    And the output contains "ok"
    Given I have run "ananke app run graph.build --key g"
    When I run "ananke app code --kind file"
    Then it succeeds
    And the output contains each of:
      """
      "via_module"
      frontend/src/lib/http.ts
      file:backend/app/db.py
      """

  @cap_repo_intel @adversarial
  Scenario: A hostile file can't stop the analysis, and nothing in the repository runs
    Given the file "boom.py" with content:
      """
      open("ran.txt", "w").write("x")
      """
    And the file "bad.py" with content:
      """
      def broken(
      """
    When I run "ananke app run repo.analyze --key hostile"
    Then it succeeds
    And the JSON output at "state" equals "succeeded"
    And the file "ran.txt" does not exist

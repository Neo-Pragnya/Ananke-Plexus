"""CAP-infra-docker-01..04: resources and links, container logs by service, across projects, a safe check order."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ananke.plexus.cli.app import app
from ananke.plexus.intel.infra import scan, triage_log, validation_sequence

COMPOSE = """services:
  api:
    build: ./api
    environment:
      PAYMENTS_URL: http://payments:8080
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  db:
    image: postgres:16
"""
K8S = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    metadata:
      labels: {app: api}
    spec:
      containers:
        - name: api
          image: shop/api:1.2
---
apiVersion: v1
kind: Service
metadata:
  name: api-svc
spec:
  selector: {app: api}
"""
TF = """resource "aws_s3_bucket" "assets" {
  bucket = "shop-assets"
}

resource "aws_s3_bucket_policy" "assets" {
  bucket = aws_s3_bucket.assets.id
  policy = var.policy
}
"""
LOG = """db-1   | 2026-11-11 10:00:00 LOG:  database system is ready to accept connections
api-1  | INFO: starting
api-1  | ERROR: could not connect to payments
api-1  | Traceback (most recent call last):
payments-1 | Process exited with code 137
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "api").mkdir(parents=True)
    (root / "k8s").mkdir()
    (root / "infra").mkdir()
    (root / "docker-compose.yml").write_text(COMPOSE)
    (root / "api" / "Dockerfile").write_text(
        'FROM python:3.12-slim\nEXPOSE 8000\nCMD ["python", "-m", "api"]\n'
    )
    (root / "k8s" / "api.yaml").write_text(K8S)
    (root / "infra" / "main.tf").write_text(TF)
    return root


def test_resources_and_links_from_every_kind_of_file(tmp_path: Path) -> None:
    out = scan([_project(tmp_path)])
    kinds = {r["kind"] for r in out["resources"]}
    assert {
        "compose-service",
        "dockerfile",
        "k8s-deployment",
        "k8s-service",
        "tf-resource",
    } <= kinds
    links = {(e["from"].split(":", 2)[2], e["to"].split(":", 2)[2], e["via"]) for e in out["edges"]}
    assert ("api", "db", "depends_on (service_healthy)") in links
    assert ("api", "api/Dockerfile", "build") in links
    assert ("Service/api-svc", "Deployment/api", "selector") in links
    assert ("aws_s3_bucket_policy.assets", "aws_s3_bucket.assets", "reference") in links
    warnings = " ".join(out["warnings"])
    assert "runs as root" in warnings and "has no liveness or readiness probe" in warnings
    assert "doesn't wait for it to be healthy" not in warnings  # this depends_on waits for health
    api = next(r for r in out["resources"] if r["name"] == "api" and r["kind"] == "compose-service")
    assert api["health"].startswith("CMD curl") and api["urls"] == ["payments"]


def test_links_across_projects_by_url(tmp_path: Path) -> None:
    other = tmp_path / "payments"
    other.mkdir()
    (other / "compose.yaml").write_text("services:\n  payments:\n    build: .\n")
    out = scan([_project(tmp_path), other])
    assert [(e["from"], e["to"]) for e in out["cross_project"]] == [
        ("shop:compose:api", "payments:compose:payments")
    ]


def test_a_log_is_triaged_by_service(tmp_path: Path) -> None:
    infra = scan([_project(tmp_path)])
    out = triage_log(infra, LOG)
    by = {s["service"]: s for s in out["services"]}
    assert out["first_failing"] == "api" and set(out["failing"]) == {"api", "payments"}
    assert by["api"]["code"] == "api" and by["api"]["depends_on"] == ["db"]
    assert {f["rule"] for f in by["api"]["findings"]} == {"error-line", "python-traceback"}
    assert not by["payments"]["declared"] and by["db"]["findings"] == []


def test_the_check_order_is_dry_runs_then_dependencies_first(tmp_path: Path) -> None:
    steps = validation_sequence(scan([_project(tmp_path)]))
    commands = [s["command"] for s in steps]
    assert commands[0] == "docker compose -f docker-compose.yml config --quiet"
    assert any(c.startswith("terraform -chdir=infra validate") for c in commands)
    assert any(c == "kubectl apply --dry-run=client -f k8s/api.yaml" for c in commands)
    assert commands.index("docker compose -f docker-compose.yml up -d db") < commands.index(
        "docker compose -f docker-compose.yml up -d api"
    )
    assert all(s["step"] in ("validate", "build", "start", "check") for s in steps)


def test_cli(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (tmp_path / "compose.log").write_text(LOG)
    res = CliRunner().invoke(
        app,
        [
            "triage",
            "infra",
            "--project",
            str(root),
            "--log",
            str(tmp_path / "compose.log"),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["log"]["first_failing"] == "api" and out["sequence"][0]["step"] == "validate"
    bad = CliRunner().invoke(app, ["triage", "infra", "--project", str(tmp_path / "nope")])
    assert bad.exit_code == 2

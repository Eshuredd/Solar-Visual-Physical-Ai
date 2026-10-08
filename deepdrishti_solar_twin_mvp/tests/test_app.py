from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "deepdrishti_solar_twin_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["SOLAR_TWIN_DB"] = str(TEST_DB)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402


def make_client() -> TestClient:
    init_db(reset=True)
    return TestClient(app)


def test_health_and_portfolio() -> None:
    with make_client() as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        portfolio = client.get("/api/portfolio")
        assert portfolio.status_code == 200
        payload = portfolio.json()
        assert payload["kpis"]["sites"] == 4
        assert payload["kpis"]["active_findings"] > 0
        assert len(payload["sites"]) == 4


def test_anomaly_to_task_to_resolution_workflow() -> None:
    with make_client() as client:
        anomaly = client.get("/api/anomalies/an-002")
        assert anomaly.status_code == 200
        assert anomaly.json()["asset_id"].startswith("SRP-")

        task_payload = {
            "site_id": "site-001",
            "anomaly_id": "an-002",
            "title": "Test critical string repair",
            "owner": "Aditi Rao",
            "due_date": "2026-08-15",
            "priority": "Critical",
            "requested_action": "Verify the string and restore continuity.",
            "notes": "Created by automated test.",
        }
        created = client.post("/api/tasks", json=task_payload)
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert created.json()["status"] == "Assigned"

        verified = client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "Verified", "notes": "Repair verified."},
        )
        assert verified.status_code == 200
        assert verified.json()["status"] == "Verified"

        updated_anomaly = client.get("/api/anomalies/an-002").json()
        assert updated_anomaly["status"] == "Resolved"
        assert any(task_id in event["event"] for event in updated_anomaly["history"])


def test_sample_thermal_analysis_creates_mapped_findings() -> None:
    with make_client() as client:
        response = client.post("/api/inspections/analyze", data={"site_id": "site-001"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["findings"] >= 1
        assert payload["affected_kw"] > 0
        assert payload["annual_revenue_loss"] > 0

        finding = client.get(f"/api/anomalies/{payload['anomaly_ids'][0]}")
        assert finding.status_code == 200
        item = finding.json()
        assert item["status"] == "Detected"
        assert item["asset_id"].startswith("SRP-")
        assert item["thermal_image"].endswith("sample_thermal_input.png")


def test_csv_export_and_frontend() -> None:
    with make_client() as client:
        export = client.get("/api/sites/site-001/export.csv")
        assert export.status_code == 200
        assert "asset_id" in export.text
        assert "String Anomaly" in export.text

        frontend = client.get("/")
        assert frontend.status_code == 200
        assert "DeepDrishti Solar Twin" in frontend.text

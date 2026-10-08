from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "deepdrishti_solar_twin_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["SOLAR_TWIN_DB"] = str(TEST_DB)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect, init_db  # noqa: E402
from app.inverter_monitoring import (  # noqa: E402
    classify_status,
    conversion_efficiency_pct,
    relative_yield_pct,
    validate_telemetry,
)
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


def test_synthetic_telemetry_generation_and_persistence() -> None:
    init_db(reset=True)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inverters WHERE site_id = 'site-001'").fetchone()[0] == 12
        assert conn.execute("SELECT COUNT(*) FROM inverter_telemetry").fetchone()[0] == 12 * 7 * 96 - 24
        row = dict(conn.execute(
            "SELECT * FROM inverter_telemetry WHERE inverter_id = 'site-001-INV-01' AND irradiance_w_m2 > 500 LIMIT 1"
        ).fetchone())
        assert row["data_source"] == "synthetic_demo_v1"
        assert row["dc_power_kw"] >= row["ac_power_kw"]
        assert abs(row["dc_power_kw"] * 1000 - row["dc_voltage_v"] * row["dc_current_a"]) < 2
        assert abs(row["ac_power_kw"] * 1000 - (3 ** 0.5) * row["ac_voltage_v"] * row["ac_current_a"] * 0.99) < 2


def test_inverter_apis_and_date_ranges() -> None:
    with make_client() as client:
        listing = client.get("/api/sites/site-001/inverters")
        assert listing.status_code == 200
        assert len(listing.json()) == 12
        assert all(item["data_source"] == "synthetic_demo" for item in listing.json())

        detail = client.get("/api/inverters/site-001-INV-07")
        assert detail.status_code == 200
        assert detail.json()["summary"]["status"] == "Critical"
        assert detail.json()["summary"]["data_classification"] == "synthetic_demo"

        telemetry = client.get(
            "/api/inverters/site-001-INV-01/telemetry?start=2026-08-07&end=2026-08-07"
        )
        assert telemetry.status_code == 200
        payload = telemetry.json()
        assert payload["count"] == 96
        assert "expected_ac_power_kw" in payload["readings"][48]
        assert "conversion_efficiency_pct" in payload["readings"][48]

        summary = client.get(
            "/api/inverters/site-001-INV-01/summary?start=2026-08-07&end=2026-08-08"
        )
        assert summary.status_code == 200
        assert summary.json()["actual_energy_kwh"] > 0
        assert summary.json()["expected_energy_kwh"] > 0
        assert client.get("/api/inverters/unknown").status_code == 404
        assert client.get("/api/inverters/site-001-INV-01/summary?start=bad-date").status_code == 400
        assert client.get("/api/inverters/site-001-INV-01/summary?start=2026-08-08&end=2026-08-07").status_code == 400


def test_efficiency_relative_yield_and_operating_guards() -> None:
    assert conversion_efficiency_pct(100, 96) == 96.0
    assert conversion_efficiency_pct(0, 0) is None
    daylight = [{
        "timestamp": "2026-08-07T12:00:00Z", "dc_power_kw": 940.0, "ac_power_kw": 900.0,
        "irradiance_w_m2": 1000.0, "inverter_temperature_c": 25.0, "operating_state": "Running",
    }]
    assert relative_yield_pct(daylight, 1000.0) == 90.0
    assert classify_status(90.0, daylight[0]) == "Healthy"

    excluded = [
        {"ac_power_kw": 0, "irradiance_w_m2": 0, "inverter_temperature_c": 20, "operating_state": "Night"},
        {"ac_power_kw": 0, "irradiance_w_m2": 40, "inverter_temperature_c": 22, "operating_state": "Startup"},
        {"ac_power_kw": 0, "irradiance_w_m2": 800, "inverter_temperature_c": 40, "operating_state": "Curtailment"},
    ]
    assert relative_yield_pct(excluded, 1000.0) is None
    assert classify_status(None, excluded[0]) == "Night"
    assert classify_status(None, excluded[2]) == "Curtailed"


def test_underperformance_missing_and_invalid_telemetry() -> None:
    with make_client() as client:
        underperformer = client.get("/api/inverters/site-001-INV-07/summary").json()
        intermittent = client.get("/api/inverters/site-001-INV-12/summary").json()
        normal = client.get("/api/inverters/site-001-INV-01/summary").json()
        assert underperformer["relative_yield_pct"] < 75
        assert any(item["code"] == "LOW_RELATIVE_YIELD" for item in underperformer["indicators"])
        assert any(item["code"] == "ABNORMAL_OPERATING_STATE" for item in intermittent["indicators"])
        assert normal["status"] == "Healthy"

    invalid = {
        "dc_power_kw": -1, "ac_power_kw": 100, "dc_voltage_v": 500, "dc_current_a": 1,
        "ac_voltage_v": 690, "ac_current_a": 1, "inverter_temperature_c": 30,
        "ambient_temperature_c": 20, "irradiance_w_m2": 1700,
    }
    errors = validate_telemetry(invalid)
    assert any("dc_power_kw" in error for error in errors)
    assert any("irradiance_w_m2" in error for error in errors)
    with connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO inverter_telemetry VALUES
               ('site-001-INV-01','2099-01-01T00:00:00Z',-1,0,0,0,0,0,20,20,0,'Night','test')"""
        )


def test_site_isolation_and_idempotent_initialization_without_data_loss() -> None:
    init_db(reset=True)
    with connect() as conn:
        conn.execute(
            """INSERT INTO sites VALUES
               ('site-test','Isolation Site','Test',1.0,'Healthy','2026-01-01',0,0,100,'Test Owner','2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO inverters VALUES
               ('site-test-INV-01','site-test','INV-01',1000,'Test','Model','Operational','T1','synthetic_demo')"""
        )
        conn.commit()
    init_db()
    with TestClient(app) as client:
        isolated = client.get("/api/sites/site-test/inverters")
        assert isolated.status_code == 200
        assert [item["inverter_id"] for item in isolated.json()] == ["site-test-INV-01"]
        assert client.get("/api/sites/site-001/inverters").json()[0]["site_id"] == "site-001"
    with connect() as conn:
        assert conn.execute("SELECT owner FROM sites WHERE id = 'site-test'").fetchone()[0] == "Test Owner"
        assert conn.execute("SELECT COUNT(*) FROM inverter_telemetry").fetchone()[0] == 12 * 7 * 96 - 24


def test_existing_application_views_and_site_apis_still_respond() -> None:
    with make_client() as client:
        for path in [
            "/api/sites", "/api/sites/site-001", "/api/sites/site-001/anomalies",
            "/api/sites/site-001/inspections", "/api/sites/site-001/assets",
            "/api/tasks?site_id=site-001", "/site", "/twin", "/inspections",
            "/tasks", "/assets",
        ]:
            response = client.get(path)
            assert response.status_code == 200, path


def test_utc_range_normalization_and_zero_is_not_missing() -> None:
    with make_client() as client:
        utc = client.get(
            "/api/inverters/site-001-INV-01/telemetry?start=2026-08-07T09:00:00Z&end=2026-08-07T13:00:00Z"
        ).json()
        offset = client.get(
            "/api/inverters/site-001-INV-01/telemetry?start=2026-08-07T14:30:00%2B05:30&end=2026-08-07T18:30:00%2B05:30"
        ).json()
        assert utc["count"] == offset["count"] == 17
        assert utc["readings"][0]["timestamp"] == offset["readings"][0]["timestamp"]
        night = client.get(
            "/api/inverters/site-001-INV-01/telemetry?start=2026-08-07T00:00:00Z&end=2026-08-07T01:00:00Z"
        ).json()["readings"]
        assert len(night) == 5
        assert all(row["ac_power_kw"] == 0 and row["operating_state"] == "Night" for row in night)


@pytest.mark.parametrize(
    ("number", "category"),
    [
        (2, "pv_side_underperformance"),
        (3, "ac_conversion_underperformance"),
        (4, "inverter_overheating"),
        (5, "unexpected_shutdown"),
        (7, "pv_side_underperformance"),
        (11, "pv_side_underperformance"),
        (12, "intermittent_derating"),
    ],
)
def test_each_equipment_anomaly_type(number: int, category: str) -> None:
    with make_client() as client:
        inverter_id = f"site-001-INV-{number:02d}"
        result = client.post(f"/api/inverters/{inverter_id}/analyze")
        assert result.status_code == 200
        alerts = client.get(f"/api/inverters/{inverter_id}/alerts").json()
        assert category in {item["anomaly_category"] for item in alerts}
        assert all(item["diagnosis_status"] == "unconfirmed" for item in alerts)


def test_normal_night_startup_and_curtailment_do_not_raise_equipment_alerts() -> None:
    with make_client() as client:
        for number in (1, 6):
            inverter_id = f"site-001-INV-{number:02d}"
            client.post(f"/api/inverters/{inverter_id}/analyze")
            alerts = client.get(f"/api/inverters/{inverter_id}/alerts").json()
            assert not [item for item in alerts if item["anomaly_category"] != "data_quality"]
        # A night-only analysis has genuine zero readings but no alert.
        night = client.post(
            "/api/inverters/site-001-INV-01/analyze?start=2026-08-07T00:00:00Z&end=2026-08-07T05:00:00Z"
        ).json()
        assert night["events_detected"] == 0


def test_missing_invalid_and_stale_data_are_separate_quality_alerts() -> None:
    with make_client() as client:
        expected = {8: "missing_timestamps", 9: "invalid_measurement", 10: "stale_telemetry"}
        for number, subtype in expected.items():
            inverter_id = f"site-001-INV-{number:02d}"
            client.post(f"/api/inverters/{inverter_id}/analyze")
            alerts = client.get(f"/api/inverters/{inverter_id}/alerts").json()
            quality = [item for item in alerts if item["anomaly_category"] == "data_quality"]
            assert subtype in {item["anomaly_subtype"] for item in quality}
            assert not [item for item in alerts if item["anomaly_category"] != "data_quality"]


def test_event_boundaries_deduplication_and_filters() -> None:
    with make_client() as client:
        inverter_id = "site-001-INV-03"
        first = client.post(f"/api/inverters/{inverter_id}/analyze").json()
        second = client.post(f"/api/inverters/{inverter_id}/analyze").json()
        assert first["alert_ids"] == second["alert_ids"]
        alerts = client.get(f"/api/inverters/{inverter_id}/alerts").json()
        assert len(alerts) == 1
        assert alerts[0]["start_timestamp"] == "2026-08-08T10:00:00Z"
        assert alerts[0]["end_timestamp"] == "2026-08-08T14:00:00Z"
        filtered = client.get(
            "/api/sites/site-001/inverter-alerts?severity=Critical&anomaly_type=ac_conversion_underperformance&start=2026-08-08&end=2026-08-08"
        )
        assert filtered.status_code == 200
        assert len(filtered.json()) == 1
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM inverter_alerts WHERE inverter_id=?", (inverter_id,)).fetchone()[0] == 1


def test_alert_lifecycle_history_recovery_and_rerun_do_not_auto_resolve() -> None:
    with make_client() as client:
        inverter_id = "site-001-INV-11"
        result = client.post(f"/api/inverters/{inverter_id}/analyze").json()
        alert_id = result["alert_ids"][0]
        investigated = client.patch(
            f"/api/inverter-alerts/{alert_id}",
            json={"lifecycle_status": "Investigating", "note": "Reviewing recovered synthetic event.", "actor": "Test operator"},
        )
        assert investigated.status_code == 200
        assert investigated.json()["lifecycle_status"] == "Investigating"
        assert len(investigated.json()["lifecycle_history"]) == 2
        init_db()
        assert client.get(f"/api/inverter-alerts/{alert_id}").json()["lifecycle_status"] == "Investigating"
        client.post(f"/api/inverters/{inverter_id}/analyze")
        assert client.get(f"/api/inverter-alerts/{alert_id}").json()["lifecycle_status"] == "Investigating"
        resolved = client.patch(f"/api/inverter-alerts/{alert_id}", json={"lifecycle_status": "Resolved"})
        assert resolved.json()["lifecycle_status"] == "Resolved"
        client.post(f"/api/inverters/{inverter_id}/analyze")
        assert client.get(f"/api/inverter-alerts/{alert_id}").json()["lifecycle_status"] == "Resolved"


def test_synthetic_event_evaluation_is_reproducible_and_explicitly_limited() -> None:
    with make_client() as client:
        first = client.get("/api/inverter-alerts/evaluation").json()
        second = client.get("/api/inverter-alerts/evaluation").json()
        assert first == second
        assert first["event_level_precision"] == 1.0
        assert first["event_level_recall"] == 1.0
        assert first["false_positive_rate"] == 0.0
        assert first["duplicate_alert_count"] == 0
        assert first["normal_operation_passed"] is True
        assert "synthetic" in first["limitations"].lower()

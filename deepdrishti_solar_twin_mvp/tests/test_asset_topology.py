from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import connect, init_db


def make_client() -> TestClient:
    init_db(reset=True)
    return TestClient(main.app)


def _alert_id(client: TestClient, inverter_id: str = "site-001-INV-03") -> str:
    analyzed = client.post(f"/api/inverters/{inverter_id}/analyze").json()
    assert analyzed["alert_ids"]
    return analyzed["alert_ids"][0]


def test_topology_seed_is_idempotent_and_migrates_missing_tables() -> None:
    init_db(reset=True)
    with connect() as conn:
        original = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("site_blocks", "inverter_mppts", "pv_strings", "physical_asset_groups", "string_asset_groups")
        }
        conn.executescript("""
            DROP TABLE alert_finding_association_events;
            DROP TABLE alert_finding_associations;
            DROP TABLE string_asset_groups;
            DROP TABLE physical_asset_groups;
            DROP TABLE pv_strings;
            DROP TABLE inverter_mppts;
            DROP TABLE site_blocks;
        """)
        conn.commit()
    # Simulates opening a Phase 2B database that predates all Phase 3A tables.
    init_db()
    with connect() as conn:
        repeated = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in original
        }
    assert repeated == original == {
        "site_blocks": 4, "inverter_mppts": 84, "pv_strings": 168,
        "physical_asset_groups": 168, "string_asset_groups": 168,
    }


def test_asset_counts_are_database_derived_and_unknown_is_explicit() -> None:
    with make_client() as client:
        mapped = client.get("/api/sites/site-001/assets").json()
        assert mapped["hierarchy"] == {
            "site": "Sunridge Solar Park", "inverters": 12, "blocks": 4,
            "mppts": 84, "strings": 168, "rows": 72, "modules": 2304,
        }
        assert mapped["topology_coverage"]["mapping_classification"] == "synthetic_demo"
        assert mapped["topology_coverage"]["is_verified_as_built"] is False

        unknown = client.get("/api/sites/site-002/assets").json()
        assert unknown["hierarchy"]["blocks"] is None
        assert unknown["hierarchy"]["strings"] is None
        assert unknown["topology_coverage"]["status"] == "unavailable"


def test_inverter_topology_is_site_scoped_and_supports_variable_inputs() -> None:
    with make_client() as client:
        first = client.get("/api/inverters/site-001-INV-01/topology").json()
        second = client.get("/api/inverters/site-001-INV-02/topology").json()
        assert first["blocks"] == ["B1"]
        assert first["mppt_count"] == 6 and first["string_count"] == 12
        assert second["mppt_count"] == 7 and second["string_count"] == 14
        assert first["mapped_module_count"] == second["mapped_module_count"] == 192
        assert all(item["mapping_classification"] == "synthetic_demo" for item in first["mppts"])

    with connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="site mismatch"):
            conn.execute(
                """INSERT INTO inverter_mppts VALUES
                   ('bad-mppt','site-002','site-001-INV-01',99,'Bad','unverified','test')"""
            )


def test_evidence_correlation_is_explicit_and_does_not_claim_causation() -> None:
    with make_client() as client:
        alert_id = _alert_id(client)
        response = client.get(f"/api/inverter-alerts/{alert_id}/evidence")
        assert response.status_code == 200
        payload = response.json()
        assert payload["topology"]["blocks"] == ["B1"]
        assert payload["electrical_evidence"]["diagnosis_status"] == "unconfirmed"
        assert payload["experimental_ml_used_for_correlation"] is False
        assert "neither proves" in payload["correlation_notice"]
        assert "verified customer as-built" in payload["missing_confirmation_information"][0]
        assert payload["related_visual_findings"]
        assert all(item["relationship_status"] == "simulated_not_verified" for item in payload["related_visual_findings"])


def test_reviewed_association_workflow_preserves_audit_history() -> None:
    with make_client() as client:
        alert_id = _alert_id(client)
        finding_id = client.get(f"/api/inverter-alerts/{alert_id}/evidence").json()["related_visual_findings"][0]["id"]
        proposed = client.post(
            f"/api/inverter-alerts/{alert_id}/associations",
            json={"anomaly_id": finding_id, "reviewer": "Aditi Rao", "explanation": "Review electrical and thermal timing together."},
        )
        assert proposed.status_code == 200
        association = proposed.json()
        assert association["review_status"] == "Proposed"
        accepted = client.patch(
            f"/api/inverter-alert-associations/{association['association_id']}",
            json={"review_status": "Accepted", "reviewer": "Vikram Shah", "explanation": "Accepted as related evidence, not confirmed root cause."},
        ).json()
        assert accepted["review_status"] == "Accepted"
        removed = client.patch(
            f"/api/inverter-alert-associations/{association['association_id']}",
            json={"review_status": "Removed", "reviewer": "Vikram Shah", "explanation": "Later field evidence did not support the relationship."},
        ).json()
        assert removed["review_status"] == "Removed"
        assert [event["review_status"] for event in removed["history"]] == ["Proposed", "Accepted", "Removed"]


def test_cross_site_association_is_rejected_and_alert_workflow_is_preserved() -> None:
    with make_client() as client:
        alert_id = _alert_id(client)
        with connect() as conn:
            conn.execute("UPDATE anomalies SET site_id='site-002' WHERE id='an-001'")
            conn.commit()
        rejected = client.post(
            f"/api/inverter-alerts/{alert_id}/associations",
            json={"anomaly_id": "an-001", "reviewer": "Aditi Rao", "explanation": "This should never cross tenant scope."},
        )
        assert rejected.status_code == 400
        alert = client.get(f"/api/inverter-alerts/{alert_id}").json()
        assert alert["lifecycle_status"] == "Open"
        assert alert["detection_method"].startswith("inverter-rules-")

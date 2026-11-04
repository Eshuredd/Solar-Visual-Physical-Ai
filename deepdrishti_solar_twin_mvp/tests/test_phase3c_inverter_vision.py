from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import inverter_vision, main
from app.db import connect, init_db
from app.inverter_vision import VisionValidationError, analyze_rgb_bytes, analyze_thermal_bytes


GUARD = {"X-DeepDrishti-Demo-Operator": "acknowledged"}


def _png(size=(100, 80), change=False, image_format="PNG") -> bytes:
    image = Image.new("RGB", size, (80, 100, 120))
    if change:
        ImageDraw.Draw(image).rectangle([20, 30, 45, 52], fill=(235, 220, 35))
    output = io.BytesIO(); image.save(output, image_format)
    return output.getvalue()


def _matrix(values: list[list[object]]) -> bytes:
    output = io.StringIO(); csv.writer(output).writerows(values)
    return output.getvalue().encode()


def test_rgb_change_localizes_unclassified_candidate_and_annotation() -> None:
    result, evidence = analyze_rgb_bytes(_png(), _png(change=True), reference_name="before.png", current_name="after.png")
    assert result["status"] == "candidate_regions" and result["candidate_count"] == 1
    candidate = result["candidate_regions"][0]
    x0, y0, x1, y1 = candidate["bbox_xyxy"]
    assert x0 <= 21 and x1 >= 45 and y0 <= 31 and y1 >= 52
    assert candidate["candidate_type"] == "visual_change_unclassified"
    assert 0 < candidate["relative_change_score"] <= 1
    assert Image.open(io.BytesIO(evidence["annotated.png"])).size == (100, 80)


def test_identical_rgb_pair_has_no_forced_candidate() -> None:
    image = _png()
    result, _ = analyze_rgb_bytes(image, image, reference_name="same.png", current_name="same.png")
    assert result["status"] == "no_change_detected"
    assert result["candidate_regions"] == []


def test_rgb_rejects_dimensions_formats_and_oversized_payload() -> None:
    with pytest.raises(VisionValidationError, match="identical dimensions"):
        analyze_rgb_bytes(_png(), _png(size=(120, 80)), reference_name="a.png", current_name="b.png")
    with pytest.raises(VisionValidationError, match="Only PNG"):
        analyze_rgb_bytes(_png(image_format="BMP"), _png(image_format="BMP"), reference_name="a.bmp", current_name="b.bmp")
    with pytest.raises(VisionValidationError, match="8 MB"):
        analyze_rgb_bytes(b"x" * (inverter_vision.MAX_IMAGE_BYTES + 1), _png(), reference_name="large.png", current_name="b.png")


def test_thermal_change_and_healthy_matrix_results_are_numeric() -> None:
    reference = [[30.0] * 20 for _ in range(16)]
    current = [[30.0] * 20 for _ in range(16)]
    for y in range(4, 9):
        for x in range(7, 12): current[y][x] = 44.0
    result, evidence = analyze_thermal_bytes(_matrix(reference), _matrix(current), reference_name="a.csv", current_name="b.csv", delta_threshold_c=10)
    assert result["candidate_regions"][0]["bbox_xyxy"] == [7, 4, 12, 9]
    assert result["candidate_regions"][0]["peak_temperature_rise_c"] == 14
    assert Image.open(io.BytesIO(evidence["difference_heatmap.png"])).width > 20
    healthy, _ = analyze_thermal_bytes(_matrix(reference), _matrix(reference), reference_name="a.csv", current_name="a.csv")
    assert healthy["status"] == "no_change_detected" and healthy["candidate_count"] == 0


def test_thermal_validation_and_missing_measurements() -> None:
    with pytest.raises(VisionValidationError, match="not numeric"):
        analyze_thermal_bytes(b"20,bad\n20,20", b"20,20\n20,20", reference_name="a.csv", current_name="b.csv")
    with pytest.raises(VisionValidationError, match="identical registered dimensions"):
        analyze_thermal_bytes(b"20,20\n20,20", b"20,20,20\n20,20,20", reference_name="a.csv", current_name="b.csv")
    result, _ = analyze_thermal_bytes(b"20,\n20,20", b"20,\n20,35", reference_name="a.csv", current_name="b.csv", min_region_pixels=1)
    assert result["missing_measurements"] == {"reference": 1, "current": 1, "comparable_cells": 3}


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(inverter_vision, "PRIVATE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(inverter_vision, "PREVIEW_ROOT", tmp_path / "evidence" / "previews")
    monkeypatch.setattr(inverter_vision, "SAVED_ROOT", tmp_path / "evidence" / "saved")
    init_db(reset=True)
    return TestClient(main.app)


def test_preview_endpoints_guard_missing_inputs_unknown_inverter_and_demo_results(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    route = "/api/inverters/site-001-INV-01/visual-inspections/rgb/preview"
    assert client.post(route, data={"demo_case":"rgb_change","demo_mode":"true"}).status_code == 403
    assert client.post(route, data={"demo_mode":"true"}, headers=GUARD).status_code == 400
    assert client.post("/api/inverters/unknown/visual-inspections/rgb/preview", data={"demo_case":"rgb_change","demo_mode":"true"}, headers=GUARD).status_code == 404
    changed = client.post(route, data={"demo_case":"rgb_change","demo_mode":"true"}, headers=GUARD)
    assert changed.status_code == 200 and changed.json()["result"]["candidate_count"] >= 1
    healthy = client.post(route, data={"demo_case":"healthy_unchanged","demo_mode":"true"}, headers=GUARD).json()
    assert healthy["result"]["status"] == "no_change_detected" and healthy["result"]["candidate_count"] == 0
    thermal = client.post("/api/inverters/site-001-INV-01/visual-inspections/thermal/preview",
        data={"demo_case":"thermal_rise","demo_mode":"true","delta_threshold_c":"10"}, headers=GUARD).json()
    assert thermal["result"]["candidate_count"] >= 1
    assert thermal["result"]["candidate_regions"][0]["peak_temperature_rise_c"] == 15


def test_upload_preview_save_review_history_telemetry_and_site_isolation(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/inverters/site-001-INV-01/visual-inspections/rgb/preview",
        files={"reference":("before.png",_png(),"image/png"),"current":("after.png",_png(change=True),"image/png")},
        data={"demo_mode":"true","dataset_id":"dataset-site-001-synthetic"}, headers=GUARD,
    )
    assert response.status_code == 200
    preview = response.json()
    cross_site = client.post("/api/inverters/site-001-INV-02/visual-inspections?demo_mode=true",
        json={"preview_token":preview["preview_token"]}, headers=GUARD)
    assert cross_site.status_code == 400
    saved = client.post("/api/inverters/site-001-INV-01/visual-inspections?demo_mode=true",
        json={"preview_token":preview["preview_token"]}, headers=GUARD)
    assert saved.status_code == 200 and saved.json()["candidate_count"] == 1
    inspection = saved.json(); candidate = inspection["candidates"][0]
    reviewed = client.patch(f"/api/inverter-visual-inspections/{inspection['inspection_id']}/review?demo_mode=true",
        json={"candidate_id":candidate["candidate_id"],"review_state":"Accepted as Relevant Evidence",
              "reviewer_label":"Demo reviewer (self-asserted)","explanation":"Keep as supporting evidence for field investigation."}, headers=GUARD)
    assert reviewed.status_code == 200
    candidate = reviewed.json()["candidates"][0]
    assert candidate["review_state"] == "Accepted as Relevant Evidence"
    assert [item["review_state"] for item in candidate["review_history"]] == ["Pending Review","Accepted as Relevant Evidence"]
    detail = client.get(f"/api/inverter-visual-inspections/{inspection['inspection_id']}?dataset_id=dataset-site-001-synthetic").json()
    assert detail["telemetry_evidence"]["dataset"]["dataset_type"] == "synthetic_demo"
    assert "does not prove" in detail["telemetry_evidence"]["correlation_notice"]
    assert client.get(f"/api/inverters/site-001-INV-01/visual-inspections").json()[0]["candidate_count"] == 1
    # Saving the same preview is idempotent.
    repeated = client.post("/api/inverters/site-001-INV-01/visual-inspections?demo_mode=true",
        json={"preview_token":preview["preview_token"]}, headers=GUARD).json()
    assert repeated["inspection_id"] == inspection["inspection_id"]
    assert client.post("/api/reset?demo_mode=true").status_code == 409


def test_visual_schema_migration_is_idempotent_and_cross_site_triggered() -> None:
    init_db(reset=True); init_db(); init_db()
    with connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"inverter_visual_inspections","inverter_visual_candidates","inverter_visual_candidate_review_events"} <= tables
        with pytest.raises(sqlite3.IntegrityError, match="site mismatch"):
            conn.execute("""INSERT INTO inverter_visual_inspections
              VALUES ('bad','site-002','site-001-INV-01','rgb','2026-01-01T00:00:00Z','a','b','x','v','{}','test','2026-01-01T00:00:00Z','Pending Review',NULL,'{}',0,'token-bad')""")

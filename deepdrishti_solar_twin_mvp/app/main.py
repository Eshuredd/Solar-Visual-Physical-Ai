from __future__ import annotations

import csv
from contextlib import asynccontextmanager
import io
import json
import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .asset_topology import association_records, correlated_findings, inverter_topology, site_topology_summary
from .db import DB_PATH, connect, init_db, rows_to_dicts
from .inverter_anomaly import CATEGORIES, METHOD_VERSION, detect_anomaly_events, evaluate_synthetic_events
from .inverter_evaluation import generate_held_out_cases
from .inverter_ml import DEFAULT_ARTIFACT_ROOT, MODEL_VERSION, candidate_events, load_artifact
from .inverter_ml_baselines import BASELINE_VERSION
from .inverter_ml_features import FEATURE_VERSION, build_feature_rows, build_peer_context
from .inverter_monitoring import (
    canonical_utc_timestamp,
    conversion_efficiency_pct,
    expected_ac_power_kw,
    parse_iso_datetime,
    summarize_inverter,
)
from .inverter_vision import (
    METHOD_VERSION as INVERTER_VISION_VERSION,
    VisionValidationError,
    analyze_rgb_bytes,
    analyze_thermal_bytes,
    create_preview,
    ensure_storage as ensure_inverter_vision_storage,
    persist_preview,
    preview_evidence_path,
    read_preview,
    saved_evidence_path,
)
from .scada_import import ImportValidationError, MAX_UPLOAD_BYTES, commit_scada_import, preview_scada_csv
from .topology_import import commit_topology_import, preview_topology_csv
from .vision import analyze_thermal_image

APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
UPLOAD_ROOT = STATIC_ROOT / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
ML_ARTIFACT_PATH = DEFAULT_ARTIFACT_ROOT / "inverter_anomaly.joblib"
ML_EVALUATION_PATH = DEFAULT_ARTIFACT_ROOT / "evaluation.json"
INVERTER_VISION_DEMO_ROOT = STATIC_ROOT / "samples" / "inverter_visual"

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_inverter_vision_storage()
    yield


app = FastAPI(
    title="DeepDrishti Solar Twin API",
    version="0.5.0",
    description="Runnable equipment-level solar Digital Twin MVP.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


class AnomalyPatch(BaseModel):
    status: Literal["Detected", "Verified", "Assigned", "In Progress", "Resolved"] | None = None
    priority: Literal["Critical", "High", "Medium", "Low"] | None = None
    notes: str | None = Field(default=None, max_length=3000)


class TaskCreate(BaseModel):
    site_id: str
    anomaly_id: str | None = None
    title: str = Field(min_length=3, max_length=200)
    owner: str = Field(min_length=2, max_length=120)
    due_date: date
    priority: Literal["Critical", "High", "Medium", "Low"] = "High"
    requested_action: str = Field(min_length=3, max_length=2000)
    notes: str = Field(default="", max_length=3000)


class TaskPatch(BaseModel):
    status: Literal["Assigned", "In Progress", "Overdue", "Completed", "Verified"] | None = None
    owner: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=3000)


class InverterAlertPatch(BaseModel):
    lifecycle_status: Literal["Open", "Acknowledged", "Investigating", "Resolved"]
    note: str = Field(default="", max_length=2000)
    actor: str = Field(default="DeepDrishti operator", min_length=2, max_length=120)


class AlertFindingAssociationCreate(BaseModel):
    anomaly_id: str
    reviewer: str = Field(min_length=2, max_length=120)
    explanation: str = Field(min_length=5, max_length=3000)


class AlertFindingAssociationPatch(BaseModel):
    review_status: Literal["Accepted", "Rejected", "Removed"]
    reviewer: str = Field(min_length=2, max_length=120)
    explanation: str = Field(min_length=5, max_length=3000)


class InverterVisualInspectionSave(BaseModel):
    preview_token: str = Field(min_length=16, max_length=120)
    capture_timestamp: datetime | None = None


class InverterVisualReview(BaseModel):
    candidate_id: str
    review_state: Literal["Accepted as Relevant Evidence", "Rejected", "Needs Further Investigation"]
    reviewer_label: str = Field(min_length=2, max_length=120)
    explanation: str = Field(min_length=5, max_length=3000)


def get_site_or_404(site_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return dict(row)


def get_inverter_or_404(inverter_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inverters WHERE inverter_id = ?", (inverter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Inverter not found")
    return dict(row)


def _telemetry_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    try:
        start_dt = parse_iso_datetime(start)
        end_dt = parse_iso_datetime(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use ISO 8601 timestamps for start and end") from exc
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start must be before end")
    normalized_start = canonical_utc_timestamp(start)
    normalized_end = canonical_utc_timestamp(end, end_of_day=True)
    return normalized_start, normalized_end


def _dataset_for_inverter(inverter: dict[str, Any], dataset_id: str | None) -> dict[str, Any]:
    if not dataset_id or dataset_id in {"demo-synthetic-site-001", "dataset-site-001-synthetic"}:
        return {"dataset_id": "dataset-site-001-synthetic", "site_id": inverter["site_id"], "name": "Bundled synthetic demonstration",
                "dataset_type": "synthetic_demo", "source_system": "DeepDrishti deterministic seed",
                "provenance": "synthetic_demo", "timezone": "UTC"}
    with connect() as conn:
        row = conn.execute("SELECT * FROM telemetry_datasets WHERE dataset_id=? AND is_selectable=1", (dataset_id,)).fetchone()
    if not row or row["site_id"] != inverter["site_id"]:
        raise HTTPException(status_code=404, detail="Telemetry dataset not found for this site")
    return dict(row)


def _read_inverter_telemetry(inverter_id: str, start: str | None = None, end: str | None = None, dataset_id: str | None = None) -> list[dict[str, Any]]:
    inverter = get_inverter_or_404(inverter_id)
    dataset = _dataset_for_inverter(inverter, dataset_id)
    start, end = _telemetry_range(start, end)
    where = ["inverter_id = ?"]
    params: list[Any] = [inverter_id]
    if start:
        where.append("timestamp >= ?")
        params.append(start)
    if end:
        where.append("timestamp <= ?")
        params.append(end)
    with connect() as conn:
        if dataset["dataset_type"] == "synthetic_demo":
            return rows_to_dicts(conn.execute(
                "SELECT * FROM inverter_telemetry WHERE " + " AND ".join(where) + " ORDER BY timestamp", params,
            ))
        where = [item.replace("timestamp", "timestamp_utc") for item in where]
        return rows_to_dicts(conn.execute(
            """SELECT dataset_id,site_id,inverter_id,timestamp_utc AS timestamp,ac_power_kw,dc_power_kw,
                      ac_voltage_v,ac_current_a,dc_voltage_v,dc_current_a,inverter_temperature_c,
                      ambient_temperature_c,irradiance_w_m2,operating_state,grid_state,source_system,
                      source_record_id,data_provenance,data_quality_status,import_id
               FROM imported_inverter_telemetry WHERE dataset_id=? AND """ + " AND ".join(where) + " ORDER BY timestamp_utc",
            [dataset["dataset_id"], *params],
        ))


def _load_ml_artifact() -> tuple[dict[str, Any] | None, str | None]:
    if not ML_ARTIFACT_PATH.exists():
        return None, "Experimental ML artifact is not installed; deterministic monitoring remains available."
    try:
        return load_artifact(ML_ARTIFACT_PATH), None
    except (OSError, ValueError, TypeError) as exc:
        return None, f"Experimental ML artifact is unavailable: {exc}"


def _ml_feature_rows(inverter: dict[str, Any], start: str | None, end: str | None) -> list[dict[str, Any]]:
    with connect() as conn:
        peers = rows_to_dicts(conn.execute(
            "SELECT * FROM inverters WHERE site_id=? ORDER BY inverter_id", (inverter["site_id"],)
        ))
    equipment = [(peer, _read_inverter_telemetry(peer["inverter_id"], start, end)) for peer in peers]
    peer_context = build_peer_context(equipment)
    selected = next(readings for peer, readings in equipment if peer["inverter_id"] == inverter["inverter_id"])
    return build_feature_rows(inverter, selected, peer_context)


def _inverter_summary_payload(inverter: dict[str, Any], start: str | None = None, end: str | None = None) -> dict[str, Any]:
    readings = _read_inverter_telemetry(inverter["inverter_id"], start, end)
    return summarize_inverter(inverter, readings)


def _active_filter_sql(status_column: str = "status") -> str:
    return f"{status_column} != 'Resolved'"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "DeepDrishti Solar Twin",
        "version": app.version,
        "database": str(DB_PATH),
        "time": datetime.now(timezone.utc).isoformat() + "Z",
    }


@app.get("/api/portfolio")
def portfolio() -> dict[str, Any]:
    with connect() as conn:
        sites = rows_to_dicts(conn.execute("SELECT * FROM sites ORDER BY capacity_mw DESC"))
        totals = dict(
            conn.execute(
                """
                SELECT
                    COALESCE(SUM(affected_kw), 0) AS affected_kw,
                    COALESCE(SUM(annual_kwh_loss), 0) AS annual_kwh_loss,
                    COALESCE(SUM(annual_revenue_loss), 0) AS annual_revenue_loss,
                    COUNT(*) AS active_findings
                FROM anomalies WHERE status != 'Resolved'
                """
            ).fetchone()
        )
        loss_by_type = rows_to_dicts(
            conn.execute(
                """
                SELECT anomaly_type AS label,
                       ROUND(SUM(annual_revenue_loss), 0) AS value,
                       COUNT(*) AS count
                FROM anomalies
                WHERE status != 'Resolved'
                GROUP BY anomaly_type
                ORDER BY value DESC
                LIMIT 7
                """
            )
        )
        overdue = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status IN ('Overdue') OR (due_date < ? AND status NOT IN ('Completed','Verified'))",
            (date.today().isoformat(),),
        ).fetchone()["c"]
        for site in sites:
            summary = dict(
                conn.execute(
                    """
                    SELECT COUNT(*) AS findings,
                           COALESCE(SUM(affected_kw),0) AS affected_kw,
                           COALESCE(SUM(annual_revenue_loss),0) AS annual_revenue_loss
                    FROM anomalies WHERE site_id = ? AND status != 'Resolved'
                    """,
                    (site["id"],),
                ).fetchone()
            )
            site.update(summary)
        capacity = sum(float(site["capacity_mw"]) for site in sites)
    return {
        "kpis": {
            "capacity_mw": round(capacity, 1),
            "sites": len(sites),
            "affected_kw": round(float(totals["affected_kw"]), 1),
            "power_loss_pct": round(float(totals["affected_kw"]) / max(1, capacity * 1000) * 100, 3),
            "annual_kwh_loss": round(float(totals["annual_kwh_loss"]), 0),
            "annual_revenue_loss": round(float(totals["annual_revenue_loss"]), 0),
            "active_findings": int(totals["active_findings"]),
            "overdue_tasks": int(overdue),
        },
        "loss_by_type": loss_by_type,
        "sites": sites,
        "trend": [
            {"month": "Mar", "loss": 274000, "recovered": 62000},
            {"month": "Apr", "loss": 248000, "recovered": 87000},
            {"month": "May", "loss": 229000, "recovered": 94000},
            {"month": "Jun", "loss": 201000, "recovered": 118000},
            {"month": "Jul", "loss": 188000, "recovered": 131000},
            {"month": "Aug", "loss": round(float(totals["annual_revenue_loss"]), 0), "recovered": 147000},
        ],
        "activity": [
            {"time": "12 min", "title": "Thermal finding verified", "detail": "String anomaly linked to SRP-B1-R10-M21"},
            {"time": "48 min", "title": "Field task updated", "detail": "Aditi Rao started work on task-001"},
            {"time": "2 hr", "title": "Inspection uploaded", "detail": "Thermal Health Survey — August"},
            {"time": "Yesterday", "title": "Tracker repair verified", "detail": "C2 row returned to expected angle"},
        ],
    }


@app.get("/api/sites")
def sites() -> list[dict[str, Any]]:
    with connect() as conn:
        result = rows_to_dicts(conn.execute("SELECT * FROM sites ORDER BY name"))
    return result


@app.get("/api/sites/{site_id}")
def site_detail(site_id: str) -> dict[str, Any]:
    site = get_site_or_404(site_id)
    with connect() as conn:
        summary = dict(
            conn.execute(
                """
                SELECT COUNT(*) AS findings,
                       COALESCE(SUM(affected_kw),0) AS affected_kw,
                       COALESCE(SUM(annual_kwh_loss),0) AS annual_kwh_loss,
                       COALESCE(SUM(annual_revenue_loss),0) AS annual_revenue_loss
                FROM anomalies WHERE site_id = ? AND status != 'Resolved'
                """,
                (site_id,),
            ).fetchone()
        )
        task_counts = rows_to_dicts(
            conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks WHERE site_id = ? GROUP BY status",
                (site_id,),
            )
        )
    site.update(summary)
    site["task_counts"] = task_counts
    site["inverters"] = list_site_inverters(site_id)
    return site


@app.get("/api/sites/{site_id}/inverters")
def list_site_inverters(site_id: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    with connect() as conn:
        inverters = rows_to_dicts(conn.execute(
            "SELECT * FROM inverters WHERE site_id = ? ORDER BY name", (site_id,)
        ))
    result: list[dict[str, Any]] = []
    for inverter in inverters:
        summary = _inverter_summary_payload(inverter, start, end)
        result.append({
            **inverter,
            "id": inverter["name"],
            "yield": summary["relative_yield_pct"],
            "status": summary["status"],
            "latest": summary["latest"],
            "actual_energy_kwh": summary["actual_energy_kwh"],
            "expected_energy_kwh": summary["expected_energy_kwh"],
            "indicator_count": len(summary["indicators"]),
        })
    return result


@app.get("/api/inverters/{inverter_id}")
def inverter_detail(inverter_id: str) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    return {**inverter, "summary": _inverter_summary_payload(inverter)}


@app.get("/api/inverters/{inverter_id}/topology")
def inverter_topology_detail(inverter_id: str) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    with connect() as conn:
        return inverter_topology(conn, inverter)


@app.get("/api/inverters/{inverter_id}/telemetry")
def inverter_telemetry(inverter_id: str, start: str | None = None, end: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    dataset = _dataset_for_inverter(inverter, dataset_id)
    readings = _read_inverter_telemetry(inverter_id, start, end, dataset_id)
    rated = float(inverter["rated_ac_power_kw"])
    for reading in readings:
        reading["expected_ac_power_kw"] = expected_ac_power_kw(
            rated, reading.get("irradiance_w_m2"), reading.get("inverter_temperature_c")
        )
        reading["conversion_efficiency_pct"] = conversion_efficiency_pct(
            reading.get("dc_power_kw"), reading.get("ac_power_kw")
        )
    return {
        "inverter_id": inverter_id,
        "start": readings[0]["timestamp"] if readings else None,
        "end": readings[-1]["timestamp"] if readings else None,
        "count": len(readings),
        "dataset": dataset,
        "data_classification": dataset["dataset_type"],
        "threshold_calibration_status": "uncalibrated_for_imported_site" if dataset["dataset_type"] == "imported_scada" else "synthetic_demo_thresholds",
        "quality": {"partial_records": sum(r.get("data_quality_status") == "partial" for r in readings)},
        "readings": readings,
    }


@app.get("/api/inverters/{inverter_id}/summary")
def inverter_summary(inverter_id: str, start: str | None = None, end: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    dataset = _dataset_for_inverter(inverter, dataset_id)
    payload = summarize_inverter(inverter, _read_inverter_telemetry(inverter_id, start, end, dataset_id))
    payload["data_classification"] = dataset["dataset_type"]
    payload["dataset"] = dataset
    payload["threshold_calibration_status"] = "uncalibrated_for_imported_site" if dataset["dataset_type"] == "imported_scada" else "synthetic_demo_thresholds"
    return payload


def _decode_alert(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for field in ("expected_measurements", "observed_measurements", "contributing_telemetry_refs", "lifecycle_history"):
        item[field] = json.loads(item[field])
    item["onset_timestamp"] = item.get("onset_timestamp") or item["start_timestamp"]
    item["detection_eligible_timestamp"] = item.get("detection_eligible_timestamp") or item["start_timestamp"]
    item["last_abnormal_timestamp"] = item.get("last_abnormal_timestamp") or item["end_timestamp"]
    start = parse_iso_datetime(item["onset_timestamp"])
    end = parse_iso_datetime(item.get("recovery_timestamp") or item["last_abnormal_timestamp"])
    item["wall_clock_duration_minutes"] = int((end - start).total_seconds() / 60) if start and end else None
    item["duration_minutes"] = item["wall_clock_duration_minutes"]
    item["alert_creation_timestamp"] = item["created_at"]
    item["evidence_type"] = "telemetry"
    item["diagnosis_status"] = "unconfirmed"
    return item


def _alert_query(where: list[str], params: list[Any], severity: str | None, status: str | None, anomaly_type: str | None, start: str | None, end: str | None) -> list[dict[str, Any]]:
    if severity and severity not in {"Critical", "High", "Medium", "Low"}:
        raise HTTPException(status_code=400, detail="Unknown alert severity")
    if status and status not in {"Open", "Acknowledged", "Investigating", "Resolved"}:
        raise HTTPException(status_code=400, detail="Unknown alert lifecycle status")
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if status:
        where.append("lifecycle_status = ?")
        params.append(status)
    if anomaly_type:
        if anomaly_type not in CATEGORIES:
            raise HTTPException(status_code=400, detail="Unknown inverter anomaly type")
        where.append("anomaly_category = ?")
        params.append(anomaly_type)
    normalized_start, normalized_end = _telemetry_range(start, end)
    if normalized_start:
        where.append("end_timestamp >= ?")
        params.append(normalized_start)
    if normalized_end:
        where.append("start_timestamp <= ?")
        params.append(normalized_end)
    with connect() as conn:
        rows = rows_to_dicts(conn.execute(
            "SELECT * FROM inverter_alerts WHERE " + " AND ".join(where) +
            " ORDER BY CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 ELSE 3 END, start_timestamp DESC",
            params,
        ))
    return [_decode_alert(row) for row in rows]


@app.get("/api/sites/{site_id}/inverter-alerts")
def site_inverter_alerts(site_id: str, severity: str | None = None, status: str | None = None, anomaly_type: str | None = None, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    return _alert_query(["site_id = ?"], [site_id], severity, status, anomaly_type, start, end)


@app.get("/api/inverters/{inverter_id}/alerts")
def inverter_alerts(inverter_id: str, severity: str | None = None, status: str | None = None, anomaly_type: str | None = None, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    get_inverter_or_404(inverter_id)
    return _alert_query(["inverter_id = ?"], [inverter_id], severity, status, anomaly_type, start, end)


@app.post("/api/inverters/{inverter_id}/analyze")
def analyze_inverter(inverter_id: str, start: str | None = None, end: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    dataset = _dataset_for_inverter(inverter, dataset_id)
    normalized_start, normalized_end = _telemetry_range(start, end)
    readings = _read_inverter_telemetry(inverter_id, normalized_start, normalized_end, dataset_id)
    with connect() as conn:
        if dataset["dataset_type"] == "synthetic_demo":
            site_end_row = conn.execute("SELECT MAX(t.timestamp) AS end_at FROM inverter_telemetry t JOIN inverters i ON i.inverter_id=t.inverter_id WHERE i.site_id = ?", (inverter["site_id"],)).fetchone()
            history_row = conn.execute("SELECT MIN(timestamp) AS first_at, MAX(timestamp) AS last_at, COUNT(*) AS samples FROM inverter_telemetry WHERE inverter_id=?", (inverter_id,)).fetchone()
        else:
            site_end_row = conn.execute("SELECT MAX(timestamp_utc) AS end_at FROM imported_inverter_telemetry WHERE dataset_id=?", (dataset["dataset_id"],)).fetchone()
            history_row = conn.execute("SELECT MIN(timestamp_utc) AS first_at, MAX(timestamp_utc) AS last_at, COUNT(*) AS samples FROM imported_inverter_telemetry WHERE dataset_id=? AND inverter_id=?", (dataset["dataset_id"],inverter_id)).fetchone()
    analysis_end = normalized_end or (site_end_row["end_at"] if site_end_row else None)
    events = detect_anomaly_events(
        inverter, readings, analysis_end=analysis_end,
        last_known_timestamp=history_row["last_at"] if history_row else None,
        has_historical_telemetry=bool(history_row and history_row["samples"]),
    )
    method = METHOD_VERSION if dataset["dataset_type"] == "synthetic_demo" else f"{METHOD_VERSION}:uncalibrated-imported"
    for event in events:
        event["detection_method"] = method
        event["expected_measurements"]["threshold_calibration_status"] = "uncalibrated_for_imported_site" if dataset["dataset_type"] == "imported_scada" else "synthetic_demo_thresholds"
        event["observed_measurements"]["dataset_id"] = dataset["dataset_id"]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    alert_ids: list[str] = []
    with connect() as conn:
        for event in events:
            identity = f"{inverter_id}|{event['anomaly_category']}|{event['start_timestamp']}|{method}|{dataset['dataset_id']}"
            alert_id = f"inv-alert-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex[:12]}"
            existing = conn.execute(
                """SELECT * FROM inverter_alerts WHERE inverter_id=? AND anomaly_category=? AND detection_method=?
                   AND COALESCE(last_abnormal_timestamp,end_timestamp) >= ?
                   AND COALESCE(onset_timestamp,start_timestamp) <= ?
                   ORDER BY created_at LIMIT 1""",
                (inverter_id, event["anomaly_category"], method, event["onset_timestamp"], event["last_abnormal_timestamp"]),
            ).fetchone()
            if existing:
                alert_id = existing["alert_id"]
                old_refs = json.loads(existing["contributing_telemetry_refs"])
                refs = sorted(set(old_refs) | set(event["contributing_telemetry_refs"]))
                old_onset = existing["onset_timestamp"] or existing["start_timestamp"]
                old_eligible = existing["detection_eligible_timestamp"] or existing["start_timestamp"]
                old_last = existing["last_abnormal_timestamp"] or existing["end_timestamp"]
                onset = min(old_onset, event["onset_timestamp"])
                eligible = min(old_eligible, event["detection_eligible_timestamp"])
                last_abnormal = max(old_last, event["last_abnormal_timestamp"])
                active_minutes = len(refs) * 15
                recovery = event.get("recovery_timestamp") or existing["recovery_timestamp"]
                wall_end = recovery or last_abnormal
                wall_minutes = max(active_minutes, int((parse_iso_datetime(wall_end) - parse_iso_datetime(onset)).total_seconds() / 60))
                conn.execute(
                    """UPDATE inverter_alerts SET end_timestamp=?, severity=?, detection_score=?,
                       expected_measurements=?, observed_measurements=?, contributing_telemetry_refs=?,
                       explanation=?, recommended_investigation=?, updated_at=?, onset_timestamp=?,
                       detection_eligible_timestamp=?, last_abnormal_timestamp=?, recovery_timestamp=?,
                       active_duration_minutes=?, excluded_duration_minutes=? WHERE alert_id=?""",
                    (last_abnormal, event["severity"], event["detection_score"],
                     json.dumps(event["expected_measurements"]), json.dumps(event["observed_measurements"]),
                     json.dumps(refs), event["explanation"], event["recommended_investigation"], now,
                     onset, eligible, last_abnormal, recovery, active_minutes, max(0, wall_minutes-active_minutes), alert_id),
                )
            else:
                history = [{"at": now, "status": "Open", "actor": "Inverter Detection Engine", "note": "Evidence-backed telemetry event created for operator investigation."}]
                conn.execute(
                    """INSERT INTO inverter_alerts
                       (alert_id, site_id, inverter_id, anomaly_category, anomaly_subtype,
                        start_timestamp, end_timestamp, severity, detection_method, detection_score,
                        expected_measurements, observed_measurements, contributing_telemetry_refs,
                        explanation, recommended_investigation, lifecycle_status, lifecycle_history,
                        created_at, updated_at, data_provenance, onset_timestamp,
                        detection_eligible_timestamp, last_abnormal_timestamp, recovery_timestamp,
                        active_duration_minutes, excluded_duration_minutes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (alert_id, inverter["site_id"], inverter_id, event["anomaly_category"], event.get("anomaly_subtype"),
                     event["start_timestamp"], event["end_timestamp"], event["severity"], event["detection_method"],
                     event["detection_score"], json.dumps(event["expected_measurements"]),
                     json.dumps(event["observed_measurements"]), json.dumps(event["contributing_telemetry_refs"]),
                     event["explanation"], event["recommended_investigation"], json.dumps(history), now, now,
                     dataset["provenance"], event["onset_timestamp"], event["detection_eligible_timestamp"],
                     event["last_abnormal_timestamp"], event.get("recovery_timestamp"),
                     event["active_duration_minutes"], event["excluded_duration_minutes"]),
                )
            alert_ids.append(alert_id)
        conn.commit()
    return {
        "inverter_id": inverter_id, "analysis_start": normalized_start or (readings[0]["timestamp"] if readings else None),
        "analysis_end": analysis_end, "events_detected": len(events), "alert_ids": alert_ids,
        "method": method, "dataset": dataset, "idempotent": True,
        "threshold_calibration_status": "uncalibrated_for_imported_site" if dataset["dataset_type"] == "imported_scada" else "synthetic_demo_thresholds",
        "message": "Rule-based investigation alerts only; no root cause has been verified.",
    }


@app.get("/api/inverter-alerts/evaluation")
def inverter_alert_evaluation(site_id: str = "site-001") -> dict[str, Any]:
    get_site_or_404(site_id)
    with connect() as conn:
        inverters = rows_to_dicts(conn.execute("SELECT * FROM inverters WHERE site_id=? ORDER BY inverter_id", (site_id,)))
        scenarios = rows_to_dicts(conn.execute(
            """SELECT s.* FROM inverter_scenarios s JOIN inverters i ON i.inverter_id=s.inverter_id
               WHERE i.site_id=? ORDER BY s.scenario_id""", (site_id,)
        ))
        site_end = conn.execute(
            "SELECT MAX(t.timestamp) FROM inverter_telemetry t JOIN inverters i ON i.inverter_id=t.inverter_id WHERE i.site_id=?", (site_id,)
        ).fetchone()[0]
    detected: list[dict[str, Any]] = []
    for inverter in inverters:
        for event in detect_anomaly_events(inverter, _read_inverter_telemetry(inverter["inverter_id"]), analysis_end=site_end):
            detected.append({**event, "inverter_id": inverter["inverter_id"]})
    fixture = evaluate_synthetic_events(detected, scenarios)
    held_out_data = generate_held_out_cases()
    held_out_detected: list[dict[str, Any]] = []
    held_out_scenarios: list[dict[str, Any]] = []
    for case in held_out_data["cases"]:
        held_out_scenarios.append(case["scenario"])
        for event in detect_anomaly_events(case["inverter"], case["readings"], analysis_end=case["analysis_end"]):
            held_out_detected.append({**event, "inverter_id": case["inverter"]["inverter_id"]})
    held_out = evaluate_synthetic_events(held_out_detected, held_out_scenarios)
    return {
        **fixture,
        "regression_fixture": fixture,
        "held_out": held_out,
        "held_out_generator": {"version": held_out_data["generator_version"], "seeds": held_out_data["seeds"], "case_count": len(held_out_data["cases"])},
    }


@app.get("/api/ml/model")
def ml_model_metadata() -> dict[str, Any]:
    artifact, reason = _load_ml_artifact()
    if artifact is None:
        return {
            "enabled": False, "status": "experimental_unavailable", "reason": reason,
            "feature_version": FEATURE_VERSION, "operational_alert_writes": False,
        }
    return {
        "enabled": True,
        "status": "experimental",
        **artifact["metadata"],
        "artifact_schema_version": artifact["schema_version"],
        "operational_alert_writes": False,
    }


@app.get("/api/ml/evaluation")
def ml_evaluation() -> dict[str, Any]:
    if not ML_EVALUATION_PATH.exists():
        return {"enabled": False, "status": "experimental_unavailable", "reason": "No offline evaluation report is installed."}
    try:
        return {"enabled": True, "status": "experimental", **json.loads(ML_EVALUATION_PATH.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError) as exc:
        return {"enabled": False, "status": "experimental_unavailable", "reason": f"Evaluation report is unavailable: {exc}"}


@app.get("/api/inverters/{inverter_id}/ml-scores")
def inverter_ml_scores(inverter_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    artifact, reason = _load_ml_artifact()
    if artifact is None:
        return {"enabled": False, "status": "experimental_unavailable", "reason": reason, "inverter_id": inverter_id, "scores": [], "candidates": []}
    rows = _ml_feature_rows(inverter, start, end)
    detector = artifact["detector"]
    scored = detector.score_rows(rows)
    candidates = candidate_events(scored, detector.threshold, "isolation_forest", MODEL_VERSION)
    return {
        "enabled": True,
        "status": "experimental_review_only",
        "inverter_id": inverter_id,
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "threshold": detector.threshold,
        "score_semantics": "higher_is_more_abnormal",
        "scores": [{
            "timestamp": row["timestamp"], "anomaly_score": round(row["anomaly_score"], 6),
            "above_threshold": row["anomaly_score"] >= detector.threshold,
        } for row in scored],
        "candidates": candidates,
        "limitations": artifact["metadata"].get("limitations", []),
        "operational_alert_writes": False,
    }


@app.get("/api/inverters/{inverter_id}/ml-comparison")
def inverter_ml_comparison(inverter_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    artifact, reason = _load_ml_artifact()
    if artifact is None:
        return {"enabled": False, "status": "experimental_unavailable", "reason": reason, "inverter_id": inverter_id}
    readings = _read_inverter_telemetry(inverter_id, start, end)
    rows = _ml_feature_rows(inverter, start, end)
    detector, baseline = artifact["detector"], artifact["baseline"]
    ml_candidates = candidate_events(detector.score_rows(rows), detector.threshold, "isolation_forest", MODEL_VERSION)
    statistical_candidates = candidate_events(baseline.score_rows(rows), baseline.threshold, "robust_statistical_baseline", BASELINE_VERSION)
    rules = [{**event, "inverter_id": inverter_id} for event in detect_anomaly_events(inverter, readings)]
    agreements = []
    for ml_candidate in ml_candidates:
        matching = [event for event in rules if event["anomaly_category"] == ml_candidate["anomaly_category"] and event["start_timestamp"] <= ml_candidate["end_timestamp"] and ml_candidate["start_timestamp"] <= event["end_timestamp"]]
        agreements.append({
            "candidate_start": ml_candidate["start_timestamp"],
            "anomaly_category": ml_candidate["anomaly_category"],
            "rule_agreement": bool(matching),
        })
    return {
        "enabled": True,
        "status": "experimental_review_only",
        "inverter_id": inverter_id,
        "rules_version": METHOD_VERSION,
        "model_version": MODEL_VERSION,
        "rule_events": rules,
        "statistical_candidates": statistical_candidates,
        "ml_candidates": ml_candidates,
        "agreement": agreements,
        "summary": {
            "rule_event_count": len(rules), "statistical_candidate_count": len(statistical_candidates),
            "ml_candidate_count": len(ml_candidates), "rule_ml_agreement_count": sum(item["rule_agreement"] for item in agreements),
        },
        "operational_alert_writes": False,
        "notice": "Experimental synthetic-data model output for review only; candidates are not confirmed faults or operational alerts.",
    }


@app.get("/api/inverter-alerts/{alert_id}")
def inverter_alert_detail(alert_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inverter_alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Inverter alert not found")
    return _decode_alert(dict(row))


@app.get("/api/inverter-alerts/{alert_id}/evidence")
def inverter_alert_evidence(alert_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inverter_alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Inverter alert not found")
        alert = _decode_alert(dict(row))
        inverter_row = conn.execute("SELECT * FROM inverters WHERE inverter_id=?", (alert["inverter_id"],)).fetchone()
        topology = inverter_topology(conn, dict(inverter_row))
        findings = correlated_findings(conn, alert) if topology["available"] else []
        associations = association_records(conn, alert_id)
    missing = ["MPPT-level and string-level electrical telemetry", "field inspection or IV-curve confirmation"]
    if not topology.get("is_verified_as_built"):
        missing.insert(0, "verified customer as-built inverter-to-string-to-module mapping")
    if not findings:
        missing.append("inspection finding on a connected mapped asset")
    return {
        "alert": alert,
        "topology": topology,
        "connected_equipment": {
            "inverter_id": alert["inverter_id"], "blocks": topology.get("blocks", []),
            "mppt_count": topology.get("mppt_count"), "string_count": topology.get("string_count"),
            "mapped_module_count": topology.get("mapped_module_count"),
        },
        "electrical_evidence": {
            "expected_measurements": alert["expected_measurements"],
            "observed_measurements": alert["observed_measurements"],
            "telemetry_references": alert["contributing_telemetry_refs"],
            "detection_method": alert["detection_method"], "diagnosis_status": "unconfirmed",
        },
        "related_visual_findings": findings,
        "reviewed_associations": associations,
        "missing_confirmation_information": missing,
        "correlation_notice": "Topology and temporal proximity identify evidence for review; neither proves a shared electrical root cause.",
        "operational_detector": METHOD_VERSION,
        "experimental_ml_used_for_correlation": False,
    }


@app.post("/api/inverter-alerts/{alert_id}/associations")
def create_alert_finding_association(alert_id: str, payload: AlertFindingAssociationCreate) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        alert = conn.execute("SELECT alert_id,site_id FROM inverter_alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if alert is None:
            raise HTTPException(status_code=404, detail="Inverter alert not found")
        finding = conn.execute("SELECT id,site_id FROM anomalies WHERE id=?", (payload.anomaly_id,)).fetchone()
        if finding is None:
            raise HTTPException(status_code=404, detail="Visual finding not found")
        if alert["site_id"] != finding["site_id"]:
            raise HTTPException(status_code=400, detail="Alert and visual finding must belong to the same site")
        existing = conn.execute(
            "SELECT association_id,review_status FROM alert_finding_associations WHERE alert_id=? AND anomaly_id=?",
            (alert_id, payload.anomaly_id),
        ).fetchone()
        if existing and existing["review_status"] not in {"Rejected", "Removed"}:
            raise HTTPException(status_code=409, detail="An active association already exists")
        association_id = existing["association_id"] if existing else f"assoc-{uuid.uuid4().hex[:12]}"
        if existing:
            conn.execute(
                """UPDATE alert_finding_associations SET review_status='Proposed',reviewer=?,explanation=?,
                     provenance='operator_review',updated_at=? WHERE association_id=?""",
                (payload.reviewer, payload.explanation, now, association_id),
            )
        else:
            conn.execute(
                """INSERT INTO alert_finding_associations
                   (association_id,site_id,alert_id,anomaly_id,review_status,reviewer,explanation,
                    provenance,created_at,updated_at)
                   VALUES (?,?,?,?,'Proposed',?,?,'operator_review',?,?)""",
                (association_id, alert["site_id"], alert_id, payload.anomaly_id, payload.reviewer, payload.explanation, now, now),
            )
        conn.execute(
            """INSERT INTO alert_finding_association_events
               (event_id,association_id,review_status,reviewer,explanation,provenance,recorded_at)
               VALUES (?,?,'Proposed',?,?,'operator_review',?)""",
            (f"assoc-event-{uuid.uuid4().hex[:12]}", association_id, payload.reviewer, payload.explanation, now),
        )
        conn.commit()
        return next(item for item in association_records(conn, alert_id) if item["association_id"] == association_id)


@app.patch("/api/inverter-alert-associations/{association_id}")
def update_alert_finding_association(association_id: str, patch: AlertFindingAssociationPatch) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        row = conn.execute("SELECT * FROM alert_finding_associations WHERE association_id=?", (association_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Association not found")
        if row["review_status"] == "Removed":
            raise HTTPException(status_code=409, detail="Removed associations must be proposed again before review")
        conn.execute(
            """UPDATE alert_finding_associations SET review_status=?,reviewer=?,explanation=?,updated_at=?
               WHERE association_id=?""",
            (patch.review_status, patch.reviewer, patch.explanation, now, association_id),
        )
        conn.execute(
            """INSERT INTO alert_finding_association_events
               (event_id,association_id,review_status,reviewer,explanation,provenance,recorded_at)
               VALUES (?,?,?,?,?,'operator_review',?)""",
            (f"assoc-event-{uuid.uuid4().hex[:12]}", association_id, patch.review_status, patch.reviewer, patch.explanation, now),
        )
        conn.commit()
        return next(item for item in association_records(conn, row["alert_id"]) if item["association_id"] == association_id)


@app.patch("/api/inverter-alerts/{alert_id}")
def update_inverter_alert(alert_id: str, patch: InverterAlertPatch) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inverter_alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Inverter alert not found")
        history = json.loads(row["lifecycle_history"])
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        history.append({"at": now, "status": patch.lifecycle_status, "actor": patch.actor, "note": patch.note or f"Status changed from {row['lifecycle_status']}."})
        acknowledgment = row["acknowledgment_timestamp"]
        if acknowledgment is None and patch.lifecycle_status in {"Acknowledged", "Investigating", "Resolved"}:
            acknowledgment = now
        conn.execute(
            "UPDATE inverter_alerts SET lifecycle_status=?, lifecycle_history=?, updated_at=?, acknowledgment_timestamp=? WHERE alert_id=?",
            (patch.lifecycle_status, json.dumps(history), now, acknowledgment, alert_id),
        )
        conn.commit()
    return inverter_alert_detail(alert_id)


@app.get("/api/sites/{site_id}/anomalies")
def anomalies(
    site_id: str,
    priority: str | None = None,
    status: str | None = None,
    anomaly_type: str | None = None,
) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    where = ["site_id = ?"]
    params: list[Any] = [site_id]
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if status:
        where.append("status = ?")
        params.append(status)
    if anomaly_type:
        where.append("anomaly_type = ?")
        params.append(anomaly_type)
    sql = "SELECT * FROM anomalies WHERE " + " AND ".join(where) + " ORDER BY CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END, annual_revenue_loss DESC"
    with connect() as conn:
        result = rows_to_dicts(conn.execute(sql, params))
    for item in result:
        item["history"] = json.loads(item["history"])
    return result


@app.get("/api/anomalies/{anomaly_id}")
def anomaly_detail(anomaly_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM anomalies WHERE id = ?", (anomaly_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Anomaly not found")
        item = dict(row)
        tasks = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE anomaly_id = ? ORDER BY created_at DESC", (anomaly_id,)))
    item["history"] = json.loads(item["history"])
    item["tasks"] = tasks
    return item


@app.patch("/api/anomalies/{anomaly_id}")
def update_anomaly(anomaly_id: str, patch: AnomalyPatch) -> dict[str, Any]:
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        return anomaly_detail(anomaly_id)
    with connect() as conn:
        row = conn.execute("SELECT * FROM anomalies WHERE id = ?", (anomaly_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Anomaly not found")
        history = json.loads(row["history"])
        for key, value in updates.items():
            if key in {"status", "priority"} and value != row[key]:
                history.append({
                    "at": datetime.now(timezone.utc).date().isoformat(),
                    "event": f"{key.replace('_',' ').title()} changed from {row[key]} to {value}",
                    "actor": "DeepDrishti user",
                })
        updates["history"] = json.dumps(history)
        clause = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(f"UPDATE anomalies SET {clause} WHERE id = ?", [*updates.values(), anomaly_id])
        conn.commit()
    return anomaly_detail(anomaly_id)


def _visual_preview_response(metadata: dict[str, Any]) -> dict[str, Any]:
    token = metadata["preview_token"]
    return {
        "preview_token": token, "modality": metadata["modality"], "result": metadata["result"],
        "provenance": metadata["provenance"], "expires_at": metadata["expires_at"],
        "fictional_demo": metadata["provenance"] == "fictional_demo_fixture",
        "evidence": {name: f"/api/inverter-visual-previews/{token}/evidence/{name}" for name in metadata["evidence_names"]},
        "notice": "Candidate regions require human review and are not a trained-model prediction or confirmed equipment fault.",
    }


@app.post("/api/inverters/{inverter_id}/visual-inspections/rgb/preview")
async def preview_inverter_rgb(
    inverter_id: str, reference: UploadFile | None = File(default=None), current: UploadFile | None = File(default=None),
    minimum_channel_difference: float = Form(30.0), demo_case: str | None = Form(None),
    dataset_id: str | None = Form(None), demo_mode: bool = Form(False),
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    if dataset_id:
        _dataset_for_inverter(inverter, dataset_id)
    if demo_case:
        if demo_case not in {"rgb_change", "healthy_unchanged"}:
            raise HTTPException(status_code=400, detail="Unknown RGB demo case")
        reference_name = "inverter_before.png"
        current_name = "inverter_after.png" if demo_case == "rgb_change" else "inverter_before.png"
        reference_content = (INVERTER_VISION_DEMO_ROOT / reference_name).read_bytes()
        current_content = (INVERTER_VISION_DEMO_ROOT / current_name).read_bytes()
        provenance = "fictional_demo_fixture"
    else:
        if reference is None or current is None:
            raise HTTPException(status_code=400, detail="Both reference and current RGB images are required")
        reference_name, current_name = Path(reference.filename or "reference").name, Path(current.filename or "current").name
        reference_content, current_content = await reference.read(), await current.read()
        provenance = "operator_supplied_upload"
    try:
        result, evidence = analyze_rgb_bytes(reference_content, current_content, reference_name=reference_name,
            current_name=current_name, minimum_channel_difference=minimum_channel_difference)
        result["input_identifiers"] = {"reference": reference_name, "current": current_name}
        metadata = create_preview(site_id=inverter["site_id"], inverter_id=inverter_id, modality="rgb",
            result=result, evidence=evidence, provenance=provenance, dataset_id=dataset_id)
    except VisionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _visual_preview_response(metadata)


@app.post("/api/inverters/{inverter_id}/visual-inspections/thermal/preview")
async def preview_inverter_thermal(
    inverter_id: str, reference: UploadFile | None = File(default=None), current: UploadFile | None = File(default=None),
    temperature_unit: str = Form("Celsius"), delta_threshold_c: float = Form(10.0),
    demo_case: str | None = Form(None), dataset_id: str | None = Form(None), demo_mode: bool = Form(False),
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    if dataset_id:
        _dataset_for_inverter(inverter, dataset_id)
    if demo_case:
        if demo_case != "thermal_rise":
            raise HTTPException(status_code=400, detail="Unknown thermal demo case")
        reference_name, current_name = "thermal_before_celsius.csv", "thermal_after_celsius.csv"
        reference_content = (INVERTER_VISION_DEMO_ROOT / reference_name).read_bytes()
        current_content = (INVERTER_VISION_DEMO_ROOT / current_name).read_bytes()
        temperature_unit, provenance = "Celsius", "fictional_demo_fixture"
    else:
        if reference is None or current is None:
            raise HTTPException(status_code=400, detail="Both reference and current calibrated temperature CSV files are required")
        reference_name, current_name = Path(reference.filename or "reference.csv").name, Path(current.filename or "current.csv").name
        if Path(reference_name).suffix.lower() != ".csv" or Path(current_name).suffix.lower() != ".csv":
            raise HTTPException(status_code=415, detail="Thermal temperature comparison accepts numeric CSV files only")
        reference_content, current_content = await reference.read(), await current.read()
        provenance = "operator_supplied_calibrated_matrix_assertion"
    try:
        result, evidence = analyze_thermal_bytes(reference_content, current_content, reference_name=reference_name,
            current_name=current_name, unit=temperature_unit, delta_threshold_c=delta_threshold_c)
        result["input_identifiers"] = {"reference": reference_name, "current": current_name}
        metadata = create_preview(site_id=inverter["site_id"], inverter_id=inverter_id, modality="thermal",
            result=result, evidence=evidence, provenance=provenance, dataset_id=dataset_id)
    except VisionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _visual_preview_response(metadata)


@app.get("/api/inverter-visual-previews/{preview_token}/evidence/{evidence_name}")
def inverter_visual_preview_evidence(preview_token: str, evidence_name: str) -> FileResponse:
    try:
        path = preview_evidence_path(preview_token, evidence_name)
    except VisionValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "text/csv" if path.suffix == ".csv" else "image/png"
    return FileResponse(path, media_type=media_type, filename=path.name if path.suffix == ".csv" else None)


def _visual_inspection_payload(conn: sqlite3.Connection, inspection_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM inverter_visual_inspections WHERE inspection_id=?", (inspection_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Inverter visual inspection not found")
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json"))
    item["evidence"] = json.loads(item.pop("evidence_json"))
    candidates = rows_to_dicts(conn.execute(
        "SELECT * FROM inverter_visual_candidates WHERE inspection_id=? ORDER BY candidate_id", (inspection_id,)
    ))
    for candidate in candidates:
        candidate["bbox_xyxy"] = json.loads(candidate.pop("bbox_json"))
        candidate["review_history"] = rows_to_dicts(conn.execute(
            "SELECT * FROM inverter_visual_candidate_review_events WHERE candidate_id=? ORDER BY created_at,rowid",
            (candidate["candidate_id"],),
        ))
    item["candidates"] = candidates
    item["candidate_count"] = len(candidates)
    item["evidence_urls"] = {name: f"/api/inverter-visual-inspections/{inspection_id}/evidence/{name}" for name in item["evidence"]}
    item["review_semantics"] = "Accepted means relevant review evidence only; it is not an authenticated approval, confirmed fault, root cause, alert or maintenance action."
    return item


@app.post("/api/inverters/{inverter_id}/visual-inspections")
def save_inverter_visual_inspection(
    inverter_id: str, body: InverterVisualInspectionSave, demo_mode: bool = False,
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    try:
        metadata = read_preview(body.preview_token)
    except VisionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if metadata["site_id"] != inverter["site_id"] or metadata["inverter_id"] != inverter_id:
        raise HTTPException(status_code=400, detail="Preview does not belong to the selected site and inverter")
    with connect() as conn:
        existing = conn.execute("SELECT inspection_id FROM inverter_visual_inspections WHERE preview_token=?", (body.preview_token,)).fetchone()
        if existing:
            return _visual_inspection_payload(conn, existing["inspection_id"])
    inspection_id = f"inv-vis-{uuid.uuid4().hex[:12]}"
    _, locations = persist_preview(body.preview_token, inspection_id)
    result = metadata["result"]
    capture = body.capture_timestamp or datetime.now(timezone.utc)
    if capture.tzinfo is None:
        capture = capture.replace(tzinfo=timezone.utc)
    captured_at = capture.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    identifiers = result.get("input_identifiers", {})
    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO inverter_visual_inspections
                   (inspection_id,site_id,inverter_id,modality,capture_timestamp,reference_identifier,current_identifier,
                    analysis_method,analysis_version,parameters_json,provenance,created_at,review_state,
                    telemetry_dataset_id,evidence_json,is_fictional_demo,preview_token)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (inspection_id,inverter["site_id"],inverter_id,metadata["modality"],captured_at,
                 identifiers.get("reference","reference"),identifiers.get("current","current"),
                 "deterministic_reference_comparison",INVERTER_VISION_VERSION,
                 json.dumps({key: result[key] for key in ("threshold_0_255","delta_threshold_c","image_size","matrix_size") if key in result}),
                 metadata["provenance"],created_at,"Pending Review",metadata.get("dataset_id"),json.dumps(locations),
                 int(metadata["provenance"] == "fictional_demo_fixture"),body.preview_token),
            )
            evidence_name = "annotated.png" if metadata["modality"] == "rgb" else "difference_heatmap.png"
            for candidate in result["candidate_regions"]:
                candidate_id = f"inv-vis-cand-{uuid.uuid5(uuid.NAMESPACE_URL, inspection_id + ':' + str(candidate['candidate_index'])).hex[:12]}"
                conn.execute(
                    """INSERT INTO inverter_visual_candidates
                       (candidate_id,inspection_id,site_id,candidate_type,bbox_json,region_size,relative_change_score,
                        peak_temperature_rise_c,threshold_value,evidence_location,detection_method,review_state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,'Pending Review')""",
                    (candidate_id,inspection_id,inverter["site_id"],candidate["candidate_type"],json.dumps(candidate["bbox_xyxy"]),
                     candidate.get("region_size_pixels",candidate.get("region_size_cells")),candidate.get("relative_change_score"),
                     candidate.get("peak_temperature_rise_c"),candidate.get("threshold_c",result.get("threshold_0_255")),
                     locations[evidence_name],candidate["detection_method"]),
                )
                conn.execute(
                    """INSERT INTO inverter_visual_candidate_review_events
                       VALUES (?,?,?,?,?,'Deterministic comparison engine','Candidate created; human review required.','automated_candidate_creation',?)""",
                    (f"inv-vis-review-{uuid.uuid4().hex[:12]}",candidate_id,inspection_id,inverter["site_id"],"Pending Review",created_at),
                )
            conn.commit()
            saved = _visual_inspection_payload(conn, inspection_id)
            saved["telemetry_evidence"] = _visual_telemetry_correlation(saved, metadata.get("dataset_id"))
            return saved
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Unable to save visual inspection: {exc}") from exc


@app.get("/api/inverters/{inverter_id}/visual-inspections")
def inverter_visual_inspections(inverter_id: str) -> list[dict[str, Any]]:
    inverter = get_inverter_or_404(inverter_id)
    with connect() as conn:
        records = rows_to_dicts(conn.execute(
            """SELECT vi.*,COUNT(vc.candidate_id) AS candidate_count
               FROM inverter_visual_inspections vi LEFT JOIN inverter_visual_candidates vc ON vc.inspection_id=vi.inspection_id
               WHERE vi.site_id=? AND vi.inverter_id=? GROUP BY vi.inspection_id
               ORDER BY vi.capture_timestamp DESC,vi.created_at DESC""", (inverter["site_id"],inverter_id),
        ))
    for item in records:
        item["parameters"] = json.loads(item.pop("parameters_json")); item.pop("evidence_json"); item.pop("preview_token")
    return records


def _visual_telemetry_correlation(inspection: dict[str, Any], dataset_id: str | None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inspection["inverter_id"])
    chosen = dataset_id or inspection.get("telemetry_dataset_id")
    dataset = _dataset_for_inverter(inverter, chosen)
    readings = _read_inverter_telemetry(inverter["inverter_id"], dataset_id=dataset["dataset_id"])
    capture = parse_iso_datetime(inspection["capture_timestamp"])
    nearest = min(readings, key=lambda row: abs((parse_iso_datetime(row["timestamp"]) - capture).total_seconds())) if readings and capture else None
    proximity = abs((parse_iso_datetime(nearest["timestamp"]) - capture).total_seconds()) / 60 if nearest and capture else None
    if nearest:
        nearest = {key: nearest.get(key) for key in ("timestamp","ac_power_kw","dc_power_kw","inverter_temperature_c","operating_state","grid_state","data_provenance","source_system")}
    with connect() as conn:
        alerts = rows_to_dicts(conn.execute("SELECT * FROM inverter_alerts WHERE inverter_id=? ORDER BY start_timestamp DESC LIMIT 5", (inverter["inverter_id"],)))
    return {"dataset": dataset, "nearest_reading": nearest, "telemetry_timestamp_proximity_minutes": round(proximity,2) if proximity is not None else None,
            "recent_rule_alerts": [_decode_alert(row) for row in alerts],
            "correlation_notice": "Visual and electrical observations are separate review evidence; temporal proximity does not prove a shared root cause."}


@app.get("/api/inverter-visual-inspections/{inspection_id}")
def inverter_visual_inspection_detail(inspection_id: str, dataset_id: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        item = _visual_inspection_payload(conn, inspection_id)
    item["telemetry_evidence"] = _visual_telemetry_correlation(item, dataset_id)
    item.pop("preview_token", None)
    return item


@app.get("/api/inverter-visual-inspections/{inspection_id}/evidence/{evidence_name}")
def inverter_visual_saved_evidence(inspection_id: str, evidence_name: str) -> FileResponse:
    with connect() as conn:
        item = _visual_inspection_payload(conn, inspection_id)
    try:
        path = saved_evidence_path(inspection_id, evidence_name, list(item["evidence"]))
    except VisionValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved evidence file is unavailable")
    return FileResponse(path, media_type="text/csv" if path.suffix == ".csv" else "image/png",
                        filename=path.name if path.suffix == ".csv" else None)


@app.patch("/api/inverter-visual-inspections/{inspection_id}/review")
def review_inverter_visual_candidate(
    inspection_id: str, body: InverterVisualReview, demo_mode: bool = False,
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        candidate = conn.execute(
            """SELECT vc.*,vi.inverter_id FROM inverter_visual_candidates vc
               JOIN inverter_visual_inspections vi ON vi.inspection_id=vc.inspection_id
               WHERE vc.candidate_id=? AND vc.inspection_id=?""", (body.candidate_id,inspection_id),
        ).fetchone()
        if not candidate:
            raise HTTPException(status_code=404, detail="Visual candidate not found for this inspection")
        conn.execute("""UPDATE inverter_visual_candidates SET review_state=?,reviewer_label=?,reviewer_explanation=?,reviewed_at=?
                        WHERE candidate_id=?""", (body.review_state,body.reviewer_label,body.explanation,now,body.candidate_id))
        conn.execute("""INSERT INTO inverter_visual_candidate_review_events VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"inv-vis-review-{uuid.uuid4().hex[:12]}",body.candidate_id,inspection_id,candidate["site_id"],body.review_state,
             body.reviewer_label,body.explanation,"unauthenticated_demo_assertion",now))
        states = [row[0] for row in conn.execute("SELECT review_state FROM inverter_visual_candidates WHERE inspection_id=?", (inspection_id,))]
        aggregate = "Needs Further Investigation" if "Needs Further Investigation" in states else (
            "Pending Review" if "Pending Review" in states else "Accepted as Relevant Evidence" if "Accepted as Relevant Evidence" in states else "Rejected")
        conn.execute("UPDATE inverter_visual_inspections SET review_state=? WHERE inspection_id=?", (aggregate,inspection_id))
        conn.commit()
        result = _visual_inspection_payload(conn, inspection_id)
    result["telemetry_evidence"] = _visual_telemetry_correlation(result, result.get("telemetry_dataset_id"))
    result.pop("preview_token", None)
    return result


@app.get("/api/sites/{site_id}/inspections")
def inspections(site_id: str) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM inspections WHERE site_id = ? ORDER BY captured_at DESC", (site_id,)))


@app.post("/api/inspections/analyze")
def analyze_inspection(
    site_id: str = Form("site-001"),
    file: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    get_site_or_404(site_id)
    if file is None:
        source = STATIC_ROOT / "assets" / "sample_thermal_input.png"
        original_name = "sample_thermal_input.png"
    else:
        suffix = Path(file.filename or "inspection.png").suffix.lower() or ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            raise HTTPException(status_code=400, detail="Upload a PNG, JPG, WEBP or TIFF image")
        target = UPLOAD_ROOT / f"{uuid.uuid4().hex}{suffix}"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        source = target
        original_name = file.filename or target.name

    try:
        findings = analyze_thermal_image(source)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to analyze image: {exc}") from exc

    inspection_id = f"ins-ai-{uuid.uuid4().hex[:8]}"
    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    image_path = "/static/assets/sample_thermal_input.png" if file is None else f"/static/uploads/{source.name}"
    created: list[str] = []
    total_kw = 0.0
    total_kwh = 0.0
    total_revenue = 0.0

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inspections (
                id, site_id, name, inspection_type, captured_at, status, findings,
                affected_kw, annual_kwh_loss, annual_revenue_loss, source,
                weather, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (
                inspection_id,
                site_id,
                f"AI Thermal Analysis — {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')}",
                "Thermal image analysis",
                captured,
                "AI review pending",
                len(findings),
                original_name,
                "Demo image • radiometric proxy",
                "thermal-demo-v0.3",
            ),
        )
        existing = conn.execute("SELECT COUNT(*) AS c FROM anomalies").fetchone()["c"]
        for i, finding in enumerate(findings, start=1):
            map_x = round(6 + finding.x * 0.88, 2)
            map_y = round(10 + finding.y * 0.80, 2)
            block = "B1" if map_x < 50 and map_y < 50 else "B2" if map_x >= 50 and map_y < 50 else "C1" if map_x < 50 else "C2"
            row_no = max(1, min(18, int((map_y % 50) / 2.7) + 1))
            module_no = max(1, min(32, int((map_x % 50) / 1.55) + 1))
            asset_id = f"SRP-{block}-R{row_no:02d}-M{module_no:02d}"
            anomaly_id = f"an-ai-{existing+i:04d}-{uuid.uuid4().hex[:4]}"
            priority = "Critical" if finding.delta_t >= 22 else "High" if finding.delta_t >= 15 else "Medium"
            affected_kw = round(0.55 * (0.95 if finding.anomaly_type == "Multi-cell Hotspot" else 0.55), 2)
            annual_kwh = round(affected_kw * 5.25 * 365 * 0.67, 1)
            revenue = round(annual_kwh * 6.9, 0)
            total_kw += affected_kw
            total_kwh += annual_kwh
            total_revenue += revenue
            history = json.dumps([
                {"at": date.today().isoformat(), "event": "Candidate generated from uploaded thermal image", "actor": "Thermal Demo Engine v0.3"},
                {"at": date.today().isoformat(), "event": f"Candidate associated with {asset_id}", "actor": "Asset Association Demo"},
            ])
            conn.execute(
                """
                INSERT INTO anomalies (
                    id, site_id, inspection_id, asset_id, anomaly_type, category,
                    priority, status, confidence, delta_t, normalized_delta_t,
                    affected_kw, annual_kwh_loss, annual_revenue_loss, block_name,
                    row_no, module_no, map_x, map_y, description,
                    recommended_action, first_detected, rgb_image, thermal_image,
                    history, notes
                ) VALUES (?, ?, ?, ?, ?, 'Thermal', ?, 'Detected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    anomaly_id,
                    site_id,
                    inspection_id,
                    asset_id,
                    finding.anomaly_type,
                    priority,
                    finding.confidence,
                    finding.delta_t,
                    round(finding.delta_t * 0.87, 1),
                    affected_kw,
                    annual_kwh,
                    revenue,
                    block,
                    row_no,
                    module_no,
                    map_x,
                    map_y,
                    f"{finding.anomaly_type} candidate generated from the uploaded image and localized to {asset_id}.",
                    "Review paired RGB evidence, verify neighboring-module baseline and inspect the module in the field.",
                    date.today().isoformat(),
                    "/static/assets/rgb_hotspot.png",
                    image_path,
                    history,
                ),
            )
            created.append(anomaly_id)
        conn.execute(
            "UPDATE inspections SET affected_kw = ?, annual_kwh_loss = ?, annual_revenue_loss = ? WHERE id = ?",
            (round(total_kw, 2), round(total_kwh, 1), round(total_revenue, 0), inspection_id),
        )
        conn.commit()

    return {
        "inspection_id": inspection_id,
        "filename": original_name,
        "findings": len(created),
        "anomaly_ids": created,
        "affected_kw": round(total_kw, 2),
        "annual_kwh_loss": round(total_kwh, 1),
        "annual_revenue_loss": round(total_revenue, 0),
        "message": "Demo analysis completed. Findings require human review before operational use.",
    }


@app.get("/api/tasks")
def tasks(site_id: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT tasks.*, anomalies.asset_id, anomalies.anomaly_type, anomalies.map_x, anomalies.map_y
        FROM tasks LEFT JOIN anomalies ON tasks.anomaly_id = anomalies.id
    """
    params: list[Any] = []
    if site_id:
        sql += " WHERE tasks.site_id = ?"
        params.append(site_id)
    sql += " ORDER BY CASE tasks.status WHEN 'Overdue' THEN 1 WHEN 'In Progress' THEN 2 WHEN 'Assigned' THEN 3 ELSE 4 END, tasks.due_date"
    with connect() as conn:
        return rows_to_dicts(conn.execute(sql, params))


@app.post("/api/tasks")
def create_task(payload: TaskCreate) -> dict[str, Any]:
    get_site_or_404(payload.site_id)
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with connect() as conn:
        if payload.anomaly_id:
            anomaly = conn.execute("SELECT id FROM anomalies WHERE id = ? AND site_id = ?", (payload.anomaly_id, payload.site_id)).fetchone()
            if anomaly is None:
                raise HTTPException(status_code=404, detail="Anomaly not found")
        conn.execute(
            """
            INSERT INTO tasks (
                id, site_id, anomaly_id, title, owner, due_date, status,
                priority, requested_action, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Assigned', ?, ?, ?, ?, ?)
            """,
            (
                task_id, payload.site_id, payload.anomaly_id, payload.title,
                payload.owner, payload.due_date.isoformat(), payload.priority,
                payload.requested_action, payload.notes, now, now,
            ),
        )
        if payload.anomaly_id:
            row = conn.execute("SELECT history FROM anomalies WHERE id = ?", (payload.anomaly_id,)).fetchone()
            history = json.loads(row["history"])
            history.append({"at": date.today().isoformat(), "event": f"Work order {task_id} assigned to {payload.owner}", "actor": "Workflow Service"})
            conn.execute("UPDATE anomalies SET status = 'Assigned', history = ? WHERE id = ?", (json.dumps(history), payload.anomaly_id))
        conn.commit()
    return next(task for task in tasks(payload.site_id) if task["id"] == task_id)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, patch: TaskPatch) -> dict[str, Any]:
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No changes supplied")
    updates["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        clause = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(f"UPDATE tasks SET {clause} WHERE id = ?", [*updates.values(), task_id])
        if updates.get("status") in {"Completed", "Verified"} and row["anomaly_id"]:
            anomaly_status = "Resolved" if updates["status"] == "Verified" else "In Progress"
            arow = conn.execute("SELECT history FROM anomalies WHERE id = ?", (row["anomaly_id"],)).fetchone()
            if arow:
                history = json.loads(arow["history"])
                history.append({"at": date.today().isoformat(), "event": f"Linked task {task_id} changed to {updates['status']}", "actor": "Field Workflow"})
                conn.execute("UPDATE anomalies SET status = ?, history = ? WHERE id = ?", (anomaly_status, json.dumps(history), row["anomaly_id"]))
        conn.commit()
    return next(task for task in tasks() if task["id"] == task_id)


@app.get("/api/sites/{site_id}/assets")
def assets(site_id: str) -> dict[str, Any]:
    site = get_site_or_404(site_id)
    with connect() as conn:
        topology = site_topology_summary(conn, site_id)
        finding_counts = {
            row["id"]: row for row in rows_to_dicts(conn.execute(
                """SELECT block_name AS id,COUNT(*) AS findings,ROUND(SUM(affected_kw),2) AS affected_kw
                   FROM anomalies WHERE site_id=? GROUP BY block_name""", (site_id,)
            ))
        }
        if topology["blocks"]:
            blocks = [
                {**block, **finding_counts.get(block["id"], {"findings": 0, "affected_kw": 0.0})}
                for block in topology["blocks"]
            ]
        else:
            blocks = [
                {**block, "rows": None, "mapping_classification": "unknown", "coverage_status": "unavailable"}
                for block in finding_counts.values()
            ]
    return {
        "site": site,
        "blocks": blocks,
        "hierarchy": {**topology["hierarchy"], "site": site["name"]},
        "topology_coverage": topology["coverage"],
    }


@app.get("/api/sites/{site_id}/export.csv")
def export_site_csv(site_id: str) -> StreamingResponse:
    get_site_or_404(site_id)
    data = anomalies(site_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "asset_id", "type", "priority", "status", "confidence",
        "delta_t", "affected_kw", "annual_kwh_loss", "annual_revenue_loss",
        "block", "row", "module", "first_detected",
    ])
    for item in data:
        writer.writerow([
            item["id"], item["asset_id"], item["anomaly_type"], item["priority"],
            item["status"], item["confidence"], item["delta_t"], item["affected_kw"],
            item["annual_kwh_loss"], item["annual_revenue_loss"], item["block_name"],
            item["row_no"], item["module_no"], item["first_detected"],
        ])
    output.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{site_id}-anomalies.csv"'}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)


def _require_demo_import_guard(demo_mode: bool, acknowledgment: str | None) -> None:
    if not demo_mode or acknowledgment != "acknowledged":
        raise HTTPException(
            status_code=403,
            detail="This MVP has no production authentication. Set demo_mode=true and send X-DeepDrishti-Demo-Operator: acknowledged.",
        )


async def _safe_csv(file: UploadFile) -> bytes:
    if file.content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only CSV uploads are accepted")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds the 2 MB upload limit")
    return content


@app.post("/api/sites/{site_id}/data-imports/scada/preview")
async def scada_import_preview(
    site_id: str, file: UploadFile = File(...), mapping_json: str = Form(...), units_json: str = Form("{}"),
    timezone_name: str = Form("UTC"), source_system: str = Form("operator_csv"), demo_mode: bool = Form(False),
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    get_site_or_404(site_id); _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    try:
        mapping, units = json.loads(mapping_json), json.loads(units_json)
        if not isinstance(mapping, dict) or not isinstance(units, dict): raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="mapping_json and units_json must be JSON objects") from exc
    try:
        with connect() as conn:
            return preview_scada_csv(conn, site_id=site_id, filename=Path(file.filename or "upload.csv").name,
                                     content=await _safe_csv(file), mapping=mapping, units=units,
                                     timezone_name=timezone_name, source_system=source_system[:120])
    except ImportValidationError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sites/{site_id}/data-imports/topology/preview")
async def topology_import_preview(
    site_id: str, file: UploadFile = File(...), source_system: str = Form("operator_csv"),
    mapping_version: str = Form(...), effective_date: str | None = Form(None), demo_mode: bool = Form(False),
    x_deepdrishti_demo_operator: str | None = Header(default=None),
) -> dict[str, Any]:
    get_site_or_404(site_id); _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    try:
        with connect() as conn:
            return preview_topology_csv(conn, site_id=site_id, filename=Path(file.filename or "upload.csv").name,
                                        content=await _safe_csv(file), source_system=source_system[:120],
                                        mapping_version=mapping_version[:120], effective_date=effective_date)
    except (ImportValidationError, sqlite3.IntegrityError) as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sites/{site_id}/data-imports/{import_id}/commit")
def commit_data_import(site_id: str, import_id: str, demo_mode: bool = False,
                       x_deepdrishti_demo_operator: str | None = Header(default=None)) -> dict[str, Any]:
    get_site_or_404(site_id); _require_demo_import_guard(demo_mode, x_deepdrishti_demo_operator)
    with connect() as conn:
        record = conn.execute("SELECT import_type FROM data_imports WHERE import_id=? AND site_id=?", (import_id,site_id)).fetchone()
        if not record: raise HTTPException(status_code=404, detail="Import not found for this site")
        try:
            return commit_scada_import(conn, import_id) if record["import_type"] == "scada_csv" else commit_topology_import(conn, import_id)
        except (ImportValidationError, sqlite3.IntegrityError) as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/sites/{site_id}/data-imports")
def data_import_history(site_id: str) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    with connect() as conn:
        records = rows_to_dicts(conn.execute("SELECT * FROM data_imports WHERE site_id=? ORDER BY created_at DESC", (site_id,)))
    for item in records:
        item["report"] = json.loads(item.pop("report_json")); item.pop("mapping_json"); item.pop("options_json")
    return records


@app.get("/api/sites/{site_id}/data-imports/{import_id}")
def data_import_detail(site_id: str, import_id: str) -> dict[str, Any]:
    with connect() as conn:
        record = conn.execute("SELECT * FROM data_imports WHERE import_id=? AND site_id=?", (import_id,site_id)).fetchone()
        if not record: raise HTTPException(status_code=404, detail="Import not found for this site")
        result = dict(record); result["report"] = json.loads(result.pop("report_json")); result["mapping"] = json.loads(result.pop("mapping_json")); result["options"] = json.loads(result.pop("options_json"))
        result["rejections"] = rows_to_dicts(conn.execute("SELECT row_number,raw_json,reasons_json FROM data_import_rejections WHERE import_id=? ORDER BY row_number LIMIT 100", (import_id,)))
    for row in result["rejections"]: row["raw"] = json.loads(row.pop("raw_json")); row["reasons"] = json.loads(row.pop("reasons_json"))
    return result


@app.get("/api/sites/{site_id}/data-imports/{import_id}/rejections.csv")
def data_import_rejections(site_id: str, import_id: str) -> StreamingResponse:
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM data_imports WHERE import_id=? AND site_id=?", (import_id,site_id)).fetchone()
        if not exists: raise HTTPException(status_code=404, detail="Import not found for this site")
        rows = list(conn.execute("SELECT row_number,raw_json,reasons_json FROM data_import_rejections WHERE import_id=? ORDER BY row_number", (import_id,)))
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(["row_number","reasons","source_record"])
    for row in rows: writer.writerow([row["row_number"], "; ".join(json.loads(row["reasons_json"])), row["raw_json"]])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{import_id}-rejections.csv"'})


@app.get("/api/sites/{site_id}/telemetry-datasets")
def telemetry_datasets(site_id: str) -> list[dict[str, Any]]:
    get_site_or_404(site_id)
    with connect() as conn: records = rows_to_dicts(conn.execute("SELECT * FROM telemetry_datasets WHERE site_id=? AND is_selectable=1 ORDER BY created_at", (site_id,)))
    return records


@app.post("/api/reset")
def reset_demo(demo_mode: bool = False) -> dict[str, Any]:
    if not demo_mode:
        raise HTTPException(status_code=400, detail="Explicit demo_mode=true is required")
    with connect() as conn:
        if conn.execute("SELECT 1 FROM data_imports WHERE status='committed' LIMIT 1").fetchone():
            raise HTTPException(status_code=409, detail="Reset refused: committed imported data is present. Demo reset never deletes imported customer data.")
        if conn.execute("SELECT 1 FROM inverter_visual_inspections LIMIT 1").fetchone():
            raise HTTPException(status_code=409, detail="Reset refused: saved inverter visual evidence is present. Demo reset never deletes inspection evidence.")
    init_db(reset=True)
    return {"status": "reset", "message": "Demo data restored"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/{path:path}")
def spa_fallback(path: str):
    candidate = STATIC_ROOT / path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_ROOT / "index.html")

from __future__ import annotations

import csv
from contextlib import asynccontextmanager
import io
import json
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import DB_PATH, connect, init_db, rows_to_dicts
from .inverter_anomaly import CATEGORIES, METHOD_VERSION, detect_anomaly_events, evaluate_synthetic_events
from .inverter_evaluation import generate_held_out_cases
from .inverter_monitoring import (
    canonical_utc_timestamp,
    conversion_efficiency_pct,
    expected_ac_power_kw,
    parse_iso_datetime,
    summarize_inverter,
)
from .vision import analyze_thermal_image

APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
UPLOAD_ROOT = STATIC_ROOT / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="DeepDrishti Solar Twin API",
    version="0.3.0",
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


def _read_inverter_telemetry(inverter_id: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
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
        return rows_to_dicts(conn.execute(
            "SELECT * FROM inverter_telemetry WHERE " + " AND ".join(where) + " ORDER BY timestamp",
            params,
        ))


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


@app.get("/api/inverters/{inverter_id}/telemetry")
def inverter_telemetry(inverter_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    readings = _read_inverter_telemetry(inverter_id, start, end)
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
        "data_classification": "synthetic_demo",
        "readings": readings,
    }


@app.get("/api/inverters/{inverter_id}/summary")
def inverter_summary(inverter_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    return _inverter_summary_payload(inverter, start, end)


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
def analyze_inverter(inverter_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    inverter = get_inverter_or_404(inverter_id)
    normalized_start, normalized_end = _telemetry_range(start, end)
    readings = _read_inverter_telemetry(inverter_id, normalized_start, normalized_end)
    with connect() as conn:
        site_end_row = conn.execute(
            "SELECT MAX(t.timestamp) AS end_at FROM inverter_telemetry t JOIN inverters i ON i.inverter_id=t.inverter_id WHERE i.site_id = ?",
            (inverter["site_id"],),
        ).fetchone()
        history_row = conn.execute(
            "SELECT MIN(timestamp) AS first_at, MAX(timestamp) AS last_at, COUNT(*) AS samples FROM inverter_telemetry WHERE inverter_id=?",
            (inverter_id,),
        ).fetchone()
    analysis_end = normalized_end or (site_end_row["end_at"] if site_end_row else None)
    events = detect_anomaly_events(
        inverter, readings, analysis_end=analysis_end,
        last_known_timestamp=history_row["last_at"] if history_row else None,
        has_historical_telemetry=bool(history_row and history_row["samples"]),
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    alert_ids: list[str] = []
    with connect() as conn:
        for event in events:
            identity = f"{inverter_id}|{event['anomaly_category']}|{event['start_timestamp']}|{METHOD_VERSION}"
            alert_id = f"inv-alert-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex[:12]}"
            existing = conn.execute(
                """SELECT * FROM inverter_alerts WHERE inverter_id=? AND anomaly_category=? AND detection_method=?
                   AND COALESCE(last_abnormal_timestamp,end_timestamp) >= ?
                   AND COALESCE(onset_timestamp,start_timestamp) <= ?
                   ORDER BY created_at LIMIT 1""",
                (inverter_id, event["anomaly_category"], METHOD_VERSION, event["onset_timestamp"], event["last_abnormal_timestamp"]),
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
                     inverter["data_source"], event["onset_timestamp"], event["detection_eligible_timestamp"],
                     event["last_abnormal_timestamp"], event.get("recovery_timestamp"),
                     event["active_duration_minutes"], event["excluded_duration_minutes"]),
                )
            alert_ids.append(alert_id)
        conn.commit()
    return {
        "inverter_id": inverter_id, "analysis_start": normalized_start or (readings[0]["timestamp"] if readings else None),
        "analysis_end": analysis_end, "events_detected": len(events), "alert_ids": alert_ids,
        "method": METHOD_VERSION, "idempotent": True,
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


@app.get("/api/inverter-alerts/{alert_id}")
def inverter_alert_detail(alert_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inverter_alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Inverter alert not found")
    return _decode_alert(dict(row))


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
        blocks = rows_to_dicts(
            conn.execute(
                """
                SELECT block_name AS id,
                       COUNT(DISTINCT row_no) AS rows,
                       COUNT(*) AS findings,
                       ROUND(SUM(affected_kw),2) AS affected_kw
                FROM anomalies WHERE site_id = ?
                GROUP BY block_name ORDER BY block_name
                """,
                (site_id,),
            )
        )
    return {
        "site": site,
        "blocks": blocks,
        "hierarchy": {
            "site": site["name"],
            "inverters": 12,
            "blocks": 4,
            "rows": 72,
            "strings": 864,
            "modules": 149760,
        },
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


@app.post("/api/reset")
def reset_demo() -> dict[str, Any]:
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

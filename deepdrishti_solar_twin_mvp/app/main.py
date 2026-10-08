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


def get_site_or_404(site_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return dict(row)


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
    site["inverters"] = [
        {"id": f"INV-{i:02d}", "yield": val, "status": "Watch" if val < 90 else "Healthy"}
        for i, val in enumerate([97.2, 96.8, 94.7, 88.9, 92.4, 95.8, 36.3, 93.9, 97.1, 90.7, 95.4, 89.5], start=1)
    ]
    return site


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

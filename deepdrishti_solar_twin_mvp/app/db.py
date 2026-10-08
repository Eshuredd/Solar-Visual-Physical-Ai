from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SOLAR_TWIN_DB", ROOT / "data" / "solar_twin.db"))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def init_db(reset: bool = False) -> None:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                capacity_mw REAL NOT NULL,
                status TEXT NOT NULL,
                last_inspection TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                performance_ratio REAL NOT NULL,
                owner TEXT NOT NULL,
                commissioned TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inspections (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                inspection_type TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                status TEXT NOT NULL,
                findings INTEGER NOT NULL,
                affected_kw REAL NOT NULL,
                annual_kwh_loss REAL NOT NULL,
                annual_revenue_loss REAL NOT NULL,
                source TEXT NOT NULL,
                weather TEXT NOT NULL,
                model_version TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS anomalies (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                inspection_id TEXT NOT NULL REFERENCES inspections(id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL,
                delta_t REAL,
                normalized_delta_t REAL,
                affected_kw REAL NOT NULL,
                annual_kwh_loss REAL NOT NULL,
                annual_revenue_loss REAL NOT NULL,
                block_name TEXT NOT NULL,
                row_no INTEGER NOT NULL,
                module_no INTEGER NOT NULL,
                map_x REAL NOT NULL,
                map_y REAL NOT NULL,
                description TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                first_detected TEXT NOT NULL,
                rgb_image TEXT NOT NULL,
                thermal_image TEXT NOT NULL,
                history TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                anomaly_id TEXT REFERENCES anomalies(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                owner TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL,
                priority TEXT NOT NULL,
                requested_action TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM sites").fetchone()["c"]
        if count == 0:
            seed_database(conn)


def _history(asset_id: str, status: str, detected: str) -> str:
    items = [
        {
            "at": detected,
            "event": "Anomaly detected by DeepDrishti Visual AI",
            "actor": "Vision Engine v0.3",
        },
        {
            "at": detected,
            "event": f"Linked to physical asset {asset_id}",
            "actor": "Asset Association Service",
        },
    ]
    if status not in {"Detected", "Verified"}:
        items.append(
            {
                "at": (date.fromisoformat(detected) + timedelta(days=1)).isoformat(),
                "event": f"Status changed to {status}",
                "actor": "O&M Coordinator",
            }
        )
    return json.dumps(items)


def seed_database(conn: sqlite3.Connection) -> None:
    sites = [
        (
            "site-001",
            "Sunridge Solar Park",
            "Rajasthan",
            82.4,
            "Attention",
            "2026-08-09",
            27.1672,
            70.9566,
            91.7,
            "Aarohan Renewables",
            "2022-03-14",
        ),
        (
            "site-002",
            "Kaveri Solar One",
            "Karnataka",
            54.8,
            "Healthy",
            "2026-08-06",
            15.3173,
            75.7139,
            96.2,
            "Vistara Energy",
            "2023-01-19",
        ),
        (
            "site-003",
            "Aravali PV Cluster",
            "Gujarat",
            126.2,
            "Attention",
            "2026-07-29",
            23.0225,
            72.5714,
            89.8,
            "Northstar Infrastructure",
            "2021-11-08",
        ),
        (
            "site-004",
            "Deccan Sunfield",
            "Telangana",
            71.5,
            "Healthy",
            "2026-08-02",
            17.3850,
            78.4867,
            95.4,
            "Helios Gridworks",
            "2024-02-22",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO sites (
            id, name, region, capacity_mw, status, last_inspection, latitude,
            longitude, performance_ratio, owner, commissioned
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        sites,
    )

    inspections = [
        (
            "ins-thermal-aug",
            "site-001",
            "Thermal Health Survey — August",
            "Thermal + RGB",
            "2026-08-09T10:30:00",
            "Reviewed",
            24,
            17.9,
            26780.0,
            184782.0,
            "Drone upload",
            "Clear • 33°C • 840 W/m²",
            "thermal-v0.3",
        ),
        (
            "ins-visual-jul",
            "site-001",
            "Tracker & Vegetation Survey",
            "RGB visual",
            "2026-07-18T09:20:00",
            "Reviewed",
            13,
            8.2,
            11840.0,
            81696.0,
            "Autonomous route",
            "Clear • 31°C • 790 W/m²",
            "visual-v0.2",
        ),
        (
            "ins-wiring-jun",
            "site-001",
            "Back-of-panel Wiring QA",
            "Oblique RGB + thermal",
            "2026-06-25T15:10:00",
            "Reviewed",
            8,
            3.7,
            5240.0,
            36156.0,
            "Technician capture",
            "Cloudy • 29°C • 520 W/m²",
            "bos-v0.1",
        ),
        (
            "ins-thermal-may",
            "site-001",
            "Thermal Health Survey — May",
            "Thermal + RGB",
            "2026-05-14T10:05:00",
            "Archived",
            17,
            11.4,
            17650.0,
            121785.0,
            "Drone upload",
            "Clear • 35°C • 910 W/m²",
            "thermal-v0.2",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO inspections (
            id, site_id, name, inspection_type, captured_at, status, findings,
            affected_kw, annual_kwh_loss, annual_revenue_loss, source, weather,
            model_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        inspections,
    )

    random.seed(42)
    templates = [
        ("Cell Hotspot", "Thermal", "High", 0.94, 21.7, 18.9, 0.42, "/static/assets/rgb_hotspot.png", "/static/assets/thermal_hotspot.png", "Inspect module and bypass diode; replace if repeat test confirms the thermal signature."),
        ("String Anomaly", "Electrical", "Critical", 0.97, 16.4, 14.1, 4.95, "/static/assets/rgb_hotspot.png", "/static/assets/thermal_hotspot.png", "Check string continuity, combiner input and connector integrity immediately."),
        ("Vegetation Encroachment", "Environmental", "Medium", 0.91, None, None, 0.66, "/static/assets/rgb_vegetation.png", "/static/assets/rgb_vegetation.png", "Schedule targeted vegetation clearance and verify row shading after completion."),
        ("Tracker Misalignment", "Mechanical", "High", 0.89, None, None, 2.31, "/static/assets/rgb_tracker.png", "/static/assets/rgb_tracker.png", "Inspect tracker controller, torque tube and drive components; recalibrate row angle."),
        ("Soiling Concentration", "Environmental", "Low", 0.86, None, None, 0.28, "/static/assets/rgb_hotspot.png", "/static/assets/rgb_hotspot.png", "Include affected row in the next targeted cleaning route."),
        ("Wiring Damage", "Balance of System", "High", 0.92, 11.2, 9.5, 0.91, "/static/assets/rgb_wiring.png", "/static/assets/thermal_hotspot.png", "De-energize as required, repair cable management and replace damaged connector or loom."),
        ("Module Crack", "Module Health", "High", 0.88, 8.7, 7.1, 0.52, "/static/assets/rgb_hotspot.png", "/static/assets/thermal_hotspot.png", "Verify crack under close visual inspection and replace module if power or safety risk is confirmed."),
        ("Diode Hotspot", "Thermal", "Medium", 0.93, 13.8, 11.6, 0.31, "/static/assets/rgb_hotspot.png", "/static/assets/thermal_hotspot.png", "Test bypass diode and module IV response during the next service visit."),
    ]
    coords = [
        (17, 20), (31, 26), (43, 18), (62, 23), (78, 30), (88, 18),
        (14, 45), (27, 52), (40, 44), (59, 48), (73, 56), (86, 43),
        (13, 73), (26, 82), (42, 70), (58, 78), (73, 72), (87, 84),
        (21, 34), (49, 31), (67, 38), (34, 66), (65, 65), (81, 65),
    ]
    statuses = ["Detected", "Verified", "Assigned", "In Progress", "Resolved"]
    anomalies: list[tuple[Any, ...]] = []
    for idx, (x, y) in enumerate(coords, start=1):
        t = templates[(idx - 1) % len(templates)]
        anomaly_type, category, priority, confidence, delta_t, ndt, affected_kw, rgb, thermal, action = t
        if idx in {2, 7, 14, 20}:
            priority = "Critical"
        status = statuses[(idx - 1) % len(statuses)]
        block = "B1" if x < 50 and y < 50 else "B2" if x >= 50 and y < 50 else "C1" if x < 50 else "C2"
        row_no = max(1, min(18, int((y % 50) / 2.7) + 1))
        module_no = max(1, min(32, int((x % 50) / 1.55) + 1))
        asset_id = f"SRP-{block}-R{row_no:02d}-M{module_no:02d}"
        annual_kwh = round(affected_kw * 5.25 * 365 * (0.62 + 0.08 * random.random()), 1)
        annual_revenue = round(annual_kwh * 6.9, 0)
        detected = (date(2026, 8, 9) - timedelta(days=(idx % 7))).isoformat()
        anomalies.append(
            (
                f"an-{idx:03d}",
                "site-001",
                "ins-thermal-aug" if idx <= 16 else "ins-visual-jul",
                asset_id,
                anomaly_type,
                category,
                priority,
                status,
                confidence,
                delta_t,
                ndt,
                affected_kw,
                annual_kwh,
                annual_revenue,
                block,
                row_no,
                module_no,
                x,
                y,
                f"{anomaly_type} detected and localized to {asset_id}. Evidence has been retained in the asset history.",
                action,
                detected,
                rgb,
                thermal,
                _history(asset_id, status, detected),
                "",
            )
        )
    conn.executemany(
        """
        INSERT INTO anomalies (
            id, site_id, inspection_id, asset_id, anomaly_type, category,
            priority, status, confidence, delta_t, normalized_delta_t,
            affected_kw, annual_kwh_loss, annual_revenue_loss, block_name,
            row_no, module_no, map_x, map_y, description, recommended_action,
            first_detected, rgb_image, thermal_image, history, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        anomalies,
    )

    now = datetime(2026, 8, 12, 10, 0, 0)
    tasks = [
        (
            "task-001", "site-001", "an-002", "Restore String B1-R10", "Aditi Rao",
            "2026-08-13", "In Progress", "Critical",
            "Check string continuity and combiner input; repair failed connection.",
            "Technician dispatched with thermal evidence attached.", now.isoformat(), now.isoformat(),
        ),
        (
            "task-002", "site-001", "an-004", "Recalibrate Tracker B2-R09", "Vikram Shah",
            "2026-08-16", "Assigned", "High",
            "Inspect tracker drive and restore expected row angle.",
            "Coordinate with tracker OEM if controller reset fails.", now.isoformat(), now.isoformat(),
        ),
        (
            "task-003", "site-001", "an-007", "Clear vegetation near C1-R17", "Neha Singh",
            "2026-08-11", "Overdue", "Critical",
            "Clear vegetation and document post-work shading condition.",
            "Access road is clear. Use field app to upload after-photo.", now.isoformat(), now.isoformat(),
        ),
        (
            "task-004", "site-001", "an-011", "Inspect module crack C2-R03", "Aditi Rao",
            "2026-08-18", "Assigned", "High",
            "Perform close visual and IV check; replace if confirmed.",
            "Warranty documentation may be required.", now.isoformat(), now.isoformat(),
        ),
    ]
    conn.executemany(
        """
        INSERT INTO tasks (
            id, site_id, anomaly_id, title, owner, due_date, status, priority,
            requested_action, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tasks,
    )
    conn.commit()

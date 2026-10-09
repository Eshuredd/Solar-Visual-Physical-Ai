from __future__ import annotations

import json
import math
import os
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
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

            CREATE TABLE IF NOT EXISTS inverters (
                inverter_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                rated_ac_power_kw REAL NOT NULL CHECK(rated_ac_power_kw > 0),
                manufacturer TEXT,
                model TEXT,
                operational_status TEXT NOT NULL,
                block_info TEXT,
                data_source TEXT NOT NULL DEFAULT 'synthetic_demo',
                UNIQUE(site_id, name)
            );

            CREATE TABLE IF NOT EXISTS inverter_telemetry (
                inverter_id TEXT NOT NULL REFERENCES inverters(inverter_id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                dc_power_kw REAL NOT NULL CHECK(dc_power_kw >= 0),
                ac_power_kw REAL NOT NULL CHECK(ac_power_kw >= 0),
                dc_voltage_v REAL NOT NULL CHECK(dc_voltage_v >= 0 AND dc_voltage_v <= 2000),
                dc_current_a REAL NOT NULL CHECK(dc_current_a >= 0),
                ac_voltage_v REAL NOT NULL CHECK(ac_voltage_v >= 0 AND ac_voltage_v <= 1500),
                ac_current_a REAL NOT NULL CHECK(ac_current_a >= 0),
                inverter_temperature_c REAL NOT NULL CHECK(inverter_temperature_c >= -40 AND inverter_temperature_c <= 150),
                ambient_temperature_c REAL NOT NULL CHECK(ambient_temperature_c >= -60 AND ambient_temperature_c <= 80),
                irradiance_w_m2 REAL CHECK(irradiance_w_m2 >= 0 AND irradiance_w_m2 <= 1500),
                operating_state TEXT NOT NULL,
                data_source TEXT NOT NULL,
                PRIMARY KEY(inverter_id, timestamp)
            );

            CREATE TABLE IF NOT EXISTS inverter_alerts (
                alert_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                inverter_id TEXT NOT NULL REFERENCES inverters(inverter_id) ON DELETE CASCADE,
                anomaly_category TEXT NOT NULL,
                anomaly_subtype TEXT,
                start_timestamp TEXT NOT NULL,
                end_timestamp TEXT NOT NULL,
                severity TEXT NOT NULL,
                detection_method TEXT NOT NULL,
                detection_score REAL NOT NULL,
                expected_measurements TEXT NOT NULL,
                observed_measurements TEXT NOT NULL,
                contributing_telemetry_refs TEXT NOT NULL,
                explanation TEXT NOT NULL,
                recommended_investigation TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL DEFAULT 'Open',
                lifecycle_history TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data_provenance TEXT NOT NULL,
                UNIQUE(inverter_id, anomaly_category, start_timestamp, detection_method)
            );

            CREATE TABLE IF NOT EXISTS inverter_scenarios (
                scenario_id TEXT PRIMARY KEY,
                inverter_id TEXT NOT NULL REFERENCES inverters(inverter_id) ON DELETE CASCADE,
                scenario_type TEXT NOT NULL,
                start_timestamp TEXT NOT NULL,
                end_timestamp TEXT NOT NULL,
                expected_alert_category TEXT,
                description TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT 'synthetic_ground_truth'
            );

            CREATE INDEX IF NOT EXISTS idx_inverters_site ON inverters(site_id);
            CREATE INDEX IF NOT EXISTS idx_inverter_telemetry_timestamp ON inverter_telemetry(timestamp);
            CREATE INDEX IF NOT EXISTS idx_inverter_telemetry_state ON inverter_telemetry(inverter_id, operating_state, timestamp);
            CREATE INDEX IF NOT EXISTS idx_inverter_alerts_site ON inverter_alerts(site_id, start_timestamp);
            CREATE INDEX IF NOT EXISTS idx_inverter_alerts_inverter ON inverter_alerts(inverter_id, lifecycle_status, start_timestamp);
            CREATE INDEX IF NOT EXISTS idx_inverter_alerts_category ON inverter_alerts(anomaly_category, severity, lifecycle_status);
            """
        )
        migrate_inverter_alerts(conn)
        count = conn.execute("SELECT COUNT(*) AS c FROM sites").fetchone()["c"]
        if count == 0:
            seed_database(conn)
        if conn.execute("SELECT 1 FROM sites WHERE id = 'site-001'").fetchone():
            seed_demo_inverters(conn)
            seed_phase2_scenarios(conn)


def migrate_inverter_alerts(conn: sqlite3.Connection) -> None:
    """Add Phase 2A.1 event semantics without rebuilding or deleting alert data."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(inverter_alerts)")}
    additions = {
        "onset_timestamp": "TEXT",
        "detection_eligible_timestamp": "TEXT",
        "last_abnormal_timestamp": "TEXT",
        "recovery_timestamp": "TEXT",
        "active_duration_minutes": "INTEGER",
        "excluded_duration_minutes": "INTEGER",
        "acknowledgment_timestamp": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE inverter_alerts ADD COLUMN {name} {sql_type}")
    conn.execute(
        """UPDATE inverter_alerts SET
             onset_timestamp=COALESCE(onset_timestamp,start_timestamp),
             detection_eligible_timestamp=COALESCE(detection_eligible_timestamp,start_timestamp),
             last_abnormal_timestamp=COALESCE(last_abnormal_timestamp,end_timestamp),
             active_duration_minutes=COALESCE(active_duration_minutes,
               CAST((julianday(end_timestamp)-julianday(start_timestamp))*1440 AS INTEGER)+15),
             excluded_duration_minutes=COALESCE(excluded_duration_minutes,0)
           WHERE onset_timestamp IS NULL OR detection_eligible_timestamp IS NULL
              OR last_abnormal_timestamp IS NULL OR active_duration_minutes IS NULL"""
    )
    conn.commit()


def seed_demo_inverters(conn: sqlite3.Connection) -> None:
    """Idempotently install deterministic synthetic SCADA-like demo records."""
    blocks = ["B1", "B1", "B1", "B2", "B2", "B2", "C1", "C1", "C1", "C2", "C2", "C2"]
    records = [
        (f"site-001-INV-{number:02d}", "site-001", f"INV-{number:02d}", 6800.0,
         "Demo Power Systems", "DD-Central-6800", "Operational", blocks[number - 1], "synthetic_demo")
        for number in range(1, 13)
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO inverters
           (inverter_id, site_id, name, rated_ac_power_kw, manufacturer, model,
            operational_status, block_info, data_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        records,
    )
    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM inverter_telemetry WHERE inverter_id LIKE 'site-001-INV-%'"
    ).fetchone()["c"]
    if existing >= 12 * 7 * 96:
        conn.commit()
        return

    start = datetime(2026, 8, 6, tzinfo=timezone.utc)
    telemetry: list[tuple[Any, ...]] = []
    for inverter_number in range(1, 13):
        inverter_id = f"site-001-INV-{inverter_number:02d}"
        unit_factor = 0.982 + inverter_number * 0.0022
        for sample in range(7 * 96):
            timestamp = start + timedelta(minutes=15 * sample)
            hour = timestamp.hour + timestamp.minute / 60
            daylight = 6.0 <= hour <= 18.0
            solar_shape = max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0)) if daylight else 0.0
            cloud_factor = 0.91 + 0.055 * math.sin(sample * 0.173) + 0.025 * math.sin(sample * 0.047 + 1.4)
            irradiance = max(0.0, min(1050.0, 980.0 * solar_shape * cloud_factor))
            ambient = 23.0 + 11.0 * max(0.0, math.sin(math.pi * (hour - 8.0) / 14.0)) + 1.1 * math.sin(sample * 0.031)
            inverter_temp = ambient + irradiance / 1000.0 * 31.0 + (inverter_number - 6.5) * 0.12
            operating_state = "Night"
            abnormal_factor = 1.0
            if irradiance >= 100:
                operating_state = "Running"
                if inverter_number == 7:
                    abnormal_factor = 0.63
                    operating_state = "Derated"
                elif inverter_number == 12 and sample % 41 in {0, 1, 2, 3}:
                    abnormal_factor = 0.42
                    operating_state = "Derated"
            elif irradiance > 0:
                operating_state = "Startup"

            temperature_factor = max(0.78, 1.0 - max(0.0, inverter_temp - 25.0) * 0.0035)
            expected_ac = min(6800.0, 6800.0 * irradiance / 1000.0 * temperature_factor)
            ripple = 1.0 + 0.008 * math.sin(sample * 0.29 + inverter_number)
            ac_power = max(0.0, min(6800.0, expected_ac * unit_factor * abnormal_factor * ripple))
            efficiency = max(0.925, 0.978 - max(0.0, inverter_temp - 55.0) * 0.00035)
            dc_power = ac_power / efficiency if ac_power else 0.0
            dc_voltage = 0.0 if irradiance <= 0 else 930.0 + 85.0 * solar_shape - 0.9 * max(0.0, inverter_temp - 25.0)
            dc_current = dc_power * 1000.0 / dc_voltage if dc_voltage else 0.0
            ac_voltage = 0.0 if ac_power <= 0 else 690.0 + 3.0 * math.sin(sample * 0.11 + inverter_number)
            ac_current = ac_power * 1000.0 / (math.sqrt(3) * ac_voltage * 0.99) if ac_voltage else 0.0
            telemetry.append((
                inverter_id, timestamp.isoformat().replace("+00:00", "Z"), round(dc_power, 3),
                round(ac_power, 3), round(dc_voltage, 3), round(dc_current, 3),
                round(ac_voltage, 3), round(ac_current, 3), round(inverter_temp, 3),
                round(ambient, 3), round(irradiance, 3), operating_state, "synthetic_demo_v1",
            ))
    conn.executemany(
        """INSERT OR IGNORE INTO inverter_telemetry
           (inverter_id, timestamp, dc_power_kw, ac_power_kw, dc_voltage_v,
            dc_current_a, ac_voltage_v, ac_current_a, inverter_temperature_c,
            ambient_temperature_c, irradiance_w_m2, operating_state, data_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        telemetry,
    )
    conn.commit()


def seed_phase2_scenarios(conn: sqlite3.Connection) -> None:
    """Inject repeatable scenario windows; labels remain in a separate truth table."""
    scenarios = [
        ("scenario-normal", "site-001-INV-01", "normal_operation", "2026-08-06T00:00:00Z", "2026-08-12T23:45:00Z", None, "Normal reference operation."),
        ("scenario-pv", "site-001-INV-02", "pv_side_underperformance", "2026-08-07T09:00:00Z", "2026-08-07T13:00:00Z", "pv_side_underperformance", "Sustained DC-side reduction with consistent conversion."),
        ("scenario-ac", "site-001-INV-03", "ac_conversion_underperformance", "2026-08-08T10:00:00Z", "2026-08-08T14:00:00Z", "ac_conversion_underperformance", "DC input remains available while AC conversion falls."),
        ("scenario-hot", "site-001-INV-04", "inverter_overheating", "2026-08-09T10:00:00Z", "2026-08-09T15:00:00Z", "inverter_overheating", "Load-adjusted inverter temperature elevation."),
        ("scenario-shutdown", "site-001-INV-05", "temporary_shutdown", "2026-08-10T11:00:00Z", "2026-08-10T12:30:00Z", "unexpected_shutdown", "Unexplained daytime zero-output interval followed by recovery."),
        ("scenario-curtail", "site-001-INV-06", "grid_curtailment", "2026-08-07T12:00:00Z", "2026-08-07T14:00:00Z", None, "Known grid curtailment exclusion window."),
        ("scenario-persistent", "site-001-INV-07", "persistent_pv_underperformance", "2026-08-06T07:00:00Z", "2026-08-12T17:00:00Z", "pv_side_underperformance", "Persistent PV-side reduction; root cause intentionally unconfirmed."),
        ("scenario-missing", "site-001-INV-08", "missing_telemetry", "2026-08-08T10:00:00Z", "2026-08-08T12:00:00Z", "data_quality", "Missing timestamp interval."),
        ("scenario-invalid", "site-001-INV-09", "invalid_measurement", "2026-08-09T12:00:00Z", "2026-08-09T12:00:00Z", "data_quality", "Physically inconsistent AC/DC measurement."),
        ("scenario-stale", "site-001-INV-10", "stale_telemetry", "2026-08-12T20:00:00Z", "2026-08-12T23:45:00Z", "data_quality", "Telemetry stream stops before analysis end."),
        ("scenario-recovery", "site-001-INV-11", "recovery_after_underperformance", "2026-08-06T09:00:00Z", "2026-08-06T11:00:00Z", "pv_side_underperformance", "Bounded PV-side reduction with later signal recovery."),
        ("scenario-intermittent", "site-001-INV-12", "intermittent_derating", "2026-08-06T07:00:00Z", "2026-08-12T17:00:00Z", "intermittent_derating", "Recurring short derating episodes."),
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO inverter_scenarios
           (scenario_id, inverter_id, scenario_type, start_timestamp, end_timestamp,
            expected_alert_category, description) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        scenarios,
    )
    # Only untouched v1 records are transformed, keeping this migration idempotent.
    def scale(inverter: str, start: str, end: str, dc: float = 1.0, ac: float = 1.0, state: str | None = None) -> None:
        conn.execute(
            """UPDATE inverter_telemetry SET
                   dc_power_kw = dc_power_kw * ?, dc_current_a = dc_current_a * ?,
                   ac_power_kw = ac_power_kw * ?, ac_current_a = ac_current_a * ?,
                   operating_state = COALESCE(?, operating_state), data_source = 'synthetic_demo_v2'
               WHERE inverter_id = ? AND timestamp BETWEEN ? AND ? AND data_source = 'synthetic_demo_v1'""",
            (dc, dc, ac, ac, state, inverter, start, end),
        )
    scale("site-001-INV-02", "2026-08-07T09:00:00Z", "2026-08-07T13:00:00Z", .62, .62)
    scale("site-001-INV-03", "2026-08-08T10:00:00Z", "2026-08-08T14:00:00Z", 1.0, .70)
    conn.execute(
        """UPDATE inverter_telemetry SET inverter_temperature_c = inverter_temperature_c + 25,
                  data_source = 'synthetic_demo_v2'
           WHERE inverter_id = 'site-001-INV-04' AND timestamp BETWEEN '2026-08-09T10:00:00Z' AND '2026-08-09T15:00:00Z'
             AND data_source = 'synthetic_demo_v1'"""
    )
    conn.execute(
        """UPDATE inverter_telemetry SET dc_power_kw=0, ac_power_kw=0, dc_current_a=0,
                  ac_current_a=0, operating_state='Running', data_source='synthetic_demo_v2'
           WHERE inverter_id='site-001-INV-05' AND timestamp BETWEEN '2026-08-10T11:00:00Z' AND '2026-08-10T12:30:00Z'
             AND data_source='synthetic_demo_v1'"""
    )
    scale("site-001-INV-06", "2026-08-07T12:00:00Z", "2026-08-07T14:00:00Z", .50, .50, "Curtailment")
    scale("site-001-INV-11", "2026-08-06T09:00:00Z", "2026-08-06T11:00:00Z", .64, .64)
    conn.execute(
        "DELETE FROM inverter_telemetry WHERE inverter_id='site-001-INV-08' AND timestamp BETWEEN '2026-08-08T10:00:00Z' AND '2026-08-08T12:00:00Z'"
    )
    conn.execute(
        """UPDATE inverter_telemetry SET ac_power_kw=dc_power_kw*1.08,
                  ac_current_a=(dc_power_kw*1.08*1000)/(1.7320508075688772*ac_voltage_v*.99),
                  data_source='synthetic_demo_v2'
           WHERE inverter_id='site-001-INV-09' AND timestamp='2026-08-09T12:00:00Z'
             AND data_source='synthetic_demo_v1'"""
    )
    conn.execute(
        "DELETE FROM inverter_telemetry WHERE inverter_id='site-001-INV-10' AND timestamp > '2026-08-12T20:00:00Z'"
    )
    conn.commit()


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

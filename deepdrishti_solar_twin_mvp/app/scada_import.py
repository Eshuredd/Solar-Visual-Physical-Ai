"""Validated, transactional CSV onboarding for fictional/real SCADA exports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 20_000
SCHEMA_VERSION = "canonical-scada-v1"
REQUIRED_FIELDS = ("site_id", "inverter_id", "timestamp", "ac_power")
OPTIONAL_NUMERIC_FIELDS = (
    "dc_power", "ac_voltage", "ac_current", "dc_voltage", "dc_current",
    "inverter_temperature", "ambient_temperature", "irradiance",
)
POWER_FIELDS = {"ac_power", "dc_power"}
SUPPORTED_POWER_UNITS = {"W": .001, "kW": 1.0, "MW": 1000.0}
RANGES = {
    "ac_power_kw": (0, 100_000), "dc_power_kw": (0, 100_000),
    "ac_voltage_v": (0, 1_500), "dc_voltage_v": (0, 2_000),
    "ac_current_a": (0, 100_000), "dc_current_a": (0, 100_000),
    "inverter_temperature_c": (-40, 150), "ambient_temperature_c": (-60, 80),
    "irradiance_w_m2": (0, 1_500),
}


class ImportValidationError(ValueError):
    pass


def _parse_number(value: str | None, field: str, required: bool = False) -> tuple[float | None, str | None]:
    if value is None or not value.strip():
        return (None, f"{field} is required") if required else (None, None)
    try:
        number = float(value)
    except ValueError:
        return None, f"{field} must be numeric"
    if not math.isfinite(number):
        return None, f"{field} must be finite"
    return number, None


def _timestamp(value: str, timezone_name: str) -> tuple[str | None, str | None, str | None]:
    if not value.strip():
        return None, None, "timestamp is required"
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            original_zone = timezone_name
        else:
            original_zone = str(parsed.utcoffset())
        utc = parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return utc, original_zone, None
    except (ValueError, ZoneInfoNotFoundError):
        return None, None, f"timestamp is invalid or timezone {timezone_name!r} is unsupported"


def _mapped(row: dict[str, str], mapping: dict[str, str], field: str) -> str | None:
    column = mapping.get(field)
    return row.get(column) if column else None


def _normalize_row(
    row: dict[str, str], mapping: dict[str, str], units: dict[str, str], timezone_name: str,
    target_site: str, source_system: str, known_inverters: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    site_id = (_mapped(row, mapping, "site_id") or "").strip()
    inverter_id = (_mapped(row, mapping, "inverter_id") or "").strip()
    original_timestamp = (_mapped(row, mapping, "timestamp") or "").strip()
    if not site_id:
        reasons.append("site_id is required")
    elif site_id != target_site:
        reasons.append(f"cross-site record {site_id!r} does not match selected site {target_site!r}")
    if not inverter_id:
        reasons.append("inverter_id is required")
    elif inverter_id not in known_inverters:
        reasons.append(f"unknown inverter identifier {inverter_id!r}")
    elif known_inverters[inverter_id] != target_site:
        reasons.append("inverter belongs to a different site")
    timestamp_utc, original_timezone, timestamp_error = _timestamp(original_timestamp, timezone_name)
    if timestamp_error:
        reasons.append(timestamp_error)

    numbers: dict[str, float | None] = {}
    for field in ("ac_power", *OPTIONAL_NUMERIC_FIELDS):
        number, error = _parse_number(_mapped(row, mapping, field), field, required=field == "ac_power")
        if error:
            reasons.append(error)
        if number is not None and field in POWER_FIELDS:
            unit = units.get(field, "kW")
            if unit not in SUPPORTED_POWER_UNITS:
                reasons.append(f"unsupported {field} unit {unit!r}")
            else:
                number *= SUPPORTED_POWER_UNITS[unit]
                if unit != "kW":
                    warnings.append(f"{field} converted from {unit} to kW")
        numbers[field] = number

    canonical = {
        "ac_power_kw": numbers["ac_power"], "dc_power_kw": numbers["dc_power"],
        "ac_voltage_v": numbers["ac_voltage"], "ac_current_a": numbers["ac_current"],
        "dc_voltage_v": numbers["dc_voltage"], "dc_current_a": numbers["dc_current"],
        "inverter_temperature_c": numbers["inverter_temperature"],
        "ambient_temperature_c": numbers["ambient_temperature"],
        "irradiance_w_m2": numbers["irradiance"],
    }
    for field, value in canonical.items():
        if value is not None:
            low, high = RANGES[field]
            if not low <= value <= high:
                reasons.append(f"{field} must be between {low} and {high}")
    if canonical["ac_power_kw"] is not None and canonical["dc_power_kw"] is not None:
        if canonical["ac_power_kw"] > canonical["dc_power_kw"] * 1.02 + .01:
            reasons.append("AC power materially exceeds DC power")
    missing_optional = [field for field, value in canonical.items() if value is None]
    normalized = {
        "site_id": site_id, "inverter_id": inverter_id, "timestamp_utc": timestamp_utc,
        "original_timestamp": original_timestamp, "original_timezone": original_timezone,
        **canonical,
        "operating_state": (_mapped(row, mapping, "operating_state") or "").strip() or None,
        "grid_state": (_mapped(row, mapping, "grid_state") or "").strip() or None,
        "source_system": source_system,
        "source_record_id": (_mapped(row, mapping, "source_record_id") or "").strip() or None,
        "data_provenance": "imported_scada",
        "data_quality_status": "partial" if missing_optional else "complete",
        "unit_warnings": warnings,
    }
    return (None if reasons else normalized), reasons, warnings


def _quality_report(records: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    timestamps = sorted(_timestamp_value(item["timestamp_utc"]) for item in records)
    per_inverter: dict[str, list[datetime]] = defaultdict(list)
    for item in records:
        per_inverter[item["inverter_id"]].append(_timestamp_value(item["timestamp_utc"]))
    gaps = irregular = 0
    cadence_values: list[float] = []
    for values in per_inverter.values():
        values.sort()
        diffs = [(right - left).total_seconds() / 60 for left, right in zip(values, values[1:])]
        if not diffs:
            continue
        cadence = statistics.median(diffs)
        cadence_values.append(cadence)
        gaps += sum(max(0, round(diff / cadence) - 1) for diff in diffs if cadence > 0 and diff > cadence * 1.5)
        irregular += sum(abs(diff - cadence) > max(1, cadence * .1) for diff in diffs)
    latest = max(timestamps) if timestamps else None
    return {
        "time_coverage": {
            "start": min(timestamps).isoformat().replace("+00:00", "Z") if timestamps else None,
            "end": latest.isoformat().replace("+00:00", "Z") if latest else None,
        },
        "sampling_cadence_minutes": round(statistics.median(cadence_values), 2) if cadence_values else None,
        "missing_intervals": gaps, "timestamp_irregularities": irregular,
        "partial_measurement_records": sum(item["data_quality_status"] == "partial" for item in records),
        "data_freshness_hours": round((datetime.now(timezone.utc) - latest).total_seconds() / 3600, 2) if latest else None,
        "unit_conversion_warnings": sorted(set(warnings)),
    }


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def preview_scada_csv(
    conn: sqlite3.Connection, *, site_id: str, filename: str, content: bytes,
    mapping: dict[str, str], units: dict[str, str], timezone_name: str, source_system: str,
) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportValidationError(f"CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if Path(filename).suffix.lower() != ".csv":
        raise ImportValidationError("Only .csv files are accepted")
    missing_mapping = [field for field in REQUIRED_FIELDS if not mapping.get(field)]
    if missing_mapping:
        raise ImportValidationError(f"Missing required column mappings: {', '.join(missing_mapping)}")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError("CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportValidationError("CSV header is missing")
    unknown_columns = [column for column in mapping.values() if column and column not in reader.fieldnames]
    if unknown_columns:
        raise ImportValidationError(f"Mapped columns not found: {', '.join(unknown_columns)}")
    rows = list(reader)
    if len(rows) > MAX_CSV_ROWS:
        raise ImportValidationError(f"CSV exceeds the {MAX_CSV_ROWS:,} record preview limit")
    digest = hashlib.sha256(content).hexdigest()
    existing_import = conn.execute(
        "SELECT * FROM data_imports WHERE site_id=? AND import_type='scada_csv' AND file_sha256=?", (site_id, digest)
    ).fetchone()
    if existing_import:
        result = json.loads(existing_import["report_json"])
        return {**result, "import_id": existing_import["import_id"], "status": existing_import["status"], "already_imported": existing_import["status"] == "committed"}
    known = {row["inverter_id"]: row["site_id"] for row in conn.execute("SELECT inverter_id,site_id FROM inverters")}
    accepted: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    source_ids: dict[str, tuple[int, tuple[str, str]]] = {}
    rejected: dict[int, tuple[dict[str, str], list[str]]] = {}
    duplicates = 0
    all_warnings: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        normalized, reasons, warnings = _normalize_row(row, mapping, units, timezone_name, site_id, source_system, known)
        all_warnings.extend(warnings)
        if reasons or normalized is None:
            rejected[row_number] = (row, reasons)
            continue
        key = (normalized["inverter_id"], normalized["timestamp_utc"])
        if key in accepted:
            prior_row, prior = accepted[key]
            if {k: v for k, v in prior.items() if k != "source_record_id"} == {k: v for k, v in normalized.items() if k != "source_record_id"}:
                duplicates += 1
                continue
            rejected[prior_row] = (rows[prior_row - 2], ["conflicting duplicate timestamp within file"])
            rejected[row_number] = (row, ["conflicting duplicate timestamp within file"])
            del accepted[key]
            continue
        source_id = normalized.get("source_record_id")
        if source_id and source_id in source_ids:
            prior_row, prior_key = source_ids[source_id]
            rejected[prior_row] = (rows[prior_row - 2], ["conflicting duplicate source_record_id within file"])
            rejected[row_number] = (row, ["conflicting duplicate source_record_id within file"])
            accepted.pop(prior_key, None)
            continue
        database_row = conn.execute(
            "SELECT * FROM imported_inverter_telemetry WHERE site_id=? AND inverter_id=? AND timestamp_utc=?",
            (site_id, *key),
        ).fetchone()
        if database_row:
            comparable = (
                "ac_power_kw", "dc_power_kw", "ac_voltage_v", "ac_current_a", "dc_voltage_v", "dc_current_a",
                "inverter_temperature_c", "ambient_temperature_c", "irradiance_w_m2", "operating_state", "grid_state",
            )
            if all(database_row[field] == normalized[field] for field in comparable):
                duplicates += 1
            else:
                rejected[row_number] = (row, ["conflicting record already exists for inverter timestamp"])
            continue
        accepted[key] = (row_number, normalized)
        if source_id:
            source_ids[source_id] = (row_number, key)
    accepted_records = [item for _, item in accepted.values()]
    quality = _quality_report(accepted_records, all_warnings)
    import_id = f"scada-import-{uuid.uuid4().hex[:12]}"
    report = {
        "schema_version": SCHEMA_VERSION, "filename": Path(filename).name,
        "total_records": len(rows), "accepted_records": len(accepted_records),
        "rejected_records": len(rejected), "duplicate_records": duplicates,
        "quality": quality,
        "preview": accepted_records[:10],
        "rejection_preview": [
            {"row_number": number, "reasons": reasons, "raw": raw}
            for number, (raw, reasons) in list(rejected.items())[:10]
        ],
    }
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn.execute(
        """INSERT INTO data_imports
           (import_id,site_id,import_type,filename,file_sha256,source_system,status,mapping_json,
            options_json,report_json,total_records,accepted_records,rejected_records,duplicate_records,created_at)
           VALUES (?,?, 'scada_csv',?,?,?,'previewed',?,?,?,?,?,?,?,?)""",
        (import_id, site_id, Path(filename).name, digest, source_system, json.dumps(mapping),
         json.dumps({"units": units, "timezone": timezone_name}), json.dumps(report), len(rows),
         len(accepted_records), len(rejected), duplicates, now),
    )
    conn.executemany(
        "INSERT INTO data_import_staging(import_id,row_number,normalized_json) VALUES (?,?,?)",
        [(import_id, number, json.dumps(item)) for number, item in accepted.values()],
    )
    conn.executemany(
        "INSERT INTO data_import_rejections(import_id,row_number,raw_json,reasons_json) VALUES (?,?,?,?)",
        [(import_id, number, json.dumps(raw), json.dumps(reasons)) for number, (raw, reasons) in rejected.items()],
    )
    conn.commit()
    return {**report, "import_id": import_id, "status": "previewed", "already_imported": False}


def commit_scada_import(conn: sqlite3.Connection, import_id: str) -> dict[str, Any]:
    record = conn.execute("SELECT * FROM data_imports WHERE import_id=?", (import_id,)).fetchone()
    if not record:
        raise ImportValidationError("Import preview not found")
    if record["status"] == "committed":
        return {**json.loads(record["report_json"]), "import_id": import_id, "status": "committed", "already_imported": True}
    if record["status"] != "previewed":
        raise ImportValidationError(f"Import cannot be committed from status {record['status']}")
    staged = [(row["row_number"], json.loads(row["normalized_json"])) for row in conn.execute(
        "SELECT * FROM data_import_staging WHERE import_id=? ORDER BY row_number", (import_id,)
    )]
    if not staged:
        raise ImportValidationError("Import has no accepted telemetry records to commit")
    conflicts = [
        row_number for row_number, item in staged if conn.execute(
            "SELECT 1 FROM imported_inverter_telemetry WHERE site_id=? AND inverter_id=? AND timestamp_utc=?",
            (item["site_id"], item["inverter_id"], item["timestamp_utc"]),
        ).fetchone()
    ]
    if conflicts:
        raise ImportValidationError(f"Import changed since preview; {len(conflicts)} timestamp conflict(s) require a new preview")
    dataset_id = f"dataset-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    options = json.loads(record["options_json"])
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO telemetry_datasets
               (dataset_id,site_id,name,dataset_type,source_system,provenance,timezone,created_at,import_id,is_selectable)
               VALUES (?,?,?,'imported_scada',?,'imported_scada',?,?,?,1)""",
            (dataset_id, record["site_id"], f"Imported {record['filename']}", record["source_system"], options["timezone"], now, import_id),
        )
        columns = (
            "dataset_id,site_id,inverter_id,timestamp_utc,original_timestamp,original_timezone,ac_power_kw,dc_power_kw,"
            "ac_voltage_v,ac_current_a,dc_voltage_v,dc_current_a,inverter_temperature_c,ambient_temperature_c,"
            "irradiance_w_m2,operating_state,grid_state,source_system,source_record_id,data_provenance,data_quality_status,import_id"
        )
        for _, item in staged:
            values = [dataset_id] + [item[key] for key in (
                "site_id", "inverter_id", "timestamp_utc", "original_timestamp", "original_timezone",
                "ac_power_kw", "dc_power_kw", "ac_voltage_v", "ac_current_a", "dc_voltage_v", "dc_current_a",
                "inverter_temperature_c", "ambient_temperature_c", "irradiance_w_m2", "operating_state",
                "grid_state", "source_system", "source_record_id", "data_provenance", "data_quality_status",
            )] + [import_id]
            conn.execute(f"INSERT INTO imported_inverter_telemetry({columns}) VALUES ({','.join('?' for _ in values)})", values)
        conn.execute("UPDATE data_imports SET status='committed',committed_at=? WHERE import_id=?", (now, import_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {**json.loads(record["report_json"]), "import_id": import_id, "dataset_id": dataset_id, "status": "committed", "already_imported": False}

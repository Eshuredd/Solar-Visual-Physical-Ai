"""Transactional CSV import for explicit electrical-to-physical topology."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .asset_topology import topology_validation_report
from .scada_import import ImportValidationError, MAX_CSV_ROWS, MAX_UPLOAD_BYTES


RECORD_TYPES = {"block", "row", "mppt", "string", "asset_group", "string_asset_group"}


def _positive(row: dict[str, str], key: str, errors: list[str], required: bool = True) -> int | None:
    value = (row.get(key) or "").strip()
    if not value and not required:
        return None
    try:
        number = int(value)
        if number < 1:
            raise ValueError
        return number
    except ValueError:
        errors.append(f"{key} must be a positive integer")
        return None


def preview_topology_csv(
    conn: sqlite3.Connection, *, site_id: str, filename: str, content: bytes,
    source_system: str, mapping_version: str, effective_date: str | None,
) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportValidationError(f"CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if Path(filename).suffix.lower() != ".csv":
        raise ImportValidationError("Only .csv files are accepted")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        rows = list(reader)
    except UnicodeDecodeError as exc:
        raise ImportValidationError("CSV must be UTF-8 encoded") from exc
    if not reader.fieldnames or "record_type" not in reader.fieldnames:
        raise ImportValidationError("Topology CSV requires a record_type header")
    if len(rows) > MAX_CSV_ROWS:
        raise ImportValidationError(f"CSV exceeds the {MAX_CSV_ROWS:,} record preview limit")
    digest = hashlib.sha256(content).hexdigest()
    existing = conn.execute(
        "SELECT * FROM data_imports WHERE site_id=? AND import_type='topology_csv' AND file_sha256=?",
        (site_id, digest),
    ).fetchone()
    if existing:
        report = json.loads(existing["report_json"])
        return {**report, "import_id": existing["import_id"], "status": existing["status"],
                "already_imported": existing["status"] == "committed"}

    known_inverters = {r[0] for r in conn.execute("SELECT inverter_id FROM inverters WHERE site_id=?", (site_id,))}
    blocks = {r[0] for r in conn.execute("SELECT block_code FROM site_blocks WHERE site_id=?", (site_id,))}
    rows_known = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT block_code,row_number,module_position_count FROM block_rows WHERE site_id=?", (site_id,)
    )}
    mppts = {r[0] for r in conn.execute("SELECT mppt_id FROM inverter_mppts WHERE site_id=?", (site_id,))}
    strings = {r[0] for r in conn.execute("SELECT string_id FROM pv_strings WHERE site_id=?", (site_id,))}
    groups: dict[str, tuple[str, int, int, int]] = {
        r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute(
            "SELECT asset_group_id,block_code,row_number,module_start,module_end FROM physical_asset_groups WHERE site_id=?", (site_id,)
        )
    }
    existing_identifiers = (
        {("block", r[0]) for r in conn.execute("SELECT block_code FROM site_blocks WHERE site_id=?", (site_id,))} |
        {("row", f"{r[0]}:{r[1]}") for r in conn.execute("SELECT block_code,row_number FROM block_rows WHERE site_id=?", (site_id,))} |
        {("mppt", r[0]) for r in conn.execute("SELECT mppt_id FROM inverter_mppts WHERE site_id=?", (site_id,))} |
        {("string", r[0]) for r in conn.execute("SELECT string_id FROM pv_strings WHERE site_id=?", (site_id,))} |
        {("asset_group", r[0]) for r in conn.execute("SELECT asset_group_id FROM physical_asset_groups WHERE site_id=?", (site_id,))} |
        {("string_asset_group", f"{r[0]}:{r[1]}") for r in conn.execute("SELECT string_id,asset_group_id FROM string_asset_groups WHERE site_id=?", (site_id,))}
    )
    accepted: list[tuple[int, dict[str, Any]]] = []
    rejected: list[tuple[int, dict[str, str], list[str]]] = []
    identifiers: set[tuple[str, str]] = set()
    ordinal = 1
    for number, raw in enumerate(rows, start=2):
        kind = (raw.get("record_type") or "").strip().lower()
        errors: list[str] = []
        row_site = (raw.get("site_id") or "").strip()
        if row_site != site_id:
            errors.append("site_id does not match selected site")
        if kind not in RECORD_TYPES:
            errors.append(f"record_type must be one of {', '.join(sorted(RECORD_TYPES))}")
        item: dict[str, Any] = {"record_type": kind, "site_id": site_id}
        identity = ""
        if kind == "block":
            identity = (raw.get("block_code") or "").strip()
            count = _positive(raw, "expected_row_count", errors, required=False)
            if not identity: errors.append("block_code is required")
            item.update(block_code=identity, display_name=(raw.get("display_name") or identity).strip(), expected_row_count=count)
        elif kind == "row":
            block = (raw.get("block_code") or "").strip(); row_no = _positive(raw, "row_number", errors)
            count = _positive(raw, "module_position_count", errors); identity = f"{block}:{row_no}"
            if block not in blocks: errors.append("parent block is missing or appears later")
            item.update(block_code=block, row_number=row_no, module_position_count=count,
                        layout_label=(raw.get("display_name") or "").strip() or None)
        elif kind == "mppt":
            identity = (raw.get("mppt_id") or "").strip(); inverter = (raw.get("inverter_id") or "").strip()
            index = _positive(raw, "input_index", errors)
            if inverter not in known_inverters: errors.append("inverter_id is not registered at the selected site")
            if not identity: errors.append("mppt_id is required")
            item.update(mppt_id=identity, inverter_id=inverter, input_index=index,
                        label=(raw.get("display_name") or identity).strip())
        elif kind == "string":
            identity = (raw.get("string_id") or "").strip(); mppt = (raw.get("mppt_id") or "").strip()
            index = _positive(raw, "string_index", errors)
            if mppt not in mppts: errors.append("parent mppt is missing or appears later")
            if not identity: errors.append("string_id is required")
            item.update(string_id=identity, mppt_id=mppt, string_index=index,
                        label=(raw.get("display_name") or identity).strip())
        elif kind == "asset_group":
            identity = (raw.get("asset_group_id") or "").strip(); block = (raw.get("block_code") or "").strip()
            row_no = _positive(raw, "row_number", errors); start = _positive(raw, "module_start", errors); end = _positive(raw, "module_end", errors)
            if (block, row_no) not in rows_known: errors.append("parent row is missing or appears later")
            elif end and end > rows_known[(block, row_no)]: errors.append("module_end exceeds the declared row layout")
            if start and end and end < start: errors.append("module_end must not precede module_start")
            if not identity: errors.append("asset_group_id is required")
            for other_id, (other_block, other_row, other_start, other_end) in groups.items():
                if block == other_block and row_no == other_row and start and end and start <= other_end and end >= other_start:
                    errors.append(f"module range overlaps asset group {other_id}")
            item.update(asset_group_id=identity, block_code=block, row_number=row_no,
                        module_start=start, module_end=end, display_name=(raw.get("display_name") or identity).strip(),
                        start_ordinal=ordinal, end_ordinal=ordinal + ((end or 0) - (start or 0)))
        elif kind == "string_asset_group":
            string_id = (raw.get("string_id") or "").strip(); group_id = (raw.get("asset_group_id") or "").strip()
            identity = f"{string_id}:{group_id}"
            if string_id not in strings: errors.append("parent string is missing or appears later")
            if group_id not in groups: errors.append("parent asset_group is missing or appears later")
            item.update(string_id=string_id, asset_group_id=group_id)
        if identity and (kind, identity) in identifiers:
            errors.append("duplicate identifier within file")
        if identity and (kind, identity) in existing_identifiers:
            errors.append("identifier already exists at this site")
        identifiers.add((kind, identity))
        if errors:
            rejected.append((number, raw, errors))
        else:
            accepted.append((number, item))
            if kind == "block": blocks.add(item["block_code"])
            elif kind == "row": rows_known[(item["block_code"], item["row_number"])] = item["module_position_count"]
            elif kind == "mppt": mppts.add(item["mppt_id"])
            elif kind == "string": strings.add(item["string_id"])
            elif kind == "asset_group":
                groups[item["asset_group_id"]] = (item["block_code"], item["row_number"], item["module_start"], item["module_end"])
                ordinal = item["end_ordinal"] + 1

    import_id = f"topology-import-{uuid.uuid4().hex[:12]}"
    report = {"filename": Path(filename).name, "total_records": len(rows), "accepted_records": len(accepted),
              "rejected_records": len(rejected), "duplicate_records": 0,
              "classification": "imported_unverified", "mapping_version": mapping_version,
              "preview": [x for _, x in accepted[:10]],
              "rejection_preview": [{"row_number": n, "raw": r, "reasons": e} for n, r, e in rejected[:10]]}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn.execute("""INSERT INTO data_imports
      (import_id,site_id,import_type,filename,file_sha256,source_system,status,mapping_json,options_json,
       report_json,total_records,accepted_records,rejected_records,duplicate_records,created_at)
      VALUES (?,?, 'topology_csv',?,?,?,'previewed','{}',?,?,?,?,?,0,?)""",
      (import_id, site_id, Path(filename).name, digest, source_system,
       json.dumps({"mapping_version": mapping_version, "effective_date": effective_date}), json.dumps(report),
       len(rows), len(accepted), len(rejected), now))
    conn.executemany("INSERT INTO data_import_staging VALUES (?,?,?)", [(import_id, n, json.dumps(x)) for n, x in accepted])
    conn.executemany("INSERT INTO data_import_rejections VALUES (?,?,?,?)", [(import_id, n, json.dumps(r), json.dumps(e)) for n, r, e in rejected])
    conn.commit()
    return {**report, "import_id": import_id, "status": "previewed", "already_imported": False}


def commit_topology_import(conn: sqlite3.Connection, import_id: str) -> dict[str, Any]:
    record = conn.execute("SELECT * FROM data_imports WHERE import_id=? AND import_type='topology_csv'", (import_id,)).fetchone()
    if not record: raise ImportValidationError("Topology import preview not found")
    if record["status"] == "committed":
        return {**json.loads(record["report_json"]), "import_id": import_id, "status": "committed", "already_imported": True}
    if record["status"] != "previewed": raise ImportValidationError("Topology import is not commit-ready")
    if record["rejected_records"]: raise ImportValidationError("Resolve rejected topology records and preview again before commit")
    options = json.loads(record["options_json"]); version = options["mapping_version"]; effective = options.get("effective_date")
    staged = [json.loads(r[0]) for r in conn.execute("SELECT normalized_json FROM data_import_staging WHERE import_id=? ORDER BY row_number", (import_id,))]
    if not staged: raise ImportValidationError("Topology import has no accepted records to commit")
    common = ("unverified", "imported_unverified", f"CSV import {import_id} from {record['source_system']}", version, effective)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for x in staged:
            kind = x["record_type"]
            if kind == "block": conn.execute("INSERT INTO site_blocks VALUES (?,?,?,?,?,'partial',?,?,?,?,?)", (x["site_id"],x["block_code"],x["display_name"],x["expected_row_count"],common[0],common[2],common[1],version,effective,import_id))
            elif kind == "row": conn.execute("INSERT INTO block_rows VALUES (?,?,?,?,?,?,?,?,?,?)", (x["site_id"],x["block_code"],x["row_number"],x["module_position_count"],x["layout_label"],*common))
            elif kind == "mppt": conn.execute("INSERT INTO inverter_mppts VALUES (?,?,?,?,?,?,?,?,?,?,?)", (x["mppt_id"],x["site_id"],x["inverter_id"],x["input_index"],x["label"],common[0],common[2],common[1],version,effective,import_id))
            elif kind == "string": conn.execute("INSERT INTO pv_strings VALUES (?,?,?,?,?,?,?,?,?,?,?)", (x["string_id"],x["site_id"],x["mppt_id"],x["string_index"],x["label"],common[0],common[2],common[1],version,effective,import_id))
            elif kind == "asset_group":
                count=x["module_end"]-x["module_start"]+1
                conn.execute("INSERT INTO physical_asset_groups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (x["asset_group_id"],x["site_id"],x["block_code"],x["display_name"],x["start_ordinal"],x["end_ordinal"],x["row_number"],x["row_number"],x["module_start"],x["module_end"],count,common[0],common[2],x["row_number"],common[1],version,effective,import_id))
            elif kind == "string_asset_group": conn.execute("INSERT INTO string_asset_groups VALUES (?,?,?,'feeds',?,?,?,?,?,?)", (x["site_id"],x["string_id"],x["asset_group_id"],common[0],common[2],common[1],version,effective,import_id))
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute("UPDATE data_imports SET status='committed',committed_at=? WHERE import_id=?", (now,import_id))
        conn.commit()
    except Exception:
        conn.rollback(); raise
    return {**json.loads(record["report_json"]), "import_id": import_id, "status": "committed",
            "validation": topology_validation_report(conn, record["site_id"]), "already_imported": False}

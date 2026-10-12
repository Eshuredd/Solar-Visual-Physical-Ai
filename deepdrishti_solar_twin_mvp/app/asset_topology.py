"""Read-only topology and evidence-correlation services for Phase 3A."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import rows_to_dicts


TOPOLOGY_VERSION = "electrical-physical-topology-v1"


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def topology_validation_report(conn: sqlite3.Connection, site_id: str) -> dict[str, Any]:
    declared = conn.execute(
        "SELECT COALESCE(SUM(module_position_count),0) FROM block_rows WHERE site_id=?", (site_id,)
    ).fetchone()[0]
    mapped = conn.execute(
        "SELECT COALESCE(SUM(mapped_module_count),0) FROM physical_asset_groups WHERE site_id=?", (site_id,)
    ).fetchone()[0]
    overlaps = conn.execute(
        """SELECT COUNT(*) FROM physical_asset_groups a JOIN physical_asset_groups b
           ON a.asset_group_id < b.asset_group_id AND a.site_id=b.site_id
           AND a.block_code=b.block_code AND a.row_number=b.row_number
           AND a.module_start<=b.module_end AND a.module_end>=b.module_start
           WHERE a.site_id=?""", (site_id,),
    ).fetchone()[0]
    inconsistent = conn.execute(
        """SELECT COUNT(*) FROM physical_asset_groups g LEFT JOIN block_rows r
           ON r.site_id=g.site_id AND r.block_code=g.block_code AND r.row_number=g.row_number
           WHERE g.site_id=? AND (r.row_number IS NULL OR g.module_start<1
             OR g.module_end>r.module_position_count
             OR g.mapped_module_count!=(g.module_end-g.module_start+1))""", (site_id,),
    ).fetchone()[0]
    unmapped_strings = conn.execute(
        """SELECT COUNT(*) FROM pv_strings s WHERE s.site_id=? AND NOT EXISTS
           (SELECT 1 FROM string_asset_groups sg WHERE sg.string_id=s.string_id)""", (site_id,),
    ).fetchone()[0]
    classifications = {
        row[0] for table in ("site_blocks", "block_rows", "inverter_mppts", "pv_strings", "physical_asset_groups", "string_asset_groups")
        for row in conn.execute(f"SELECT DISTINCT mapping_classification FROM {table} WHERE site_id=?", (site_id,))
    }
    reviews = {
        row[0] for table in ("site_blocks", "block_rows", "inverter_mppts", "pv_strings", "physical_asset_groups", "string_asset_groups")
        for row in conn.execute(f"SELECT DISTINCT review_status FROM {table} WHERE site_id=?", (site_id,)) if row[0]
    }
    completeness = None if declared == 0 else max(0.0, min(1.0, mapped / declared))
    if not classifications:
        verification = "unavailable"
    elif "synthetic_demo" in classifications:
        verification = "synthetic_topology"
    elif completeness is None or completeness < 1 or overlaps or inconsistent or unmapped_strings:
        verification = "partially_mapped"
    elif classifications == {"verified_as_built"} and reviews == {"verified_as_built"}:
        verification = "verified_as_built"
    elif reviews and reviews <= {"reviewed", "verified_as_built"}:
        verification = "reviewed_topology"
    else:
        verification = "imported_unverified"
    return {
        "verification_status": verification,
        "is_verified_as_built": verification == "verified_as_built",
        "declared_module_positions": declared or None,
        "mapped_module_positions": mapped,
        "mapping_completeness": None if completeness is None else round(completeness, 4),
        "mapping_completeness_pct": None if completeness is None else round(completeness * 100, 2),
        "unmapped_module_positions": None if completeness is None else max(0, declared - mapped),
        "overlap_count": overlaps, "inconsistent_group_count": inconsistent,
        "unmapped_string_count": unmapped_strings,
        "classifications": sorted(classifications), "review_statuses": sorted(reviews),
        "valid": not overlaps and not inconsistent and verification != "unavailable",
    }


def site_topology_summary(conn: sqlite3.Connection, site_id: str) -> dict[str, Any]:
    inverter_count = conn.execute("SELECT COUNT(*) FROM inverters WHERE site_id=?", (site_id,)).fetchone()[0]
    block_count = conn.execute("SELECT COUNT(*) FROM site_blocks WHERE site_id=?", (site_id,)).fetchone()[0]
    if block_count == 0:
        return {
            "hierarchy": {
                "site": site_id, "inverters": inverter_count, "blocks": None, "mppts": None,
                "strings": None, "rows": None, "modules": None,
            },
            "coverage": {
                "status": "unavailable", "mapping_classification": "unknown",
                "is_verified_as_built": False,
                "message": "No electrical-to-physical topology has been loaded for this site.",
            },
            "blocks": [],
        }
    validation = topology_validation_report(conn, site_id)
    counts = dict(conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM inverter_mppts WHERE site_id=?) AS mppts,
             (SELECT COUNT(*) FROM pv_strings WHERE site_id=?) AS strings,
             (SELECT COUNT(*) FROM block_rows WHERE site_id=?) AS rows,
             (SELECT COALESCE(SUM(module_position_count),0) FROM block_rows WHERE site_id=?) AS modules""",
        (site_id, site_id, site_id, site_id),
    ).fetchone())
    classification = validation["verification_status"]
    coverage_status = "complete" if validation["mapping_completeness"] == 1 else "partial"
    blocks = rows_to_dicts(conn.execute(
        """SELECT b.block_code AS id, b.display_name, b.expected_row_count AS rows,
                  b.mapping_classification, b.coverage_status, b.provenance,
                  COUNT(DISTINCT m.inverter_id) AS inverters,
                  COUNT(DISTINCT p.mppt_id) AS mppts,
                  COUNT(DISTINCT s.string_id) AS strings,
                  COALESCE((SELECT SUM(r.module_position_count) FROM block_rows r
                    WHERE r.site_id=b.site_id AND r.block_code=b.block_code),0) AS modules
           FROM site_blocks b
           LEFT JOIN physical_asset_groups g ON g.site_id=b.site_id AND g.block_code=b.block_code
           LEFT JOIN string_asset_groups sg ON sg.asset_group_id=g.asset_group_id
           LEFT JOIN pv_strings s ON s.string_id=sg.string_id
           LEFT JOIN inverter_mppts p ON p.mppt_id=s.mppt_id
           LEFT JOIN inverters m ON m.inverter_id=p.inverter_id
           WHERE b.site_id=? GROUP BY b.site_id,b.block_code ORDER BY b.block_code""",
        (site_id,),
    ))
    return {
        "hierarchy": {
            "site": site_id, "inverters": inverter_count, "blocks": block_count,
            "mppts": counts["mppts"], "strings": counts["strings"],
            "rows": counts["rows"], "modules": counts["modules"],
        },
        "coverage": {
            "status": coverage_status,
            "mapping_classification": classification,
            "is_verified_as_built": validation["is_verified_as_built"],
            "validation": validation,
            "message": (
                "Deterministic simulated topology for the rendered demo layout; not engineering as-built documentation."
                if classification == "synthetic_topology" else "Topology coverage is calculated from row layouts and mapped ranges."
            ),
        },
        "blocks": blocks,
    }


def inverter_topology(conn: sqlite3.Connection, inverter: dict[str, Any]) -> dict[str, Any]:
    rows = rows_to_dicts(conn.execute(
        """SELECT p.mppt_id,p.input_index,p.label AS mppt_label,p.mapping_classification AS mppt_classification,
                  p.provenance AS mppt_provenance,s.string_id,s.string_index,s.label AS string_label,
                  s.mapping_classification AS string_classification,s.provenance AS string_provenance,
                  g.asset_group_id,g.block_code,g.display_name AS asset_group_name,g.start_ordinal,g.end_ordinal,
                  g.row_start,g.row_end,g.row_number,g.module_start,g.module_end,g.mapped_module_count,
                  g.mapping_classification AS group_classification,g.review_status AS group_review_status,
                  s.review_status AS string_review_status,p.review_status AS mppt_review_status,
                  sg.mapping_classification AS relationship_classification,sg.review_status AS relationship_review_status,
                  sg.provenance AS relationship_provenance
           FROM inverter_mppts p
           LEFT JOIN pv_strings s ON s.mppt_id=p.mppt_id
           LEFT JOIN string_asset_groups sg ON sg.string_id=s.string_id
           LEFT JOIN physical_asset_groups g ON g.asset_group_id=sg.asset_group_id
           WHERE p.site_id=? AND p.inverter_id=? ORDER BY p.input_index,s.string_index,s.string_id""",
        (inverter["site_id"], inverter["inverter_id"]),
    ))
    if not rows:
        return {
            "inverter_id": inverter["inverter_id"], "site_id": inverter["site_id"],
            "available": False, "mapping_classification": "unknown", "is_verified_as_built": False,
            "coverage_status": "unavailable", "blocks": [], "mppts": [],
            "message": "No inverter-to-string topology is available.",
        }
    mppts: dict[str, dict[str, Any]] = {}
    blocks: set[str] = set()
    classifications: set[str] = set()
    for row in rows:
        entry = mppts.setdefault(row["mppt_id"], {
            "mppt_id": row["mppt_id"], "input_index": row["input_index"], "label": row["mppt_label"],
            "mapping_classification": row["mppt_classification"], "provenance": row["mppt_provenance"],
            "strings": {},
        })
        classifications.add(row["mppt_classification"])
        if row["string_id"]:
            classifications.add(row["string_classification"])
            group = None
            if row["asset_group_id"]:
                blocks.add(row["block_code"])
                classifications.update((row["group_classification"], row["relationship_classification"]))
                group = {key: row[key] for key in (
                    "asset_group_id", "block_code", "asset_group_name", "start_ordinal", "end_ordinal",
                    "row_start", "row_end", "row_number", "module_start", "module_end", "mapped_module_count",
                )}
            string = entry["strings"].setdefault(row["string_id"], {
                "string_id": row["string_id"], "string_index": row["string_index"], "label": row["string_label"],
                "mapping_classification": row["string_classification"], "provenance": row["string_provenance"],
                "asset_group": group, "asset_groups": [],
            })
            if group:
                string["asset_groups"].append(group)
    for mppt in mppts.values():
        mppt["strings"] = list(mppt["strings"].values())
    all_groups = [group for mppt in mppts.values() for item in mppt["strings"] for group in item["asset_groups"]]
    chain_reviews = {row[key] for row in rows for key in ("mppt_review_status", "string_review_status", "group_review_status", "relationship_review_status") if row[key]}
    if "synthetic_demo" in classifications:
        verification = "synthetic_topology"
    elif not all_groups or not all(all_groups):
        verification = "partially_mapped"
    elif classifications == {"verified_as_built"} and chain_reviews == {"verified_as_built"}:
        verification = "verified_as_built"
    elif chain_reviews and chain_reviews <= {"reviewed", "verified_as_built"}:
        verification = "reviewed_topology"
    else:
        verification = "imported_unverified"
    return {
        "inverter_id": inverter["inverter_id"], "site_id": inverter["site_id"], "available": True,
        "mapping_classification": verification, "is_verified_as_built": verification == "verified_as_built",
        "coverage_status": "complete" if all_groups and all(all_groups) else "partial",
        "blocks": sorted(blocks), "mppt_count": len(mppts),
        "string_count": sum(len(item["strings"]) for item in mppts.values()),
        "mapped_module_count": sum(group["mapped_module_count"] for group in all_groups),
        "mppts": list(mppts.values()),
        "message": "Simulated demo relationship; a customer as-built import and field verification are required." if verification == "synthetic_topology" else "Topology relationship loaded from the database; verification follows the weakest link in the chain.",
        "topology_version": TOPOLOGY_VERSION,
    }


def correlated_findings(conn: sqlite3.Connection, alert: dict[str, Any]) -> list[dict[str, Any]]:
    findings = rows_to_dicts(conn.execute(
        """SELECT DISTINCT a.*,i.captured_at AS inspection_captured_at,i.name AS inspection_name,
                  s.string_id,p.mppt_id,g.asset_group_id,
                  p.mapping_classification AS mppt_classification,
                  s.mapping_classification AS string_classification,
                  g.mapping_classification AS group_classification,
                  sg.mapping_classification AS link_classification,
                  p.review_status AS mppt_review_status,s.review_status AS string_review_status,
                  g.review_status AS group_review_status,sg.review_status AS link_review_status,
                  sg.provenance AS relationship_provenance
           FROM anomalies a
           JOIN inspections i ON i.id=a.inspection_id
           JOIN physical_asset_groups g ON g.site_id=a.site_id AND g.block_code=a.block_name
             AND g.row_number=a.row_no AND a.module_no BETWEEN g.module_start AND g.module_end
           JOIN string_asset_groups sg ON sg.asset_group_id=g.asset_group_id
           JOIN pv_strings s ON s.string_id=sg.string_id
           JOIN inverter_mppts p ON p.mppt_id=s.mppt_id
           WHERE a.site_id=? AND p.inverter_id=?
           ORDER BY i.captured_at DESC,a.priority,a.id""",
        (alert["site_id"], alert["inverter_id"]),
    ))
    alert_time = _dt(alert.get("onset_timestamp") or alert["start_timestamp"])
    result: list[dict[str, Any]] = []
    for finding in findings:
        difference = abs((_dt(finding["inspection_captured_at"]) - alert_time).total_seconds()) / 86400
        proximity = "same_day" if difference < 1 else "within_7_days" if difference <= 7 else "within_30_days" if difference <= 30 else "distant"
        finding["history"] = json.loads(finding["history"])
        finding["temporal_proximity"] = {"classification": proximity, "absolute_days": round(difference, 2)}
        finding["relationship_basis"] = "explicit string-to-physical-group mapping"
        chain = {finding[key] for key in (
            "mppt_classification", "string_classification", "group_classification", "link_classification"
        )}
        reviews = {finding[key] for key in (
            "mppt_review_status", "string_review_status", "group_review_status", "link_review_status"
        ) if finding[key]}
        if "synthetic_demo" in chain:
            finding["relationship_status"] = "simulated_not_verified"
        elif chain == {"verified_as_built"} and reviews == {"verified_as_built"}:
            finding["relationship_status"] = "verified"
        elif reviews and reviews <= {"reviewed", "verified_as_built"}:
            finding["relationship_status"] = "reviewed_not_as_built_verified"
        else:
            finding["relationship_status"] = "imported_unverified"
        result.append(finding)
    return result


def association_records(conn: sqlite3.Connection, alert_id: str) -> list[dict[str, Any]]:
    records = rows_to_dicts(conn.execute(
        """SELECT af.*,a.asset_id,a.anomaly_type,a.priority AS finding_priority
           FROM alert_finding_associations af JOIN anomalies a ON a.id=af.anomaly_id
           WHERE af.alert_id=? ORDER BY af.created_at,af.association_id""", (alert_id,)
    ))
    for record in records:
        record["history"] = rows_to_dicts(conn.execute(
            "SELECT * FROM alert_finding_association_events WHERE association_id=? ORDER BY rowid",
            (record["association_id"],),
        ))
    return records

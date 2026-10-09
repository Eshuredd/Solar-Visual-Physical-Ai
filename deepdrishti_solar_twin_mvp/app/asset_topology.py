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
    counts = dict(conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM inverter_mppts WHERE site_id=?) AS mppts,
             (SELECT COUNT(*) FROM pv_strings WHERE site_id=?) AS strings,
             (SELECT COALESCE(SUM(expected_row_count),0) FROM site_blocks WHERE site_id=?) AS rows,
             (SELECT COALESCE(SUM(mapped_module_count),0) FROM physical_asset_groups WHERE site_id=?) AS modules""",
        (site_id, site_id, site_id, site_id),
    ).fetchone())
    classifications = [row[0] for row in conn.execute(
        "SELECT DISTINCT mapping_classification FROM site_blocks WHERE site_id=?", (site_id,)
    )]
    classification = classifications[0] if len(classifications) == 1 else "mixed"
    coverage_states = [row[0] for row in conn.execute(
        "SELECT DISTINCT coverage_status FROM site_blocks WHERE site_id=?", (site_id,)
    )]
    coverage_status = "complete" if coverage_states == ["complete"] else "partial"
    blocks = rows_to_dicts(conn.execute(
        """SELECT b.block_code AS id, b.display_name, b.expected_row_count AS rows,
                  b.mapping_classification, b.coverage_status, b.provenance,
                  COUNT(DISTINCT m.inverter_id) AS inverters,
                  COUNT(DISTINCT p.mppt_id) AS mppts,
                  COUNT(DISTINCT s.string_id) AS strings,
                  COALESCE(SUM(g.mapped_module_count),0) AS modules
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
            "is_verified_as_built": classification == "verified_as_built",
            "message": (
                "Deterministic simulated topology for the rendered demo layout; not engineering as-built documentation."
                if classification == "synthetic_demo" else "Topology coverage is database-derived."
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
                  g.row_start,g.row_end,g.module_start,g.module_end,g.mapped_module_count,
                  sg.mapping_classification AS relationship_classification,sg.provenance AS relationship_provenance
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
            "strings": [],
        })
        classifications.add(row["mppt_classification"])
        if row["string_id"]:
            group = None
            if row["asset_group_id"]:
                blocks.add(row["block_code"])
                classifications.add(row["relationship_classification"])
                group = {key: row[key] for key in (
                    "asset_group_id", "block_code", "asset_group_name", "start_ordinal", "end_ordinal",
                    "row_start", "row_end", "module_start", "module_end", "mapped_module_count",
                )}
            entry["strings"].append({
                "string_id": row["string_id"], "string_index": row["string_index"], "label": row["string_label"],
                "mapping_classification": row["string_classification"], "provenance": row["string_provenance"],
                "asset_group": group,
            })
    classification = next(iter(classifications)) if len(classifications) == 1 else "mixed"
    return {
        "inverter_id": inverter["inverter_id"], "site_id": inverter["site_id"], "available": True,
        "mapping_classification": classification, "is_verified_as_built": classification == "verified_as_built",
        "coverage_status": "complete" if all(item["asset_group"] for mppt in mppts.values() for item in mppt["strings"]) else "partial",
        "blocks": sorted(blocks), "mppt_count": len(mppts),
        "string_count": sum(len(item["strings"]) for item in mppts.values()),
        "mapped_module_count": sum(
            item["asset_group"]["mapped_module_count"] for mppt in mppts.values()
            for item in mppt["strings"] if item["asset_group"]
        ),
        "mppts": list(mppts.values()),
        "message": "Simulated demo relationship; a customer as-built import and field verification are required." if classification == "synthetic_demo" else "Topology relationship loaded from the database.",
        "topology_version": TOPOLOGY_VERSION,
    }


def correlated_findings(conn: sqlite3.Connection, alert: dict[str, Any]) -> list[dict[str, Any]]:
    findings = rows_to_dicts(conn.execute(
        """SELECT DISTINCT a.*,i.captured_at AS inspection_captured_at,i.name AS inspection_name,
                  s.string_id,p.mppt_id,g.asset_group_id,g.mapping_classification AS relationship_classification,
                  g.provenance AS relationship_provenance
           FROM anomalies a
           JOIN inspections i ON i.id=a.inspection_id
           JOIN physical_asset_groups g ON g.site_id=a.site_id AND g.block_code=a.block_name
             AND ((a.row_no-1)*32+a.module_no) BETWEEN g.start_ordinal AND g.end_ordinal
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
        finding["relationship_status"] = "verified" if finding["relationship_classification"] == "verified_as_built" else "simulated_not_verified"
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

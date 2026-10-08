"""Deterministic, explainable event detection for inverter telemetry.

The rules generate investigation alerts, not verified root-cause diagnoses.
Ground-truth scenario labels are intentionally not consumed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .inverter_monitoring import expected_ac_power_kw, parse_iso_datetime, validate_telemetry

METHOD_VERSION = "inverter-rules-v2a.1"


@dataclass(frozen=True)
class DetectionThresholds:
    interval_minutes: int = 15
    minimum_irradiance_w_m2: float = 200.0
    minimum_load_fraction: float = 0.15
    ac_conversion_ratio: float = 0.88
    ac_persistence_samples: int = 4
    pv_production_ratio: float = 0.78
    pv_persistence_samples: int = 6
    overheat_residual_c: float = 10.0
    absolute_temperature_c: float = 75.0
    temperature_persistence_samples: int = 4
    shutdown_persistence_samples: int = 2
    intermittent_ratio: float = 0.72
    intermittent_max_run_samples: int = 4
    intermittent_min_occurrences: int = 3
    stale_after_minutes: int = 45


DEFAULT_THRESHOLDS = DetectionThresholds()
KNOWN_EXCLUSIONS = {"Night", "Startup", "Shutdown", "Curtailment", "Protection", "Grid Restricted", "Missing"}
CATEGORIES = {
    "ac_conversion_underperformance", "pv_side_underperformance", "inverter_overheating",
    "unexpected_shutdown", "intermittent_derating", "data_quality",
}


def evaluate_synthetic_events(detected: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """Event-overlap evaluation for deterministic synthetic fixtures only."""
    positive = [item for item in scenarios if item.get("expected_alert_category")]
    negative = [item for item in scenarios if not item.get("expected_alert_category")]
    matched_events: set[int] = set()
    matched_truth: set[str] = set()
    latencies: list[float] = []
    duplicates = 0
    by_type: dict[str, dict[str, Any]] = {}
    for truth in positive:
        matches = [
            (index, event) for index, event in enumerate(detected)
            if event["inverter_id"] == truth["inverter_id"]
            and event["anomaly_category"] == truth["expected_alert_category"]
            and _dt(event["end_timestamp"]) >= _dt(truth["start_timestamp"])
            and _dt(event["start_timestamp"]) <= _dt(truth["end_timestamp"])
        ]
        if matches:
            matched_truth.add(truth["scenario_id"])
            matched_events.add(matches[0][0])
            duplicates += max(0, len(matches) - 1)
            latencies.append(max(0.0, (_dt(matches[0][1]["start_timestamp"]) - _dt(truth["start_timestamp"])).total_seconds() / 60))
    false_events = [event for index, event in enumerate(detected) if index not in matched_events]
    categories = sorted({item["expected_alert_category"] for item in positive})
    for category in categories:
        truths = [item for item in positive if item["expected_alert_category"] == category]
        detected_category = [item for item in detected if item["anomaly_category"] == category]
        true_count = sum(1 for item in truths if item["scenario_id"] in matched_truth)
        matched_count = len([index for index in matched_events if detected[index]["anomaly_category"] == category])
        false_count = max(0, len(detected_category) - matched_count)
        by_type[category] = {
            "ground_truth_events": len(truths), "detected_true_events": true_count,
            "false_positive_events": false_count,
            "precision": round(true_count / max(1, true_count + false_count), 3),
            "recall": round(true_count / max(1, len(truths)), 3),
        }
    negative_with_alert = 0
    for scenario in negative:
        if any(
            event["inverter_id"] == scenario["inverter_id"]
            and event["anomaly_category"] != "data_quality"
            and _dt(event["end_timestamp"]) >= _dt(scenario["start_timestamp"])
            and _dt(event["start_timestamp"]) <= _dt(scenario["end_timestamp"])
            for event in detected
        ):
            negative_with_alert += 1
    true_total = len(matched_truth)
    return {
        "scope": "synthetic_scenario_evaluation_only",
        "event_level_precision": round(true_total / max(1, true_total + len(false_events)), 3),
        "event_level_recall": round(true_total / max(1, len(positive)), 3),
        "false_positive_rate": round(negative_with_alert / max(1, len(negative)), 3),
        "mean_detection_latency_minutes": round(sum(latencies) / max(1, len(latencies)), 2),
        "duplicate_alert_count": duplicates,
        "normal_operation_passed": not any(event["inverter_id"] == "site-001-INV-01" for event in detected),
        "by_anomaly_type": by_type,
        "limitations": "Metrics use deterministic synthetic scenarios and do not represent real-world fault-detection accuracy.",
    }


def _dt(value: str) -> datetime:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        raise ValueError("timestamp is required")
    return parsed.astimezone(timezone.utc)


def _runs(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool], thresholds: DetectionThresholds) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    maximum_gap = timedelta(minutes=thresholds.interval_minutes * 1.6)
    for row in rows:
        contiguous = not current or _dt(row["timestamp"]) - _dt(current[-1]["timestamp"]) <= maximum_gap
        if predicate(row) and contiguous:
            current.append(row)
        else:
            if current:
                result.append(current)
            current = [row] if predicate(row) else []
    if current:
        result.append(current)
    return result


def _severity(deviation: float, duration_samples: int) -> str:
    if deviation >= 0.45 or duration_samples >= 16:
        return "Critical"
    if deviation >= 0.25 or duration_samples >= 8:
        return "High"
    return "Medium"


def _event(
    category: str,
    rows: list[dict[str, Any]],
    deviation: float,
    expected: dict[str, Any],
    observed: dict[str, Any],
    explanation: str,
    recommendation: str,
    subtype: str | None = None,
) -> dict[str, Any]:
    duration = int((_dt(rows[-1]["timestamp"]) - _dt(rows[0]["timestamp"])).total_seconds() / 60) + 15
    return {
        "anomaly_category": category,
        "anomaly_subtype": subtype,
        "start_timestamp": rows[0]["timestamp"],
        "end_timestamp": rows[-1]["timestamp"],
        "duration_minutes": duration,
        "severity": _severity(deviation, len(rows)) if category != "data_quality" else "Medium",
        "detection_method": METHOD_VERSION,
        "detection_score": round(max(0.0, deviation), 4),
        "expected_measurements": expected,
        "observed_measurements": observed,
        "contributing_telemetry_refs": [row["timestamp"] for row in rows],
        "explanation": explanation,
        "recommended_investigation": recommendation,
    }


def detect_anomaly_events(
    inverter: dict[str, Any],
    readings: list[dict[str, Any]],
    analysis_end: str | None = None,
    thresholds: DetectionThresholds = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    """Detect time-bounded events from measurements only, never scenario labels."""
    if not readings:
        return []
    rated = float(inverter["rated_ac_power_kw"])
    rows: list[dict[str, Any]] = []
    for source in sorted(readings, key=lambda item: item["timestamp"]):
        row = dict(source)
        solar_ac = expected_ac_power_kw(rated, row.get("irradiance_w_m2"), row.get("inverter_temperature_c"))
        row["expected_solar_ac_kw"] = solar_ac
        row["expected_dc_kw"] = None if solar_ac is None else solar_ac / 0.975
        row["expected_ac_from_dc_kw"] = min(rated, float(row.get("dc_power_kw") or 0) * 0.975)
        row["pv_ratio"] = None if not row["expected_dc_kw"] else float(row.get("dc_power_kw") or 0) / row["expected_dc_kw"]
        row["conversion_ratio"] = None if not row["expected_ac_from_dc_kw"] else float(row.get("ac_power_kw") or 0) / row["expected_ac_from_dc_kw"]
        row["load_fraction"] = float(row.get("ac_power_kw") or 0) / rated
        row["temperature_delta_c"] = float(row.get("inverter_temperature_c") or 0) - float(row.get("ambient_temperature_c") or 0)
        rows.append(row)

    def valid_daylight(row: dict[str, Any]) -> bool:
        return (
            row.get("operating_state") not in KNOWN_EXCLUSIONS
            and float(row.get("irradiance_w_m2") or 0) >= thresholds.minimum_irradiance_w_m2
            and not validate_telemetry(row)
        )

    events: list[dict[str, Any]] = []
    ac_runs = _runs(rows, lambda row: valid_daylight(row) and row["expected_ac_from_dc_kw"] >= rated * thresholds.minimum_load_fraction and row["conversion_ratio"] < thresholds.ac_conversion_ratio, thresholds)
    for run in ac_runs:
        if len(run) < thresholds.ac_persistence_samples:
            continue
        ratio = sum(row["conversion_ratio"] for row in run) / len(run)
        events.append(_event(
            "ac_conversion_underperformance", run, 1 - ratio,
            {"mean_expected_ac_from_dc_kw": round(sum(row["expected_ac_from_dc_kw"] for row in run) / len(run), 2)},
            {"mean_observed_ac_kw": round(sum(float(row["ac_power_kw"]) for row in run) / len(run), 2), "mean_conversion_ratio": round(ratio, 3)},
            "AC output remained below the DC-input-based conversion model at valid load. DC input itself was available, so this is a conversion-path investigation alert.",
            "Check inverter efficiency, grid-side limits, controls and AC measurements. Confirm root cause before maintenance.",
        ))

    pv_runs = _runs(rows, lambda row: valid_daylight(row) and row["expected_dc_kw"] and row["expected_dc_kw"] >= rated * thresholds.minimum_load_fraction and float(row.get("dc_power_kw") or 0) > rated * .02 and row["pv_ratio"] < thresholds.pv_production_ratio, thresholds)
    for run in pv_runs:
        if len(run) < thresholds.pv_persistence_samples:
            continue
        ratio = sum(row["pv_ratio"] for row in run) / len(run)
        events.append(_event(
            "pv_side_underperformance", run, 1 - ratio,
            {"mean_modeled_dc_kw": round(sum(row["expected_dc_kw"] for row in run) / len(run), 2)},
            {"mean_observed_dc_kw": round(sum(float(row["dc_power_kw"]) for row in run) / len(run), 2), "mean_production_ratio": round(ratio, 3)},
            "DC production was persistently below the irradiance/temperature baseline while AC conversion remained physically consistent. Cause is unconfirmed without string or MPPT evidence.",
            "Review irradiance quality, strings, MPPT channels, soiling/shading and DC availability before assigning a cause.",
        ))

    hot_runs = _runs(rows, lambda row: valid_daylight(row) and row["load_fraction"] >= thresholds.minimum_load_fraction and (float(row["inverter_temperature_c"]) >= thresholds.absolute_temperature_c or (row["load_fraction"] >= .5 and row["temperature_delta_c"] > 8 + 25 * row["load_fraction"] + thresholds.overheat_residual_c)), thresholds)
    for run in hot_runs:
        if len(run) < thresholds.temperature_persistence_samples:
            continue
        residuals = [row["temperature_delta_c"] - (8 + 25 * row["load_fraction"]) for row in run]
        events.append(_event(
            "inverter_overheating", run, max(residuals) / 50,
            {"load_adjusted_temperature_delta_c": round(sum(8 + 25 * row["load_fraction"] for row in run) / len(run), 2)},
            {"maximum_inverter_temperature_c": max(float(row["inverter_temperature_c"]) for row in run), "mean_excess_delta_c": round(sum(residuals) / len(run), 2)},
            "Inverter temperature remained abnormally high relative to ambient temperature and electrical load.",
            "Inspect ventilation, filters, fans, enclosure temperature sensors and site ambient conditions.",
        ))

    shutdown_runs = _runs(rows, lambda row: valid_daylight(row) and float(row.get("irradiance_w_m2") or 0) >= 300 and row["expected_solar_ac_kw"] >= rated * .2 and float(row.get("ac_power_kw") or 0) <= rated * .005 and float(row.get("dc_power_kw") or 0) <= rated * .005, thresholds)
    for run in shutdown_runs:
        if len(run) < thresholds.shutdown_persistence_samples:
            continue
        events.append(_event(
            "unexpected_shutdown", run, 1.0,
            {"mean_expected_ac_kw": round(sum(row["expected_solar_ac_kw"] for row in run) / len(run), 2)},
            {"mean_observed_ac_kw": round(sum(float(row["ac_power_kw"]) for row in run) / len(run), 2)},
            "AC and DC output were effectively zero during valid daytime generation conditions without a declared protection or grid restriction state.",
            "Review event logs, protection trips, DC isolation, auxiliary power and grid availability before restart decisions.",
        ))

    intermittent_runs = [run for run in _runs(rows, lambda row: valid_daylight(row) and row["pv_ratio"] is not None and row["pv_ratio"] < thresholds.intermittent_ratio, thresholds) if len(run) <= thresholds.intermittent_max_run_samples]
    if len(intermittent_runs) >= thresholds.intermittent_min_occurrences:
        contributing = [row for run in intermittent_runs for row in run]
        mean_ratio = sum(row["pv_ratio"] for row in contributing) / len(contributing)
        event = _event(
            "intermittent_derating", contributing, 1 - mean_ratio,
            {"minimum_recurrences": thresholds.intermittent_min_occurrences},
            {"recurrence_count": len(intermittent_runs), "affected_samples": len(contributing), "mean_production_ratio": round(mean_ratio, 3)},
            "Short, recurring power reductions were observed under otherwise valid generation conditions.",
            "Compare control/event logs with grid commands and MPPT behavior; inspect connectors only after evidence review.",
        )
        event["start_timestamp"] = intermittent_runs[0][0]["timestamp"]
        event["end_timestamp"] = intermittent_runs[-1][-1]["timestamp"]
        events.append(event)

    # Data quality is intentionally emitted separately from equipment behavior.
    expected_gap = timedelta(minutes=thresholds.interval_minutes)
    for previous, current in zip(rows, rows[1:]):
        gap = _dt(current["timestamp"]) - _dt(previous["timestamp"])
        if gap > expected_gap * 1.5:
            missing = max(1, round(gap / expected_gap) - 1)
            events.append(_event(
                "data_quality", [previous, current], min(1.0, missing / 8),
                {"interval_minutes": thresholds.interval_minutes}, {"missing_sample_count": missing, "gap_minutes": int(gap.total_seconds() / 60)},
                f"A telemetry gap indicates {missing} missing timestamp(s); missing data is not treated as zero production.",
                "Check ingestion, communications and source historian continuity.", "missing_timestamps",
            ))
    for row in rows:
        errors = validate_telemetry(row)
        if errors:
            events.append(_event(
                "data_quality", [row], 1.0, {"physical_consistency": "AC power should not materially exceed DC input"},
                {"validation_errors": errors}, "Telemetry failed numerical or physical-consistency validation and was excluded from equipment-fault rules.",
                "Validate sensor scaling, units, channel mapping and source quality.", "invalid_measurement",
            ))
    if analysis_end:
        lag = _dt(analysis_end) - _dt(rows[-1]["timestamp"])
        if lag > timedelta(minutes=thresholds.stale_after_minutes):
            events.append(_event(
                "data_quality", [rows[-1]], min(1.0, lag.total_seconds() / 7200),
                {"maximum_staleness_minutes": thresholds.stale_after_minutes}, {"staleness_minutes": int(lag.total_seconds() / 60)},
                "The latest available telemetry is stale relative to the requested analysis window; absence of new data is not a zero-power reading.",
                "Check the data connector, device clock and historian availability.", "stale_telemetry",
            ))
    ordered = sorted(events, key=lambda item: (item["start_timestamp"], item["anomaly_category"]))
    merged: list[dict[str, Any]] = []
    for event in ordered:
        prior = next((item for item in reversed(merged) if item["anomaly_category"] == event["anomaly_category"]), None)
        can_bridge = (
            prior is not None
            and event["anomaly_category"] == "pv_side_underperformance"
            and _dt(event["start_timestamp"]) - _dt(prior["end_timestamp"]) <= timedelta(hours=18)
        )
        if can_bridge:
            prior["end_timestamp"] = event["end_timestamp"]
            prior["duration_minutes"] = int((_dt(prior["end_timestamp"]) - _dt(prior["start_timestamp"])).total_seconds() / 60) + thresholds.interval_minutes
            prior["detection_score"] = max(prior["detection_score"], event["detection_score"])
            prior["contributing_telemetry_refs"].extend(event["contributing_telemetry_refs"])
            prior["observed_measurements"]["episode_count"] = prior["observed_measurements"].get("episode_count", 1) + 1
        else:
            merged.append(event)
    return sorted(merged, key=lambda item: (item["start_timestamp"], item["anomaly_category"]))

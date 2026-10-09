"""Deterministic, explainable event detection for inverter telemetry.

The rules generate investigation alerts, not verified root-cause diagnoses.
Ground-truth scenario labels are intentionally not consumed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .inverter_monitoring import expected_ac_power_kw, parse_iso_datetime, validate_telemetry

METHOD_VERSION = "inverter-rules-v2a.2"


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


def evaluate_synthetic_events(
    detected: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    overlap_tolerance_minutes: int = 30,
) -> dict[str, Any]:
    """Causally valid one-to-one event matching for synthetic evaluation."""
    positive = [item for item in scenarios if item.get("expected_alert_category")]
    tolerance = timedelta(minutes=overlap_tolerance_minutes)
    candidates: list[tuple[float, int, int]] = []
    for prediction_index, prediction in enumerate(detected):
        for truth_index, truth in enumerate(positive):
            if prediction["inverter_id"] != truth["inverter_id"] or prediction["anomaly_category"] != truth["expected_alert_category"]:
                continue
            prediction_start = _dt(prediction.get("onset_timestamp") or prediction["start_timestamp"])
            prediction_end = _dt(prediction.get("last_abnormal_timestamp") or prediction["end_timestamp"])
            truth_start, truth_end = _dt(truth["start_timestamp"]), _dt(truth["end_timestamp"])
            overlap = min(prediction_end, truth_end) - max(prediction_start, truth_start)
            if overlap.total_seconds() >= 0 or (prediction_start <= truth_end + tolerance and prediction_end >= truth_start - tolerance):
                candidates.append((max(0.0, overlap.total_seconds()), prediction_index, truth_index))
    matched_predictions: set[int] = set()
    matched_truths: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, prediction_index, truth_index in sorted(candidates, reverse=True):
        if prediction_index not in matched_predictions and truth_index not in matched_truths:
            matched_predictions.add(prediction_index)
            matched_truths.add(truth_index)
            pairs.append((prediction_index, truth_index))

    tp, fp, fn = len(pairs), len(detected) - len(pairs), len(positive) - len(pairs)
    metric = lambda numerator, denominator: None if denominator == 0 else round(numerator / denominator, 3)
    precision, recall = metric(tp, tp + fp), metric(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else round(2 * precision * recall / (precision + recall), 3)
    latencies: list[float] = []
    onset_errors: list[float] = []
    offset_errors: list[float] = []
    for prediction_index, truth_index in pairs:
        prediction, truth = detected[prediction_index], positive[truth_index]
        onset = _dt(prediction.get("onset_timestamp") or prediction["start_timestamp"])
        eligible = _dt(prediction.get("detection_eligible_timestamp") or prediction["start_timestamp"])
        last_abnormal = _dt(prediction.get("last_abnormal_timestamp") or prediction["end_timestamp"])
        truth_start, truth_end = _dt(truth["start_timestamp"]), _dt(truth["end_timestamp"])
        latencies.append((eligible - truth_start).total_seconds() / 60)
        onset_errors.append((onset - truth_start).total_seconds() / 60)
        offset_errors.append((last_abnormal - truth_end).total_seconds() / 60)

    categories = sorted({item["expected_alert_category"] for item in positive} | {item["anomaly_category"] for item in detected})
    by_type: dict[str, dict[str, Any]] = {}
    for category in categories:
        type_pairs = [(p, t) for p, t in pairs if detected[p]["anomaly_category"] == category]
        type_predictions = sum(item["anomaly_category"] == category for item in detected)
        type_truths = sum(item["expected_alert_category"] == category for item in positive)
        type_tp, type_fp, type_fn = len(type_pairs), type_predictions - len(type_pairs), type_truths - len(type_pairs)
        type_precision, type_recall = metric(type_tp, type_tp + type_fp), metric(type_tp, type_tp + type_fn)
        by_type[category] = {
            "true_positive_events": type_tp, "false_positive_events": type_fp, "false_negative_events": type_fn,
            "precision": type_precision, "recall": type_recall,
            "f1": None if type_precision is None or type_recall is None or type_precision + type_recall == 0 else round(2 * type_precision * type_recall / (type_precision + type_recall), 3),
        }

    compatible_counts: dict[int, int] = {}
    for _, prediction_index, truth_index in candidates:
        compatible_counts[truth_index] = compatible_counts.get(truth_index, 0) + 1
    duplicates = sum(max(0, count - 1) for count in compatible_counts.values())
    inverter_ranges: dict[str, tuple[datetime, datetime]] = {}
    for scenario in scenarios:
        start, end = _dt(scenario["start_timestamp"]), _dt(scenario["end_timestamp"])
        prior = inverter_ranges.get(scenario["inverter_id"])
        inverter_ranges[scenario["inverter_id"]] = (min(start, prior[0]) if prior else start, max(end, prior[1]) if prior else end)
    inverter_days = sum(max(1 / 96, (end - start).total_seconds() / 86400) for start, end in inverter_ranges.values())
    mean = lambda values: None if not values else round(sum(values) / len(values), 2)
    return {
        "scope": "synthetic_scenario_evaluation_only",
        "matching": {"method": "greedy one-to-one maximum temporal overlap", "category_required": True, "overlap_tolerance_minutes": overlap_tolerance_minutes},
        "true_positive_events": tp, "false_positive_events": fp, "false_negative_events": fn,
        "event_level_precision": precision, "event_level_recall": recall, "event_level_f1": f1,
        "false_positive_rate": metric(fp, tp + fp),
        "false_alerts_per_inverter_day": round(fp / inverter_days, 4) if inverter_days else None,
        "mean_detection_latency_minutes": mean(latencies),
        "mean_onset_error_minutes": mean(onset_errors), "mean_offset_error_minutes": mean(offset_errors),
        "duplicate_incident_count": duplicates, "duplicate_alert_count": duplicates,
        "normal_operation_passed": not any(
            prediction["inverter_id"] == scenario["inverter_id"]
            for scenario in scenarios if scenario.get("scenario_type") in {"normal", "normal_operation"}
            for prediction in detected
        ),
        "by_anomaly_type": by_type,
        "limitations": "Metrics use synthetic scenarios and do not represent real-world fault-detection accuracy.",
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
    persistence_samples: int = 1,
    recovery_timestamp: str | None = None,
) -> dict[str, Any]:
    onset = rows[0]["timestamp"]
    last_abnormal = rows[-1]["timestamp"]
    eligible = rows[min(len(rows), persistence_samples) - 1]["timestamp"]
    active_duration = len({row["timestamp"] for row in rows}) * 15
    wall_end = recovery_timestamp or (_dt(last_abnormal) + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    wall_duration = max(active_duration, int((_dt(wall_end) - _dt(onset)).total_seconds() / 60))
    return {
        "anomaly_category": category,
        "anomaly_subtype": subtype,
        "start_timestamp": onset,
        "end_timestamp": last_abnormal,
        "onset_timestamp": onset,
        "detection_eligible_timestamp": eligible,
        "last_abnormal_timestamp": last_abnormal,
        "recovery_timestamp": recovery_timestamp,
        "duration_minutes": wall_duration,
        "wall_clock_duration_minutes": wall_duration,
        "active_duration_minutes": active_duration,
        "excluded_duration_minutes": max(0, wall_duration - active_duration),
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
    last_known_timestamp: str | None = None,
    has_historical_telemetry: bool = False,
    thresholds: DetectionThresholds = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    """Detect time-bounded events from measurements only, never scenario labels."""
    if not readings:
        if not (analysis_end and last_known_timestamp and has_historical_telemetry):
            return []
        lag = _dt(analysis_end) - _dt(last_known_timestamp)
        if lag <= timedelta(minutes=thresholds.stale_after_minutes):
            return []
        synthetic_row = {"timestamp": last_known_timestamp}
        onset = (_dt(last_known_timestamp) + timedelta(minutes=thresholds.interval_minutes)).isoformat().replace("+00:00", "Z")
        event = _event(
            "data_quality", [synthetic_row], min(1.0, lag.total_seconds() / 7200),
            {"expected_interval_minutes": thresholds.interval_minutes},
            {"last_known_timestamp": last_known_timestamp, "outage_minutes": int(lag.total_seconds() / 60)},
            "A configured inverter with prior history has stopped reporting completely; absence of telemetry is not zero production.",
            "Check device connectivity, gateway health, source historian and clock synchronization.", "complete_outage",
        )
        event.update({
            "start_timestamp": onset, "onset_timestamp": onset,
            "detection_eligible_timestamp": (_dt(last_known_timestamp) + timedelta(minutes=thresholds.stale_after_minutes)).isoformat().replace("+00:00", "Z"),
            "last_abnormal_timestamp": analysis_end, "end_timestamp": analysis_end,
            "active_duration_minutes": 0, "excluded_duration_minutes": int(lag.total_seconds() / 60),
            "duration_minutes": int(lag.total_seconds() / 60), "wall_clock_duration_minutes": int(lag.total_seconds() / 60),
        })
        return [event]
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

    def recovery_after(run: list[dict[str, Any]], abnormal: Callable[[dict[str, Any]], bool]) -> str | None:
        last = _dt(run[-1]["timestamp"])
        return next((row["timestamp"] for row in rows if _dt(row["timestamp"]) > last and valid_daylight(row) and not abnormal(row)), None)

    events: list[dict[str, Any]] = []
    ac_runs = _runs(rows, lambda row: valid_daylight(row) and row["expected_ac_from_dc_kw"] >= rated * thresholds.minimum_load_fraction and row["conversion_ratio"] < thresholds.ac_conversion_ratio, thresholds)
    for run in ac_runs:
        if len(run) < thresholds.ac_persistence_samples:
            continue
        ratio = sum(row["conversion_ratio"] for row in run) / len(run)
        predicate = lambda row: valid_daylight(row) and row["expected_ac_from_dc_kw"] >= rated * thresholds.minimum_load_fraction and row["conversion_ratio"] < thresholds.ac_conversion_ratio
        events.append(_event(
            "ac_conversion_underperformance", run, 1 - ratio,
            {"mean_expected_ac_from_dc_kw": round(sum(row["expected_ac_from_dc_kw"] for row in run) / len(run), 2)},
            {"mean_observed_ac_kw": round(sum(float(row["ac_power_kw"]) for row in run) / len(run), 2), "mean_conversion_ratio": round(ratio, 3)},
            "AC output remained below the DC-input-based conversion model at valid load. DC input itself was available, so this is a conversion-path investigation alert.",
            "Check inverter efficiency, grid-side limits, controls and AC measurements. Confirm root cause before maintenance.",
            persistence_samples=thresholds.ac_persistence_samples, recovery_timestamp=recovery_after(run, predicate),
        ))

    pv_runs = _runs(rows, lambda row: valid_daylight(row) and row["expected_dc_kw"] and row["expected_dc_kw"] >= rated * thresholds.minimum_load_fraction and float(row.get("dc_power_kw") or 0) > rated * .02 and row["pv_ratio"] < thresholds.pv_production_ratio, thresholds)
    for run in pv_runs:
        if len(run) < thresholds.pv_persistence_samples:
            continue
        ratio = sum(row["pv_ratio"] for row in run) / len(run)
        predicate = lambda row: valid_daylight(row) and row["expected_dc_kw"] and row["expected_dc_kw"] >= rated * thresholds.minimum_load_fraction and float(row.get("dc_power_kw") or 0) > rated * .02 and row["pv_ratio"] < thresholds.pv_production_ratio
        events.append(_event(
            "pv_side_underperformance", run, 1 - ratio,
            {"mean_modeled_dc_kw": round(sum(row["expected_dc_kw"] for row in run) / len(run), 2)},
            {"mean_observed_dc_kw": round(sum(float(row["dc_power_kw"]) for row in run) / len(run), 2), "mean_production_ratio": round(ratio, 3)},
            "DC production was persistently below the irradiance/temperature baseline while AC conversion remained physically consistent. Cause is unconfirmed without string or MPPT evidence.",
            "Review irradiance quality, strings, MPPT channels, soiling/shading and DC availability before assigning a cause.",
            persistence_samples=thresholds.pv_persistence_samples, recovery_timestamp=recovery_after(run, predicate),
        ))

    hot_runs = _runs(rows, lambda row: valid_daylight(row) and row["load_fraction"] >= thresholds.minimum_load_fraction and (float(row["inverter_temperature_c"]) >= thresholds.absolute_temperature_c or (row["load_fraction"] >= .5 and row["temperature_delta_c"] > 8 + 25 * row["load_fraction"] + thresholds.overheat_residual_c)), thresholds)
    for run in hot_runs:
        if len(run) < thresholds.temperature_persistence_samples:
            continue
        residuals = [row["temperature_delta_c"] - (8 + 25 * row["load_fraction"]) for row in run]
        predicate = lambda row: valid_daylight(row) and row["load_fraction"] >= thresholds.minimum_load_fraction and (float(row["inverter_temperature_c"]) >= thresholds.absolute_temperature_c or (row["load_fraction"] >= .5 and row["temperature_delta_c"] > 8 + 25 * row["load_fraction"] + thresholds.overheat_residual_c))
        events.append(_event(
            "inverter_overheating", run, max(residuals) / 50,
            {"load_adjusted_temperature_delta_c": round(sum(8 + 25 * row["load_fraction"] for row in run) / len(run), 2)},
            {"maximum_inverter_temperature_c": max(float(row["inverter_temperature_c"]) for row in run), "mean_excess_delta_c": round(sum(residuals) / len(run), 2)},
            "Inverter temperature remained abnormally high relative to ambient temperature and electrical load.",
            "Inspect ventilation, filters, fans, enclosure temperature sensors and site ambient conditions.",
            persistence_samples=thresholds.temperature_persistence_samples, recovery_timestamp=recovery_after(run, predicate),
        ))

    shutdown_runs = _runs(rows, lambda row: valid_daylight(row) and float(row.get("irradiance_w_m2") or 0) >= 300 and row["expected_solar_ac_kw"] >= rated * .2 and float(row.get("ac_power_kw") or 0) <= rated * .005 and float(row.get("dc_power_kw") or 0) <= rated * .005, thresholds)
    for run in shutdown_runs:
        if len(run) < thresholds.shutdown_persistence_samples:
            continue
        predicate = lambda row: valid_daylight(row) and float(row.get("irradiance_w_m2") or 0) >= 300 and row["expected_solar_ac_kw"] >= rated * .2 and float(row.get("ac_power_kw") or 0) <= rated * .005 and float(row.get("dc_power_kw") or 0) <= rated * .005
        events.append(_event(
            "unexpected_shutdown", run, 1.0,
            {"mean_expected_ac_kw": round(sum(row["expected_solar_ac_kw"] for row in run) / len(run), 2)},
            {"mean_observed_ac_kw": round(sum(float(row["ac_power_kw"]) for row in run) / len(run), 2)},
            "AC and DC output were effectively zero during valid daytime generation conditions without a declared protection or grid restriction state.",
            "Review event logs, protection trips, DC isolation, auxiliary power and grid availability before restart decisions.",
            persistence_samples=thresholds.shutdown_persistence_samples, recovery_timestamp=recovery_after(run, predicate),
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
            persistence_samples=1,
        )
        event["start_timestamp"] = intermittent_runs[0][0]["timestamp"]
        event["end_timestamp"] = intermittent_runs[-1][-1]["timestamp"]
        event["onset_timestamp"] = event["start_timestamp"]
        event["detection_eligible_timestamp"] = intermittent_runs[thresholds.intermittent_min_occurrences - 1][-1]["timestamp"]
        event["last_abnormal_timestamp"] = event["end_timestamp"]
        event["active_duration_minutes"] = len(contributing) * thresholds.interval_minutes
        event["recovery_timestamp"] = next((row["timestamp"] for row in rows if _dt(row["timestamp"]) > _dt(event["end_timestamp"]) and valid_daylight(row) and (row["pv_ratio"] or 0) >= thresholds.intermittent_ratio), None)
        wall_end = event["recovery_timestamp"] or (_dt(event["end_timestamp"]) + timedelta(minutes=thresholds.interval_minutes)).isoformat().replace("+00:00", "Z")
        event["wall_clock_duration_minutes"] = int((_dt(wall_end) - _dt(event["onset_timestamp"])).total_seconds() / 60)
        event["duration_minutes"] = event["wall_clock_duration_minutes"]
        event["excluded_duration_minutes"] = max(0, event["wall_clock_duration_minutes"] - event["active_duration_minutes"])
        events.append(event)

    # Data quality is intentionally emitted separately from equipment behavior.
    expected_gap = timedelta(minutes=thresholds.interval_minutes)
    for previous, current in zip(rows, rows[1:]):
        gap = _dt(current["timestamp"]) - _dt(previous["timestamp"])
        if gap > expected_gap * 1.5:
            missing = max(1, round(gap / expected_gap) - 1)
            event = _event(
                "data_quality", [previous, current], min(1.0, missing / 8),
                {"interval_minutes": thresholds.interval_minutes}, {"missing_sample_count": missing, "gap_minutes": int(gap.total_seconds() / 60)},
                f"A telemetry gap indicates {missing} missing timestamp(s); missing data is not treated as zero production.",
                "Check ingestion, communications and source historian continuity.", "missing_timestamps",
            )
            event["onset_timestamp"] = (_dt(previous["timestamp"]) + expected_gap).isoformat().replace("+00:00", "Z")
            event["start_timestamp"] = event["onset_timestamp"]
            event["detection_eligible_timestamp"] = current["timestamp"]
            event["last_abnormal_timestamp"] = current["timestamp"]
            event["end_timestamp"] = current["timestamp"]
            event["active_duration_minutes"] = 0
            event["excluded_duration_minutes"] = int(gap.total_seconds() / 60)
            events.append(event)
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
            event = _event(
                "data_quality", [rows[-1]], min(1.0, lag.total_seconds() / 7200),
                {"maximum_staleness_minutes": thresholds.stale_after_minutes}, {"staleness_minutes": int(lag.total_seconds() / 60)},
                "The latest available telemetry is stale relative to the requested analysis window; absence of new data is not a zero-power reading.",
                "Check the data connector, device clock and historian availability.", "stale_telemetry",
            )
            event["onset_timestamp"] = (_dt(rows[-1]["timestamp"]) + expected_gap).isoformat().replace("+00:00", "Z")
            event["start_timestamp"] = event["onset_timestamp"]
            event["detection_eligible_timestamp"] = (_dt(rows[-1]["timestamp"]) + timedelta(minutes=thresholds.stale_after_minutes)).isoformat().replace("+00:00", "Z")
            event["last_abnormal_timestamp"] = analysis_end
            event["end_timestamp"] = analysis_end
            event["active_duration_minutes"] = 0
            event["excluded_duration_minutes"] = int(lag.total_seconds() / 60)
            event["duration_minutes"] = int(lag.total_seconds() / 60)
            event["wall_clock_duration_minutes"] = int(lag.total_seconds() / 60)
            events.append(event)
    ordered = sorted(events, key=lambda item: (item["start_timestamp"], item["anomaly_category"]))
    merged: list[dict[str, Any]] = []
    for event in ordered:
        prior = next((item for item in reversed(merged) if item["anomaly_category"] == event["anomaly_category"]), None)
        can_bridge = (
            prior is not None
            and event["anomaly_category"] == "pv_side_underperformance"
            and _dt(event["onset_timestamp"]) - _dt(prior["last_abnormal_timestamp"]) <= timedelta(hours=18)
            and (prior.get("recovery_timestamp") is None or _dt(prior["recovery_timestamp"]) > _dt(event["onset_timestamp"]))
        )
        if can_bridge:
            prior["end_timestamp"] = event["end_timestamp"]
            prior["last_abnormal_timestamp"] = event["last_abnormal_timestamp"]
            prior["recovery_timestamp"] = event.get("recovery_timestamp")
            prior["active_duration_minutes"] += event["active_duration_minutes"]
            wall_end = prior["recovery_timestamp"] or (_dt(prior["last_abnormal_timestamp"]) + timedelta(minutes=thresholds.interval_minutes)).isoformat().replace("+00:00", "Z")
            prior["wall_clock_duration_minutes"] = int((_dt(wall_end) - _dt(prior["onset_timestamp"])).total_seconds() / 60)
            prior["duration_minutes"] = prior["wall_clock_duration_minutes"]
            prior["excluded_duration_minutes"] = max(0, prior["wall_clock_duration_minutes"] - prior["active_duration_minutes"])
            prior["detection_score"] = max(prior["detection_score"], event["detection_score"])
            prior["contributing_telemetry_refs"].extend(event["contributing_telemetry_refs"])
            prior["observed_measurements"]["episode_count"] = prior["observed_measurements"].get("episode_count", 1) + 1
        else:
            merged.append(event)
    return sorted(merged, key=lambda item: (item["start_timestamp"], item["anomaly_category"]))

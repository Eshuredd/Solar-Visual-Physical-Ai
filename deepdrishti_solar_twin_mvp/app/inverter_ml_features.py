"""Causal, interpretable inverter features for experimental novelty detection."""
from __future__ import annotations

from collections import deque
from statistics import median, pstdev
from typing import Any

from .inverter_monitoring import expected_ac_power_kw, validate_telemetry

FEATURE_VERSION = "inverter-features-v1"
FEATURE_NAMES = [
    "normalized_ac_power", "normalized_dc_power", "conversion_efficiency",
    "expected_ac_residual", "expected_dc_residual", "production_ratio",
    "dc_power_balance_error", "inverter_temperature_c", "ambient_temperature_c",
    "temperature_rise_c", "temperature_load_residual_c", "recent_ac_change",
    "rolling_ac_variability", "rolling_efficiency_variation",
    "rolling_expected_residual", "negative_residual_persistence",
    "peer_relative_difference",
]
EXCLUDED_STATES = {"Night", "Startup", "Shutdown", "Curtailment", "Protection", "Grid Restricted", "Missing"}


def build_peer_context(equipment: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> dict[str, float]:
    """Return timestamp medians of capacity-normalized AC production."""
    values: dict[str, list[float]] = {}
    for inverter, readings in equipment:
        rated = float(inverter["rated_ac_power_kw"])
        for row in readings:
            if row.get("operating_state") in EXCLUDED_STATES or float(row.get("irradiance_w_m2") or 0) < 200 or validate_telemetry(row):
                continue
            values.setdefault(row["timestamp"], []).append(float(row["ac_power_kw"]) / rated)
    return {timestamp: median(items) for timestamp, items in values.items() if items}


def build_feature_rows(
    inverter: dict[str, Any],
    readings: list[dict[str, Any]],
    peer_context: dict[str, float] | None = None,
    rolling_window: int = 4,
) -> list[dict[str, Any]]:
    """Build features using the current and preceding samples only."""
    rated = float(inverter["rated_ac_power_kw"])
    ac_history: deque[float] = deque(maxlen=rolling_window)
    efficiency_history: deque[float] = deque(maxlen=rolling_window)
    residual_history: deque[float] = deque(maxlen=rolling_window)
    previous_ac: float | None = None
    persistence = 0
    result: list[dict[str, Any]] = []
    for source in sorted(readings, key=lambda item: item["timestamp"]):
        if source.get("operating_state") in EXCLUDED_STATES or float(source.get("irradiance_w_m2") or 0) < 200 or validate_telemetry(source):
            continue
        ac = float(source["ac_power_kw"])
        dc = float(source["dc_power_kw"])
        expected_ac = expected_ac_power_kw(rated, source.get("irradiance_w_m2"), source.get("inverter_temperature_c")) or 0.0
        expected_dc = expected_ac / .975 if expected_ac else 0.0
        normalized_ac, normalized_dc = ac / rated, dc / rated
        efficiency = ac / dc if dc > 0 else 0.0
        ac_residual = (ac - expected_ac) / rated
        dc_residual = (dc - expected_dc) / rated
        production_ratio = ac / expected_ac if expected_ac > 0 else 1.0
        balance_error = (dc * 1000 - float(source["dc_voltage_v"]) * float(source["dc_current_a"])) / (rated * 1000)
        load = normalized_ac
        temperature_rise = float(source["inverter_temperature_c"]) - float(source["ambient_temperature_c"])
        temperature_residual = temperature_rise - (8 + 25 * load)
        recent_change = 0.0 if previous_ac is None else (ac - previous_ac) / rated
        ac_history.append(normalized_ac)
        efficiency_history.append(efficiency)
        residual_history.append(ac_residual)
        persistence = persistence + 1 if ac_residual < -.15 else 0
        feature_map = {
            "normalized_ac_power": normalized_ac,
            "normalized_dc_power": normalized_dc,
            "conversion_efficiency": efficiency,
            "expected_ac_residual": ac_residual,
            "expected_dc_residual": dc_residual,
            "production_ratio": production_ratio,
            "dc_power_balance_error": balance_error,
            "inverter_temperature_c": float(source["inverter_temperature_c"]),
            "ambient_temperature_c": float(source["ambient_temperature_c"]),
            "temperature_rise_c": temperature_rise,
            "temperature_load_residual_c": temperature_residual,
            "recent_ac_change": recent_change,
            "rolling_ac_variability": pstdev(ac_history) if len(ac_history) > 1 else 0.0,
            "rolling_efficiency_variation": pstdev(efficiency_history) if len(efficiency_history) > 1 else 0.0,
            "rolling_expected_residual": sum(residual_history) / len(residual_history),
            "negative_residual_persistence": float(persistence),
            "peer_relative_difference": normalized_ac - (peer_context or {}).get(source["timestamp"], normalized_ac),
        }
        result.append({
            "inverter_id": inverter["inverter_id"], "timestamp": source["timestamp"],
            "features": [feature_map[name] for name in FEATURE_NAMES], "feature_map": feature_map,
            "feature_version": FEATURE_VERSION, "data_source": source.get("data_source"),
        })
        previous_ac = ac
    return result

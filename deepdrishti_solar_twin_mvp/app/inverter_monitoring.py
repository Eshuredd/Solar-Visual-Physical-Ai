"""Transparent inverter performance calculations for the demonstration twin.

All power values are kW, energy values are kWh, irradiance is W/m2, and
temperatures are degrees Celsius.  This module deliberately contains no ML:
its outputs are explainable operational indicators for synthetic telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class MonitoringThresholds:
    minimum_irradiance_w_m2: float = 100.0
    minimum_expected_power_fraction: float = 0.05
    healthy_relative_yield_pct: float = 90.0
    watch_relative_yield_pct: float = 75.0
    minimum_efficiency_pct: float = 90.0
    high_temperature_c: float = 75.0
    sample_interval_hours: float = 0.25


DEFAULT_THRESHOLDS = MonitoringThresholds()
EXCLUDED_STATES = {"Night", "Shutdown", "Startup", "Curtailment", "Missing"}


def validate_telemetry(reading: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors without mutating a reading."""
    limits = {
        "dc_power_kw": (0, 100_000), "ac_power_kw": (0, 100_000),
        "dc_voltage_v": (0, 2_000), "dc_current_a": (0, 100_000),
        "ac_voltage_v": (0, 1_500), "ac_current_a": (0, 100_000),
        "inverter_temperature_c": (-40, 150), "ambient_temperature_c": (-60, 80),
        "irradiance_w_m2": (0, 1_500),
    }
    errors: list[str] = []
    for field, (minimum, maximum) in limits.items():
        value = reading.get(field)
        if value is None:
            errors.append(f"{field} is missing")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{field} must be numeric")
            continue
        if not minimum <= number <= maximum:
            errors.append(f"{field} must be between {minimum} and {maximum}")
    if reading.get("dc_power_kw") is not None and reading.get("ac_power_kw") is not None:
        if float(reading["ac_power_kw"]) > float(reading["dc_power_kw"]) * 1.02 + 0.01:
            errors.append("ac_power_kw cannot materially exceed dc_power_kw")
    return errors


def conversion_efficiency_pct(dc_power_kw: float | None, ac_power_kw: float | None) -> float | None:
    """DC-to-AC conversion efficiency; unavailable when DC input is absent."""
    if dc_power_kw is None or ac_power_kw is None or dc_power_kw <= 0:
        return None
    return round(max(0.0, min(100.0, ac_power_kw / dc_power_kw * 100)), 2)


def expected_ac_power_kw(
    rated_ac_power_kw: float,
    irradiance_w_m2: float | None,
    inverter_temperature_c: float | None,
) -> float | None:
    """Simple capacity/irradiance baseline with a temperature derate above 25 C."""
    if irradiance_w_m2 is None or irradiance_w_m2 < 0 or rated_ac_power_kw <= 0:
        return None
    temperature = 25.0 if inverter_temperature_c is None else inverter_temperature_c
    temperature_factor = max(0.78, 1.0 - max(0.0, temperature - 25.0) * 0.0035)
    return round(min(rated_ac_power_kw, rated_ac_power_kw * irradiance_w_m2 / 1000.0 * temperature_factor), 3)


def energy_kwh(readings: Iterable[dict[str, Any]], interval_hours: float = 0.25, field: str = "ac_power_kw") -> float:
    """Integrate regularly sampled power using the rectangular interval rule."""
    total = sum(max(0.0, float(item.get(field) or 0.0)) * interval_hours for item in readings)
    return round(total, 3)


def is_comparable(reading: dict[str, Any], expected_kw: float | None, thresholds: MonitoringThresholds = DEFAULT_THRESHOLDS) -> bool:
    state = str(reading.get("operating_state") or "Missing")
    irradiance = reading.get("irradiance_w_m2")
    if state in EXCLUDED_STATES or irradiance is None or expected_kw is None:
        return False
    return float(irradiance) >= thresholds.minimum_irradiance_w_m2 and expected_kw > 0


def relative_yield_pct(
    readings: Iterable[dict[str, Any]],
    rated_ac_power_kw: float,
    thresholds: MonitoringThresholds = DEFAULT_THRESHOLDS,
) -> float | None:
    """Irradiance/temperature-normalized actual energy divided by modeled energy."""
    actual = 0.0
    expected = 0.0
    for reading in readings:
        baseline = expected_ac_power_kw(rated_ac_power_kw, reading.get("irradiance_w_m2"), reading.get("inverter_temperature_c"))
        if is_comparable(reading, baseline, thresholds):
            actual += max(0.0, float(reading.get("ac_power_kw") or 0.0)) * thresholds.sample_interval_hours
            expected += float(baseline) * thresholds.sample_interval_hours
    return None if expected <= 0 else round(actual / expected * 100, 2)


def classify_status(
    relative_yield: float | None,
    latest: dict[str, Any] | None,
    thresholds: MonitoringThresholds = DEFAULT_THRESHOLDS,
) -> str:
    """Classify performance without turning normal night/low-light states into faults."""
    if latest is None:
        return "No Data"
    state = str(latest.get("operating_state") or "Missing")
    irradiance = float(latest.get("irradiance_w_m2") or 0.0)
    if state == "Missing":
        return "No Data"
    if state == "Curtailment":
        return "Curtailed"
    if relative_yield is None:
        return "Night" if state in {"Night", "Shutdown"} and irradiance < thresholds.minimum_irradiance_w_m2 else "Insufficient Data"
    if relative_yield < thresholds.watch_relative_yield_pct:
        return "Critical"
    if relative_yield < thresholds.healthy_relative_yield_pct:
        return "Watch"
    return "Healthy"


def summarize_inverter(
    inverter: dict[str, Any],
    readings: list[dict[str, Any]],
    thresholds: MonitoringThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    rated = float(inverter["rated_ac_power_kw"])
    enriched: list[dict[str, Any]] = []
    for raw in readings:
        item = dict(raw)
        item["expected_ac_power_kw"] = expected_ac_power_kw(rated, item.get("irradiance_w_m2"), item.get("inverter_temperature_c"))
        item["conversion_efficiency_pct"] = conversion_efficiency_pct(item.get("dc_power_kw"), item.get("ac_power_kw"))
        enriched.append(item)
    relative_yield = relative_yield_pct(enriched, rated, thresholds)
    latest = enriched[-1] if enriched else None
    comparable = [item for item in enriched if is_comparable(item, item["expected_ac_power_kw"], thresholds)]
    efficiencies = [item["conversion_efficiency_pct"] for item in comparable if item["conversion_efficiency_pct"] is not None]
    indicators: list[dict[str, str]] = []
    if relative_yield is not None and relative_yield < thresholds.healthy_relative_yield_pct:
        severity = "critical" if relative_yield < thresholds.watch_relative_yield_pct else "warning"
        indicators.append({"code": "LOW_RELATIVE_YIELD", "severity": severity, "message": f"Relative yield is {relative_yield:.1f}% of the modeled baseline."})
    abnormal_states = sum(1 for item in enriched if item.get("operating_state") in {"Derated", "Fault"} and float(item.get("irradiance_w_m2") or 0) >= thresholds.minimum_irradiance_w_m2)
    if abnormal_states:
        indicators.append({"code": "ABNORMAL_OPERATING_STATE", "severity": "warning", "message": f"{abnormal_states} daylight samples report a derated or abnormal state."})
    if any(float(item.get("inverter_temperature_c") or 0) >= thresholds.high_temperature_c for item in enriched):
        indicators.append({"code": "HIGH_TEMPERATURE", "severity": "warning", "message": "Inverter temperature crossed the configured demonstration threshold."})
    actual_energy = energy_kwh(comparable, thresholds.sample_interval_hours)
    expected_energy = energy_kwh(comparable, thresholds.sample_interval_hours, "expected_ac_power_kw")
    return {
        "inverter_id": inverter["inverter_id"],
        "period_start": enriched[0]["timestamp"] if enriched else None,
        "period_end": enriched[-1]["timestamp"] if enriched else None,
        "sample_count": len(enriched),
        "comparable_sample_count": len(comparable),
        "latest": latest,
        "actual_energy_kwh": actual_energy,
        "expected_energy_kwh": expected_energy,
        "energy_variance_kwh": round(actual_energy - expected_energy, 3),
        "relative_yield_pct": relative_yield,
        "average_conversion_efficiency_pct": round(sum(efficiencies) / len(efficiencies), 2) if efficiencies else None,
        "status": classify_status(relative_yield, latest, thresholds),
        "indicators": indicators,
        "methodology": "Expected AC power = rated AC capacity x irradiance/1000 x temperature factor; low-light, night, startup, shutdown, curtailment and missing samples are excluded from performance comparison.",
        "data_classification": "synthetic_demo",
    }


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

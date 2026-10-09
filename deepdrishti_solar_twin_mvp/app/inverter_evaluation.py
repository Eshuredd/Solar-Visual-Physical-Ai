"""Independent, reproducible synthetic hold-out generator for rule robustness tests."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

HELD_OUT_GENERATOR_VERSION = "heldout-solar-v1"
HELD_OUT_SEEDS = (731, 1291, 2027)


def generate_held_out_cases(seeds: tuple[int, ...] = HELD_OUT_SEEDS) -> dict[str, Any]:
    """Generate noisy scenarios whose labels are never added to telemetry features."""
    cases: list[dict[str, Any]] = []
    scenario_types = (
        "pv_side_underperformance", "ac_conversion_underperformance", "inverter_overheating",
        "unexpected_shutdown", "intermittent_derating", "data_quality", "normal", "grid_curtailment",
    )
    for seed_index, seed in enumerate(seeds):
        for case_index, scenario_type in enumerate(scenario_types):
            rng = random.Random(seed * 100 + case_index)
            inverter_id = f"heldout-{seed}-{case_index:02d}"
            rated = 4200.0 + rng.uniform(-350, 350)
            start = datetime(2026, 9, 1 + seed_index * 3, tzinfo=timezone.utc)
            fault_start = start + timedelta(days=1, hours=9 + rng.randrange(0, 3), minutes=15 * rng.randrange(0, 3))
            duration_samples = rng.randrange(6, 15)
            if scenario_type == "unexpected_shutdown":
                duration_samples = rng.randrange(2, 7)
            fault_end = fault_start + timedelta(minutes=15 * (duration_samples - 1))
            readings: list[dict[str, Any]] = []
            intermittent_points = {45, 46, 87, 88, 137, 138, 139}
            for sample in range(2 * 96):
                timestamp = start + timedelta(minutes=15 * sample)
                hour = timestamp.hour + timestamp.minute / 60
                solar = max(0.0, math.sin(math.pi * (hour - 6.1) / 12.1)) if 6.1 <= hour <= 18.2 else 0.0
                cloud = max(.62, min(1.04, .9 + .09 * math.sin(sample * rng.uniform(.07, .19)) + rng.gauss(0, .025)))
                irradiance = max(0.0, min(1120.0, 1010 * solar * cloud + rng.gauss(0, 7)))
                ambient = 21 + rng.uniform(-2, 3) + 12 * max(0.0, math.sin(math.pi * (hour - 8) / 14))
                inverter_temp = ambient + irradiance / 1000 * rng.uniform(27, 34) + rng.gauss(0, .5)
                temperature_factor = max(.78, 1 - max(0.0, inverter_temp - 25) * .0035)
                expected_ac = min(rated, rated * irradiance / 1000 * temperature_factor)
                efficiency = rng.uniform(.966, .982)
                ac_power = max(0.0, expected_ac * rng.uniform(.97, 1.015))
                dc_power = ac_power / efficiency if ac_power else 0.0
                state = "Running" if irradiance >= 100 else "Startup" if irradiance > 0 else "Night"
                in_window = fault_start <= timestamp <= fault_end
                if scenario_type == "pv_side_underperformance" and in_window:
                    factor = rng.uniform(.48, .72)
                    dc_power *= factor
                    ac_power *= factor
                elif scenario_type == "ac_conversion_underperformance" and in_window:
                    ac_power *= rng.uniform(.62, .84)
                elif scenario_type == "inverter_overheating" and in_window:
                    inverter_temp += rng.uniform(18, 29)
                elif scenario_type == "unexpected_shutdown" and in_window:
                    dc_power = ac_power = 0.0
                    state = "Running"
                elif scenario_type == "intermittent_derating" and sample in intermittent_points and irradiance >= 200:
                    factor = rng.uniform(.38, .65)
                    dc_power *= factor
                    ac_power *= factor
                elif scenario_type == "grid_curtailment" and in_window:
                    dc_power *= .55
                    ac_power *= .55
                    state = "Curtailment"
                if scenario_type == "data_quality" and fault_start <= timestamp <= fault_end:
                    continue
                dc_voltage = 0.0 if irradiance <= 0 else 880 + 90 * solar
                ac_voltage = 0.0 if ac_power <= 0 else 690 + rng.gauss(0, 2)
                readings.append({
                    "inverter_id": inverter_id, "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "dc_power_kw": round(dc_power, 3), "ac_power_kw": round(ac_power, 3),
                    "dc_voltage_v": round(dc_voltage, 3), "dc_current_a": round(dc_power * 1000 / dc_voltage, 3) if dc_voltage else 0.0,
                    "ac_voltage_v": round(ac_voltage, 3), "ac_current_a": round(ac_power * 1000 / (math.sqrt(3) * ac_voltage * .99), 3) if ac_voltage else 0.0,
                    "inverter_temperature_c": round(inverter_temp, 3), "ambient_temperature_c": round(ambient, 3),
                    "irradiance_w_m2": round(irradiance, 3), "operating_state": state,
                    "data_source": f"{HELD_OUT_GENERATOR_VERSION}:seed-{seed}",
                })
            expected_category = None if scenario_type in {"normal", "grid_curtailment"} else scenario_type
            scenario_end = fault_end
            if scenario_type == "intermittent_derating":
                affected = [start + timedelta(minutes=15 * point) for point in intermittent_points if point < 192]
                fault_start, scenario_end = min(affected), max(affected)
            cases.append({
                "inverter": {"inverter_id": inverter_id, "site_id": "heldout-site", "rated_ac_power_kw": rated, "data_source": "synthetic_holdout"},
                "readings": readings,
                "analysis_end": (start + timedelta(days=2) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
                "scenario": {
                    "scenario_id": f"holdout-{seed}-{scenario_type}", "inverter_id": inverter_id,
                    "scenario_type": scenario_type, "start_timestamp": fault_start.isoformat().replace("+00:00", "Z"),
                    "end_timestamp": scenario_end.isoformat().replace("+00:00", "Z"),
                    "expected_alert_category": expected_category,
                },
            })
    return {"generator_version": HELD_OUT_GENERATOR_VERSION, "seeds": list(seeds), "cases": cases}

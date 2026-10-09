"""Seed-isolated development and final synthetic partitions for Phase 2B."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from .inverter_evaluation import generate_held_out_cases
from .inverter_ml_features import build_feature_rows, build_peer_context
from .inverter_monitoring import parse_iso_datetime

DATASET_VERSION = "inverter-ml-synthetic-v2"
PARTITION_SEEDS = {
    "train": (101, 103, 107, 109, 113),
    "validation": (211, 223),
    "test": (307, 311),
    "final": (401, 409),
}
LOCKED_HISTORICAL_SEEDS = (731, 1291, 2027)
DEVELOPMENT_PROFILES = {
    "train": (
        ("2025-01-05", .78, -8.0), ("2025-03-05", .90, -3.0), ("2025-05-05", 1.00, 2.0),
        ("2025-07-05", 1.04, 6.0), ("2025-09-05", .94, 2.0),
    ),
    "validation": (("2025-02-05", .84, -5.0), ("2025-06-05", 1.03, 5.0)),
    "test": (("2025-04-05", .96, 0.0), ("2025-08-05", 1.01, 5.0)),
    "final": (("2025-10-05", .88, 0.0), ("2025-12-05", .74, -7.0)),
}


def _development_cases(name: str, seeds: tuple[int, ...]) -> dict[str, Any]:
    """Vary season and calendar period without changing the locked generator."""
    generated = deepcopy(generate_held_out_cases(seeds))
    profiles = DEVELOPMENT_PROFILES[name]
    for case in generated["cases"]:
        seed = int(case["inverter"]["inverter_id"].split("-")[1])
        seed_index = seeds.index(seed)
        date_text, irradiance_factor, ambient_offset = profiles[seed_index]
        old_start = parse_iso_datetime(case["readings"][0]["timestamp"])
        new_start = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
        shift = new_start - old_start
        rated = float(case["inverter"]["rated_ac_power_kw"])
        for row in case["readings"]:
            row["timestamp"] = (parse_iso_datetime(row["timestamp"]) + shift).isoformat().replace("+00:00", "Z")
            old_irradiance = float(row["irradiance_w_m2"])
            new_irradiance = min(1200.0, old_irradiance * irradiance_factor)
            power_factor = 1.0 if old_irradiance <= 0 else new_irradiance / old_irradiance
            row["irradiance_w_m2"] = round(new_irradiance, 3)
            row["ambient_temperature_c"] = round(float(row["ambient_temperature_c"]) + ambient_offset, 3)
            row["inverter_temperature_c"] = round(float(row["inverter_temperature_c"]) + ambient_offset, 3)
            row["ac_power_kw"] = round(min(rated, float(row["ac_power_kw"]) * power_factor), 3)
            row["dc_power_kw"] = round(float(row["dc_power_kw"]) * power_factor, 3)
            dc_voltage = float(row["dc_voltage_v"])
            ac_voltage = float(row["ac_voltage_v"])
            row["dc_current_a"] = round(row["dc_power_kw"] * 1000 / dc_voltage, 3) if dc_voltage else 0.0
            row["ac_current_a"] = round(row["ac_power_kw"] * 1000 / (math.sqrt(3) * ac_voltage * .99), 3) if ac_voltage else 0.0
        scenario = case["scenario"]
        scenario["start_timestamp"] = (parse_iso_datetime(scenario["start_timestamp"]) + shift).isoformat().replace("+00:00", "Z")
        scenario["end_timestamp"] = (parse_iso_datetime(scenario["end_timestamp"]) + shift).isoformat().replace("+00:00", "Z")
        case["analysis_end"] = (parse_iso_datetime(case["analysis_end"]) + shift).isoformat().replace("+00:00", "Z")
        case["inverter"]["development_profile"] = {
            "calendar_start": date_text, "irradiance_factor": irradiance_factor, "ambient_offset_c": ambient_offset,
        }
    generated["dataset_version"] = DATASET_VERSION
    generated["partition"] = name
    return generated


def build_partition(name: str) -> dict[str, Any]:
    if name not in PARTITION_SEEDS:
        raise ValueError(f"Unknown partition: {name}")
    return build_seed_partition(name, PARTITION_SEEDS[name], development=True)


def build_seed_partition(name: str, seeds: tuple[int, ...], development: bool = False) -> dict[str, Any]:
    """Build an explicitly named partition from caller-supplied generation seeds."""
    generated = _development_cases(name, seeds) if development else generate_held_out_cases(seeds)
    cases = generated["cases"]
    peer_context = build_peer_context([(case["inverter"], case["readings"]) for case in cases])
    feature_rows: list[dict[str, Any]] = []
    normal_rows: list[dict[str, Any]] = []
    for case in cases:
        scenario = case["scenario"]
        rows = build_feature_rows(case["inverter"], case["readings"], peer_context)
        feature_rows.extend(rows)
        if scenario.get("expected_alert_category") is None:
            normal_rows.extend(rows)
            continue
        start = parse_iso_datetime(scenario["start_timestamp"]) - timedelta(hours=1)
        end = parse_iso_datetime(scenario["end_timestamp"]) + timedelta(hours=1)
        normal_rows.extend(row for row in rows if not start <= parse_iso_datetime(row["timestamp"]) <= end)
    return {
        "name": name, "dataset_version": DATASET_VERSION, "seeds": list(seeds),
        "cases": cases, "feature_rows": feature_rows, "normal_feature_rows": normal_rows,
        "scenarios": [case["scenario"] for case in cases],
    }


def dataset_audit() -> dict[str, Any]:
    partitions = {name: build_partition(name) for name in PARTITION_SEEDS}
    return {
        "dataset_version": DATASET_VERSION,
        "partitions": {
            name: {
                "seeds": data["seeds"], "cases": len(data["cases"]),
                "scored_operating_rows": len(data["feature_rows"]),
                "validated_normal_training_rows": len(data["normal_feature_rows"]),
            }
            for name, data in partitions.items()
        },
        "locked_historical_seeds": list(LOCKED_HISTORICAL_SEEDS),
        "leakage_controls": [
            "disjoint generation seeds", "non-overlapping seasonally varied two-day operating periods",
            "no inverter identifier feature", "causal rolling features", "training-normal rows only",
            "ground-truth labels excluded from feature vectors",
        ],
    }

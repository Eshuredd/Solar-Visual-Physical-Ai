"""Explicit, offline Phase 2B model training and evaluation command.

Run from the project directory with: ``python -m app.train_inverter_ml``.
The web API deliberately never invokes this module.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .inverter_anomaly import METHOD_VERSION, detect_anomaly_events, evaluate_synthetic_events
from .inverter_ml import (
    DEFAULT_ARTIFACT_ROOT,
    MODEL_VERSION,
    IsolationForestDetector,
    candidate_events,
    save_artifact,
)
from .inverter_ml_baselines import BASELINE_VERSION, RobustResidualDetector
from .inverter_ml_dataset import (
    LOCKED_HISTORICAL_SEEDS,
    build_partition,
    build_seed_partition,
    dataset_audit,
)


def _rules(partition: dict[str, Any]) -> list[dict[str, Any]]:
    detected: list[dict[str, Any]] = []
    for case in partition["cases"]:
        for event in detect_anomaly_events(case["inverter"], case["readings"], analysis_end=case["analysis_end"]):
            if event["anomaly_category"] != "data_quality":
                detected.append({**event, "inverter_id": case["inverter"]["inverter_id"]})
    return detected


def _equipment_scenarios(partition: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in partition["scenarios"] if item.get("expected_alert_category") != "data_quality"]


def _hybrid(rules: list[dict[str, Any]], ml: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union review candidates, preferring an overlapping deterministic rule event."""
    result = list(rules)
    for candidate in ml:
        duplicate = any(
            event["inverter_id"] == candidate["inverter_id"]
            and event["anomaly_category"] == candidate["anomaly_category"]
            and event["start_timestamp"] <= candidate["end_timestamp"]
            and candidate["start_timestamp"] <= event["end_timestamp"]
            for event in rules
        )
        if not duplicate:
            result.append(candidate)
    return result


def evaluate_partition(
    partition: dict[str, Any],
    baseline: RobustResidualDetector,
    isolation_forest: IsolationForestDetector,
) -> dict[str, Any]:
    started = time.perf_counter()
    rule_events = _rules(partition)
    rule_runtime = time.perf_counter() - started
    started = time.perf_counter()
    statistical_events = candidate_events(
        baseline.score_rows(partition["feature_rows"]), baseline.threshold or 0,
        "robust_statistical_baseline", BASELINE_VERSION,
    )
    statistical_runtime = time.perf_counter() - started
    started = time.perf_counter()
    ml_events = candidate_events(
        isolation_forest.score_rows(partition["feature_rows"]), isolation_forest.threshold or 0,
        "isolation_forest", MODEL_VERSION,
    )
    ml_runtime = time.perf_counter() - started
    hybrid_events = _hybrid(rule_events, ml_events)
    scenarios = _equipment_scenarios(partition)
    return {
        "partition": partition["name"],
        "seeds": partition["seeds"],
        "evaluation_scope": "equipment anomalies; telemetry data-quality scenarios are handled by deterministic monitoring and excluded",
        "rules": {**evaluate_synthetic_events(rule_events, scenarios), "candidate_count": len(rule_events), "runtime_seconds": round(rule_runtime, 4)},
        "statistical": {**evaluate_synthetic_events(statistical_events, scenarios), "candidate_count": len(statistical_events), "runtime_seconds": round(statistical_runtime, 4)},
        "isolation_forest": {**evaluate_synthetic_events(ml_events, scenarios), "candidate_count": len(ml_events), "runtime_seconds": round(ml_runtime, 4)},
        "hybrid": {**evaluate_synthetic_events(hybrid_events, scenarios), "candidate_count": len(hybrid_events)},
    }


def train(output_dir: Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Any]:
    train_data = build_partition("train")
    validation_data = build_partition("validation")
    test_data = build_partition("test")

    baseline = RobustResidualDetector().fit(train_data["normal_feature_rows"])
    baseline.calibrate(validation_data["normal_feature_rows"])
    isolation_forest = IsolationForestDetector(random_state=42).fit(train_data["normal_feature_rows"])
    isolation_forest.calibrate(validation_data["normal_feature_rows"])

    # All parameters and thresholds are now frozen. The final partition is
    # materialized and evaluated only after this point.
    pre_final = {
        "validation": evaluate_partition(validation_data, baseline, isolation_forest),
        "test": evaluate_partition(test_data, baseline, isolation_forest),
    }
    final_data = build_partition("final")
    historical = build_seed_partition("historical_locked", LOCKED_HISTORICAL_SEEDS)
    evaluations = {
        **pre_final,
        "final": evaluate_partition(final_data, baseline, isolation_forest),
        "historical_locked": evaluate_partition(historical, baseline, isolation_forest),
    }
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = {
        "status": "experimental",
        "created_at": created,
        "model": isolation_forest.metadata(),
        "statistical_baseline": baseline.metadata(),
        "rules_version": METHOD_VERSION,
        "training_partition": {"name": "train", "seeds": train_data["seeds"], "normal_rows": len(train_data["normal_feature_rows"])},
        "calibration_partition": {"name": "validation", "seeds": validation_data["seeds"], "normal_rows": len(validation_data["normal_feature_rows"])},
        "parameter_freeze": "before test and final evaluation",
        "operational_alert_writes": False,
        "limitations": [
            "trained and evaluated only on synthetic telemetry",
            "candidate events require operator review and are not confirmed faults",
            "data-quality outage detection remains the responsibility of deterministic monitoring",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = save_artifact(output_dir / "inverter_anomaly.joblib", isolation_forest, baseline, metadata, output_dir)
    artifact_bytes = artifact_path.stat().st_size
    report = {
        "metadata": metadata,
        "dataset_audit": dataset_audit(),
        "evaluation": evaluations,
        "resource_usage": {"artifact_bytes": artifact_bytes, "training_rows": len(train_data["normal_feature_rows"])},
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate the experimental inverter anomaly model")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    args = parser.parse_args()
    report = train(args.output_dir.resolve())
    print(json.dumps({
        "artifact": str(args.output_dir.resolve() / "inverter_anomaly.joblib"),
        "final": report["evaluation"]["final"],
    }, indent=2))


if __name__ == "__main__":
    main()

"""Experimental Isolation Forest scoring and causal candidate-event utilities.

The outputs in this module are review candidates. They never write operational
alerts and must not be presented as confirmed equipment faults.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from .inverter_ml_baselines import RobustResidualDetector
from .inverter_ml_features import FEATURE_NAMES, FEATURE_VERSION
from .inverter_monitoring import parse_iso_datetime


MODEL_VERSION = "isolation-forest-v1"
ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parent / "ml_artifacts"


class IsolationForestDetector:
    def __init__(self, random_state: int = 42, n_estimators: int = 250) -> None:
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.threshold: float | None = None
        self.pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", RobustScaler()),
            ("model", IsolationForest(
                n_estimators=n_estimators,
                contamination="auto",
                max_samples=256,
                random_state=random_state,
                n_jobs=1,
            )),
        ])

    def fit(self, normal_rows: Iterable[dict[str, Any]]) -> "IsolationForestDetector":
        values = [row["features"] for row in normal_rows]
        if not values:
            raise ValueError("At least one normal feature row is required")
        self.pipeline.fit(values)
        return self

    def score_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        materialized = list(rows)
        if not materialized:
            return []
        # sklearn decision_function is larger for inliers; invert it so every
        # Phase 2B detector shares the intuitive higher-is-more-abnormal scale.
        scores = -self.pipeline.decision_function([row["features"] for row in materialized])
        transformed = self.pipeline[:-1].transform([row["features"] for row in materialized])
        centers = np.median(transformed, axis=0)
        deviations = np.abs(transformed - centers)
        result = []
        for row, score, deviation in zip(materialized, scores, deviations):
            ranked = np.argsort(deviation)[::-1][:4]
            result.append({
                **row,
                "anomaly_score": float(score),
                "feature_deviations": {FEATURE_NAMES[index]: float(deviation[index]) for index in ranked},
            })
        return result

    def calibrate(self, validation_normal_rows: Iterable[dict[str, Any]], quantile: float = 0.995) -> float:
        scores = [row["anomaly_score"] for row in self.score_rows(validation_normal_rows)]
        if not scores:
            raise ValueError("Validation-normal rows are required for calibration")
        self.threshold = float(np.quantile(scores, quantile))
        return self.threshold

    def metadata(self) -> dict[str, Any]:
        return {
            "version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "feature_names": FEATURE_NAMES,
            "random_state": self.random_state,
            "n_estimators": self.n_estimators,
            "threshold": self.threshold,
            "score_semantics": "higher_is_more_abnormal",
            "sklearn_version": sklearn.__version__,
        }


def _safe_artifact_path(path: str | Path, artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    root = Path(artifact_root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Artifact path must stay inside the trusted artifact directory")
    return candidate


def save_artifact(
    path: str | Path,
    detector: IsolationForestDetector,
    baseline: RobustResidualDetector,
    metadata: dict[str, Any],
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    target = _safe_artifact_path(path, artifact_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "feature_names": FEATURE_NAMES,
        "model_version": MODEL_VERSION,
        "detector": detector,
        "baseline": baseline,
        "metadata": metadata,
    }, target)
    return target


def load_artifact(path: str | Path, artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Any]:
    """Load only a locally generated artifact from the trusted model directory."""
    target = _safe_artifact_path(path, artifact_root)
    artifact = joblib.load(target)
    if not isinstance(artifact, dict):
        raise ValueError("Invalid model artifact")
    expected = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "feature_names": FEATURE_NAMES,
        "model_version": MODEL_VERSION,
    }
    for key, value in expected.items():
        if artifact.get(key) != value:
            raise ValueError(f"Model artifact {key} is incompatible")
    if not isinstance(artifact.get("detector"), IsolationForestDetector):
        raise ValueError("Model artifact detector type is incompatible")
    if not isinstance(artifact.get("baseline"), RobustResidualDetector):
        raise ValueError("Model artifact baseline type is incompatible")
    return artifact


def _category(row: dict[str, Any]) -> str:
    features = row["feature_map"]
    if features["normalized_ac_power"] < 0.02 and features["normalized_dc_power"] < 0.03:
        return "unexpected_shutdown"
    if features["temperature_load_residual_c"] > 8 or features["inverter_temperature_c"] >= 72:
        return "inverter_overheating"
    if features["conversion_efficiency"] < 0.9 and features["normalized_dc_power"] > 0.12:
        return "ac_conversion_underperformance"
    return "pv_side_underperformance"


def _contiguous_runs(rows: list[dict[str, Any]], threshold: float) -> list[list[dict[str, Any]]]:
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["timestamp"]):
        abnormal = float(row["anomaly_score"]) >= threshold
        contiguous = not current or (
            parse_iso_datetime(row["timestamp"]) - parse_iso_datetime(current[-1]["timestamp"])
            <= timedelta(minutes=24)
        )
        if abnormal and contiguous:
            current.append(row)
        else:
            if current:
                runs.append(current)
                current = []
            if abnormal:
                current = [row]
    if current:
        runs.append(current)
    return runs


def _event(
    run: list[dict[str, Any]], category: str, detector_type: str, model_version: str,
    recovery_timestamp: str | None = None,
) -> dict[str, Any]:
    onset = run[0]["timestamp"]
    eligible_index = min(1, len(run) - 1)
    last = run[-1]["timestamp"]
    top: dict[str, float] = defaultdict(float)
    for row in run:
        for name, value in row.get("feature_deviations", {}).items():
            top[name] = max(top[name], float(value))
    contributing = [
        {"feature": name, "deviation": round(value, 3)}
        for name, value in sorted(top.items(), key=lambda item: item[1], reverse=True)[:4]
    ]
    return {
        "inverter_id": run[0]["inverter_id"],
        "anomaly_category": category,
        "start_timestamp": onset,
        "end_timestamp": last,
        "onset_timestamp": onset,
        "detection_eligible_timestamp": run[eligible_index]["timestamp"],
        "last_abnormal_timestamp": last,
        "recovery_timestamp": recovery_timestamp,
        "active_duration_minutes": len({row["timestamp"] for row in run}) * 15,
        "peak_anomaly_score": round(max(float(row["anomaly_score"]) for row in run), 6),
        "detector_type": detector_type,
        "model_version": model_version,
        "feature_version": FEATURE_VERSION,
        "contributing_feature_deviations": contributing,
        "status": "experimental_review_candidate",
    }


def candidate_events(
    scored_rows: Iterable[dict[str, Any]],
    threshold: float,
    detector_type: str,
    model_version: str,
) -> list[dict[str, Any]]:
    """Convert scores to causal candidates; short recurrence is aggregated."""
    by_inverter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        by_inverter[row["inverter_id"]].append(row)
    events: list[dict[str, Any]] = []
    for rows in by_inverter.values():
        ordered_rows = sorted(rows, key=lambda item: item["timestamp"])
        runs = _contiguous_runs(ordered_rows, threshold)
        recovery_by_end = {
            run[-1]["timestamp"]: next((row["timestamp"] for row in ordered_rows if row["timestamp"] > run[-1]["timestamp"] and float(row["anomaly_score"]) < threshold), None)
            for run in runs
        }
        short_pv = [run for run in runs if len(run) <= 4 and _category(max(run, key=lambda item: item["anomaly_score"])) == "pv_side_underperformance"]
        recurring_ids: set[int] = set()
        index = 0
        while index < len(short_pv) - 1:
            group = [short_pv[index], short_pv[index + 1]]
            if parse_iso_datetime(group[-1][0]["timestamp"]) - parse_iso_datetime(group[0][-1]["timestamp"]) <= timedelta(hours=12):
                merged = [row for run in group for row in run]
                event = _event(merged, "intermittent_derating", detector_type, model_version, recovery_by_end[group[-1][-1]["timestamp"]])
                event["recurrence_count"] = 2
                events.append(event)
                recurring_ids.update(id(run) for run in group)
                index += 2
            else:
                index += 1
        for run in runs:
            if id(run) in recurring_ids or len(run) < 2:
                continue
            representative = max(run, key=lambda item: item["anomaly_score"])
            events.append(_event(run, _category(representative), detector_type, model_version, recovery_by_end[run[-1]["timestamp"]]))
    ordered_events = sorted(events, key=lambda event: (event["inverter_id"], event["anomaly_category"], event["start_timestamp"]))
    deduplicated: list[dict[str, Any]] = []
    for event in ordered_events:
        prior = deduplicated[-1] if deduplicated else None
        if (
            prior and prior["inverter_id"] == event["inverter_id"]
            and prior["anomaly_category"] == event["anomaly_category"]
            and parse_iso_datetime(event["start_timestamp"]) - parse_iso_datetime(prior["last_abnormal_timestamp"]) <= timedelta(minutes=30)
        ):
            prior["end_timestamp"] = event["end_timestamp"]
            prior["last_abnormal_timestamp"] = event["last_abnormal_timestamp"]
            prior["recovery_timestamp"] = event["recovery_timestamp"]
            prior["active_duration_minutes"] += event["active_duration_minutes"]
            prior["peak_anomaly_score"] = max(prior["peak_anomaly_score"], event["peak_anomaly_score"])
        else:
            deduplicated.append(event)
    return sorted(deduplicated, key=lambda event: (event["start_timestamp"], event["inverter_id"]))

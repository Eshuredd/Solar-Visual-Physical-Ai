from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.inverter_ml import IsolationForestDetector, candidate_events, load_artifact, save_artifact
from app.inverter_ml_baselines import RobustResidualDetector
from app.inverter_ml_dataset import LOCKED_HISTORICAL_SEEDS, PARTITION_SEEDS, build_partition
from app.inverter_ml_features import FEATURE_NAMES, build_feature_rows
from app.inverter_evaluation import generate_held_out_cases
from app import main


def make_client() -> TestClient:
    init_db(reset=True)
    return TestClient(main.app)


def _normal_case() -> dict:
    return next(case for case in generate_held_out_cases((991,))["cases"] if case["scenario"]["scenario_type"] == "normal")


def test_feature_vector_schema_is_causal_and_has_no_identity_or_label() -> None:
    case = _normal_case()
    original = build_feature_rows(case["inverter"], case["readings"])
    changed = deepcopy(case["readings"])
    changed[-1]["ac_power_kw"] *= .2
    rebuilt = build_feature_rows(case["inverter"], changed)
    assert len(original[0]["features"]) == len(FEATURE_NAMES)
    assert original[:-1] == rebuilt[:-1]
    assert "inverter_id" not in FEATURE_NAMES
    assert not any("label" in name or "scenario" in name for name in FEATURE_NAMES)


def test_feature_filters_exclude_night_curtailment_and_invalid_rows() -> None:
    case = _normal_case()
    rows = deepcopy(case["readings"])
    daylight = next(row for row in rows if row["irradiance_w_m2"] >= 200)
    curtailed = deepcopy(daylight)
    curtailed["timestamp"] = "2026-09-03T10:00:00Z"
    curtailed["operating_state"] = "Curtailment"
    invalid = deepcopy(daylight)
    invalid["timestamp"] = "2026-09-03T10:15:00Z"
    invalid["ac_power_kw"] = -1
    features = build_feature_rows(case["inverter"], rows + [curtailed, invalid])
    timestamps = {row["timestamp"] for row in features}
    assert curtailed["timestamp"] not in timestamps
    assert invalid["timestamp"] not in timestamps
    assert all(row["timestamp"] in timestamps for row in rows if row["irradiance_w_m2"] >= 200)


def test_seed_partitions_are_disjoint_and_historical_seeds_stay_locked() -> None:
    sets = [set(seeds) for seeds in PARTITION_SEEDS.values()]
    assert all(left.isdisjoint(right) for index, left in enumerate(sets) for right in sets[index + 1:])
    assert set(LOCKED_HISTORICAL_SEEDS).isdisjoint(set().union(*sets))
    assert LOCKED_HISTORICAL_SEEDS == (731, 1291, 2027)


def test_isolation_forest_is_reproducible_and_round_trips(tmp_path: Path) -> None:
    train = build_partition("train")["normal_feature_rows"]
    validation = build_partition("validation")["normal_feature_rows"]
    first = IsolationForestDetector(random_state=42, n_estimators=30).fit(train)
    second = IsolationForestDetector(random_state=42, n_estimators=30).fit(train)
    first.calibrate(validation)
    second.calibrate(validation)
    assert first.threshold == pytest.approx(second.threshold)
    assert [row["anomaly_score"] for row in first.score_rows(validation[:20])] == pytest.approx(
        [row["anomaly_score"] for row in second.score_rows(validation[:20])]
    )
    baseline = RobustResidualDetector().fit(train)
    baseline.calibrate(validation)
    saved = save_artifact(tmp_path / "model.joblib", first, baseline, {"test": True}, tmp_path)
    loaded = load_artifact(saved, tmp_path)
    assert loaded["metadata"] == {"test": True}
    with pytest.raises(ValueError):
        load_artifact(tmp_path.parent / "outside.joblib", tmp_path)


def test_candidate_event_timestamps_are_causal() -> None:
    feature_map = {name: 0.0 for name in FEATURE_NAMES}
    feature_map.update({"normalized_ac_power": .4, "normalized_dc_power": .5, "conversion_efficiency": .97})
    rows = [
        {"inverter_id": "inv", "timestamp": timestamp, "feature_map": feature_map, "features": [0.0] * len(FEATURE_NAMES), "anomaly_score": score, "feature_deviations": {"expected_ac_residual": score}}
        for timestamp, score in [("2026-01-01T10:00:00Z", 2.0), ("2026-01-01T10:15:00Z", 3.0), ("2026-01-01T10:30:00Z", 0.0)]
    ]
    event = candidate_events(rows, 1.0, "test", "test-v1")[0]
    assert event["onset_timestamp"] == "2026-01-01T10:00:00Z"
    assert event["detection_eligible_timestamp"] == "2026-01-01T10:15:00Z"
    assert event["last_abnormal_timestamp"] == "2026-01-01T10:15:00Z"


def test_ml_api_is_read_only_and_has_disabled_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with make_client() as client:
        before = client.get("/api/inverters/site-001-INV-03/alerts").json()
        scores = client.get("/api/inverters/site-001-INV-03/ml-scores")
        comparison = client.get("/api/inverters/site-001-INV-03/ml-comparison")
        after = client.get("/api/inverters/site-001-INV-03/alerts").json()
        assert scores.status_code == comparison.status_code == 200
        assert scores.json()["status"] == "experimental_review_only"
        assert comparison.json()["operational_alert_writes"] is False
        assert before == after

        monkeypatch.setattr(main, "ML_ARTIFACT_PATH", tmp_path / "missing.joblib")
        disabled = client.get("/api/ml/model").json()
        assert disabled["enabled"] is False
        assert client.get("/api/inverters/site-001-INV-03/ml-scores").json()["scores"] == []

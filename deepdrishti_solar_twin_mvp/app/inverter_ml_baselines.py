"""Robust, interpretable statistical baseline for inverter anomaly scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np


BASELINE_VERSION = "robust-residual-v1"
BASELINE_FEATURES = (
    "expected_ac_residual",
    "expected_dc_residual",
    "temperature_load_residual_c",
    "conversion_efficiency",
    "dc_power_balance_error",
    "rolling_ac_variability",
)


@dataclass
class RobustResidualDetector:
    """Median/MAD detector fitted only on known-normal feature rows."""

    feature_names: tuple[str, ...] = BASELINE_FEATURES
    medians: dict[str, float] = field(default_factory=dict)
    scales: dict[str, float] = field(default_factory=dict)
    threshold: float | None = None
    version: str = BASELINE_VERSION

    def fit(self, rows: Iterable[dict[str, Any]]) -> "RobustResidualDetector":
        materialized = list(rows)
        if not materialized:
            raise ValueError("At least one normal feature row is required")
        for name in self.feature_names:
            values = np.asarray([float(row["feature_map"][name]) for row in materialized])
            median = float(np.median(values))
            mad = float(np.median(np.abs(values - median)))
            # 1.4826 converts MAD to a normal-distribution scale estimate. The
            # floor prevents essentially constant synthetic features exploding.
            self.medians[name] = median
            self.scales[name] = max(1.4826 * mad, 0.01)
        return self

    def score_row(self, row: dict[str, Any]) -> tuple[float, dict[str, float]]:
        if not self.medians:
            raise RuntimeError("Detector has not been fitted")
        deviations = {
            name: abs(float(row["feature_map"][name]) - self.medians[name])
            / self.scales[name]
            for name in self.feature_names
        }
        return max(deviations.values()), deviations

    def score_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []
        for row in rows:
            score, deviations = self.score_row(row)
            scored.append({**row, "anomaly_score": score, "feature_deviations": deviations})
        return scored

    def calibrate(self, normal_rows: Iterable[dict[str, Any]], quantile: float = 0.995) -> float:
        if not 0.5 < quantile < 1:
            raise ValueError("Calibration quantile must be between 0.5 and 1")
        scores = [self.score_row(row)[0] for row in normal_rows]
        if not scores:
            raise ValueError("Validation-normal rows are required for calibration")
        self.threshold = float(np.quantile(scores, quantile))
        return self.threshold

    def metadata(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "feature_names": list(self.feature_names),
            "medians": self.medians,
            "scales": self.scales,
            "threshold": self.threshold,
            "score_semantics": "higher_is_more_abnormal",
        }

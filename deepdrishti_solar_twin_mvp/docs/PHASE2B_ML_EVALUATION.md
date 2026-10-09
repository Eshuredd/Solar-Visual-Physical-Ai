# Phase 2B experimental ML evaluation

## Decision summary

The Isolation Forest is implemented as a reproducible experimental review layer, not as a replacement for the deterministic alert engine. On the untouched final synthetic partition, the rules achieved 1.000 precision and 0.800 recall, while Isolation Forest achieved 0.500 precision and 0.600 recall. The hybrid increased false positives without improving final recall. Operational alerts therefore remain rule-driven.

All figures below describe synthetic scenarios. They are not estimates of real-world fault-detection accuracy.

## Dataset and leakage audit

The generator is `heldout-solar-v1`; the Phase 2B dataset manifest is `inverter-ml-synthetic-v2`. The original application contains 12 synthetic inverter records with seven days of 15-minute telemetry. Each generated evaluation seed contributes eight two-day inverter cases: five equipment-anomaly cases, one telemetry-outage case, one normal case and one curtailment case.

Available channels are AC/DC power, voltage and current; inverter and ambient temperature; irradiance; operating state; timestamp and source provenance. There are no measured plane-of-array irradiance calibration records, string-level channels, alarms from real controllers, maintenance outcomes or verified physical-fault labels.

| Partition | Seeds | Cases | Scored operating rows | Normal rows available for fit/calibration |
|---|---:|---:|---:|---:|
| Train | 101, 103, 107, 109, 113 | 40 | 3,177 | 2,554 |
| Validation | 211, 223 | 16 | 1,261 | 1,008 |
| Test | 307, 311 | 16 | 1,289 | 1,048 |
| Final untouched | 401, 409 | 16 | 1,228 | 997 |
| Historical locked | 731, 1291, 2027 | 24 | evaluation only | evaluation only |

Controls:

- Generation seeds are disjoint between train, validation, test, final and historical locked evaluation.
- Calendar periods do not overlap. A Phase 2B-only transform spans winter, spring, summer and autumn profiles by varying irradiance, ambient temperature and inverter temperature while preserving injected fault ratios. It does not alter the locked generator.
- Training uses only normal operating rows. A one-hour guard band around known positive scenario windows is removed.
- Labels, scenario names and inverter identifiers are absent from feature vectors.
- Rolling features use the current and preceding samples only.
- Night, low irradiance, startup, shutdown, protection, curtailment, grid restriction and invalid telemetry are excluded before scoring.
- Validation-normal scores set both detector thresholds at the frozen 99.5th percentile. Test, final and historical locked partitions do not change parameters.
- Data-quality outage scenarios are excluded from the equipment-anomaly comparison because rows that do not exist cannot be scored; the deterministic monitoring layer continues to own outage detection.

The data are strongly row-imbalanced toward healthy operation. Scenario counts are deliberately balanced for evaluation, which is unlike field prevalence. Repeated analytic waveforms, fixed 15-minute cadence, clean operating-state flags, shared simulator equations and abrupt injected changes are simulator artifacts a model can exploit. Identity leakage is limited by omitting IDs and separating seeds, but the same simulator family remains present in every partition. This is therefore a synthetic proof of concept, not enough data for a production model.

## Feature and model provenance

`inverter-features-v1` contains 17 identity-free features covering normalized power, conversion efficiency, expected-power residuals, temperature/load residuals, recent and rolling behavior, persistence and capacity-normalized peer difference.

| Feature | Definition |
|---|---|
| `normalized_ac_power`, `normalized_dc_power` | AC or DC kW divided by rated AC kW |
| `conversion_efficiency` | AC kW divided by DC kW |
| `expected_ac_residual`, `expected_dc_residual` | Capacity-normalized difference from the irradiance/temperature physics baseline |
| `production_ratio` | Actual AC divided by expected AC |
| `dc_power_balance_error` | Difference between reported DC power and voltage × current, normalized by capacity |
| `inverter_temperature_c`, `ambient_temperature_c` | Current measured temperatures |
| `temperature_rise_c` | Inverter minus ambient temperature |
| `temperature_load_residual_c` | Temperature rise minus a simple load-conditioned thermal baseline |
| `recent_ac_change` | Capacity-normalized change from the preceding scored observation |
| `rolling_ac_variability`, `rolling_efficiency_variation` | Causal four-sample population standard deviations |
| `rolling_expected_residual` | Causal four-sample mean AC residual |
| `negative_residual_persistence` | Count of consecutive residuals below -0.15 |
| `peer_relative_difference` | Capacity-normalized AC minus the same-timestamp peer median |

The interpretable baseline, `robust-residual-v1`, fits per-feature medians and MAD scales on train-normal rows. The novelty model, `isolation-forest-v1`, uses median imputation, robust scaling and scikit-learn Isolation Forest with 250 trees, `random_state=42`, one worker and a maximum of 256 samples per tree. Model scores are inverted so larger values always mean “more unusual”; they are not failure probabilities. The frozen ML review threshold is `0.0698744716`.

## Event-level comparison

Precision, recall and F1 use the existing causal one-to-one temporal matcher. Each row is `precision / recall / F1`.

| Partition | Rules | Robust statistical | Isolation Forest | Hybrid rules + ML candidates |
|---|---:|---:|---:|---:|
| Validation | 1.000 / 0.800 / 0.889 | 0.467 / 0.700 / 0.560 | 0.429 / 0.600 / 0.500 | 0.500 / 0.800 / 0.615 |
| Test | 1.000 / 0.800 / 0.889 | 0.429 / 0.600 / 0.500 | 0.400 / 0.600 / 0.480 | 0.471 / 0.800 / 0.593 |
| Final untouched | 1.000 / 0.800 / 0.889 | 0.385 / 0.500 / 0.435 | 0.500 / 0.600 / 0.545 | 0.571 / 0.800 / 0.666 |
| Historical locked | 1.000 / 0.800 / 0.889 | 0.450 / 0.600 / 0.514 | 0.474 / 0.600 / 0.530 | 0.545 / 0.800 / 0.648 |

No ML or hybrid recall gain reproduced on the untouched final partition. Isolation Forest produced six final false-positive candidates (all categorized as overheating), missed both intermittent-derating and both PV-side cases, and detected both AC-conversion, overheating and shutdown cases. The statistical detector produced eight overheating false positives and had the same intermittent/PV misses. On the historical locked partition, Isolation Forest missed all three intermittent cases and added one false intermittent candidate. All four methods passed the normal-operation scenario check, but that small synthetic check does not establish a field false-alarm rate. These results are evidence against promoting the ML or hybrid detector. They are retained rather than hidden or tuned away.

## Runtime and artifact

The generated joblib artifact is 3,445,968 bytes and was fitted from 2,554 train-normal rows. On the final partition, the observed detector runtimes were approximately 0.018 seconds for rules, 0.001 seconds for the robust baseline and 0.016 seconds for Isolation Forest on the development machine. These measurements are directional, not production capacity guarantees.

## Candidate semantics and operational boundary

Score runs become causal review candidates only after two consecutive above-threshold samples. Candidate records retain onset, detection-eligible time, last abnormal time, model and feature versions, peak score and the largest feature deviations. Recurring short PV-like runs can be grouped as intermittent candidates. Candidates are stored only in the response; they never insert or update `inverter_alerts`.

The API and inverter drawer label all ML output **experimental** and **not an operational alert**. Missing or incompatible artifacts produce an explicit ML-disabled response while deterministic monitoring keeps working. Model loading is restricted to the trusted local artifact directory and validates schema, model and feature versions; joblib artifacts must still be treated as trusted local build products, never as user uploads.

## Reproduction

```bash
python3 -m app.train_inverter_ml
pytest -q
```

The explicit training command creates `app/ml_artifacts/inverter_anomaly.joblib`, `metadata.json` and `evaluation.json`. Starting the API does not train or modify a model.

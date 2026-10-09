# Phase 2A.1 synthetic evaluation

This is a detector regression and robustness check using synthetic telemetry. It
is not customer-ready accuracy evidence or field fault-detection performance.

## Causal event timestamps

Every event distinguishes anomaly onset, first detection eligibility after
persistence, last abnormal observation, observed recovery, alert creation and
first acknowledgement. Detection latency is:

```text
detection_eligible_timestamp - ground_truth_onset
```

This reproduces what chronological streaming could know and never uses future
observations to assign an earlier detection. Active duration is the number of
contributing abnormal samples times the 15-minute cadence. Wall-clock duration
spans onset to recovery (or the last abnormal interval); excluded duration
reports night and other non-active gaps. Overnight PV episodes are bridged only
when no valid recovery sample occurred between them.

## One-to-one matching

Prediction and truth must agree on inverter and category, then overlap or fall
within a 30-minute boundary tolerance. Candidate pairs are ranked by temporal
overlap and assigned one-to-one. Extra compatible predictions are duplicates;
unmatched predictions are false positives and unmatched truths are false
negatives. Metrics return `null` when their denominator is zero.

## Regression fixture

| Metric | Result |
|---|---:|
| TP / FP / FN events | 10 / 0 / 0 |
| Precision / recall / F1 | 1.000 / 1.000 / 1.000 |
| False alerts per inverter-day | 0.0000 |
| Mean causal detection latency | 255.0 minutes |
| Mean onset / offset error | 21.0 / -37.5 minutes |
| Duplicate incidents | 0 |
| Normal operation | Passed |

The former 19.5-minute latency incorrectly used the first abnormal sample. The
corrected result waits for persistence and, for intermittent behavior, recurrence.

## Independent held-out set

`heldout-solar-v1` produces 24 cases using stored seeds `731`, `1291`, and `2027`.
It independently varies clouds, irradiance, ambient temperature, sensor noise,
normal unit response, fault severity/duration, missing data, partial recovery,
intermittent behavior and grid curtailment. Labels never enter feature rows.

| Metric | Result |
|---|---:|
| TP / FP / FN events | 15 / 0 / 3 |
| Precision / recall / F1 | 1.000 / 0.833 / 0.909 |
| False alerts per inverter-day | 0.0000 |
| Mean causal detection latency | 65.0 minutes |
| Mean onset / offset error | 0.0 / 3.0 minutes |
| Duplicate incidents | 0 |
| Normal operation | Passed |

The three misses are intermittent-derating cases whose timing does not satisfy
the current recurrence rule. This limitation is retained rather than tuning on
the hold-out. Intermittent precision is `null` because no prediction was emitted;
recall is `0.0`.

## Reproduction

```bash
pytest -q
curl http://127.0.0.1:8000/api/inverter-alerts/evaluation
```

The API returns separate `regression_fixture` and `held_out` results plus the
generator version and seeds.

## Limitations

- Both datasets are synthetic and use simplified solar behavior.
- Coverage is small relative to real fleets, weather, firmware and grid regimes.
- Field ground-truth boundaries are rarely known this precisely.
- No string/MPPT, protection-log or maintenance-confirmation data is available.
- Rules remain unvalidated on independent real sites and seasons.
- The intermittent recall gap must be addressed using new development data, not
  by tuning against this final hold-out.

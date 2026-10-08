# Phase 2A synthetic evaluation

This evaluation measures the deterministic rule engine against scenario labels
stored separately from the telemetry features. It is a software regression and
explainability check—not evidence of real-world fault-detection accuracy.

## Scenarios

- Normal operation
- PV-side production underperformance and later recovery
- AC conversion underperformance with DC input remaining available
- Load-adjusted elevated inverter temperature
- Temporary unexplained daytime shutdown
- Recurring intermittent derating
- Missing timestamps, a physically invalid measurement and stale telemetry
- Declared grid curtailment as a negative/exclusion case

Injected magnitudes and durations differ from their rule thresholds. Scenario
labels are retained in `inverter_scenarios`; the detector reads only inverter
metadata and telemetry.

## Reproducible result

Run:

```bash
curl http://127.0.0.1:8000/api/inverter-alerts/evaluation
```

Current deterministic fixture result:

| Metric | Result |
|---|---:|
| Event-level precision | 1.000 |
| Event-level recall | 1.000 |
| False-positive rate across normal/curtailment scenarios | 0.000 |
| Mean detection latency | 19.5 minutes |
| Duplicate alert count | 0 |
| Normal-operation scenario | Passed |

Every configured category currently has precision and recall of 1.000 on these
fixtures. These perfect synthetic numbers are expected for bounded regression
scenarios and must not be presented as field performance. Real evaluation needs
labeled SCADA histories from multiple sites, sensor-quality review, independent
train/calibration/test periods, operating-regime stratification and root-cause
confirmation by qualified engineers.

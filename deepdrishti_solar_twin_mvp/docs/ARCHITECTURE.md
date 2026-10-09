# DeepDrishti Solar Twin — Architecture

## Product loop

```text
Capture → Ingest → Reconstruct → Detect → Localize → Review
→ Quantify → Prioritize → Assign → Repair → Verify → Retain history
```

## Runnable MVP architecture

```text
Browser
  ├─ Portfolio/site dashboards
  ├─ SVG Digital Twin
  ├─ Map/list/table synchronization
  ├─ Evidence drawer
  ├─ Task board
  └─ Field-mode simulation
          │ REST/JSON
          ▼
FastAPI
  ├─ Portfolio aggregation
  ├─ Site and asset APIs
  ├─ Anomaly state machine
  ├─ Work-order workflow
  ├─ Image-analysis endpoint
  ├─ Inverter monitoring and rule-based indicators
  ├─ Event-based inverter anomaly detection and lifecycle APIs
  └─ CSV export
          │
          ├─ SQLite: sites, inspections, anomalies, tasks, inverters, telemetry, inverter alerts, topology and reviewed evidence links
          └─ Local static storage: RGB/thermal evidence and uploads
```

## Core entities

### Site

- Permanent site ID
- Owner, region, capacity and coordinates
- Performance context
- Inspection history
- Asset hierarchy

### Inspection

- Site ID
- Capture time
- Inspection type and source
- Weather/capture metadata
- Model version
- Findings and modeled impact

### Anomaly

- Permanent anomaly ID
- Inspection ID
- Physical asset ID
- Point/map coordinates
- Type and category
- Priority and status
- Confidence
- Thermal measurements
- Energy and revenue impact
- RGB/thermal evidence
- Recommendation
- Audit history

### Task

- Linked anomaly and site
- Owner and due date
- Requested action
- Priority and workflow status
- Notes and timestamps

### Inverter and telemetry

- Site-scoped permanent inverter identity, capacity, make/model, block and operational status
- Timestamp-unique electrical, temperature, irradiance and operating-state telemetry
- Synthetic data provenance on both equipment and readings
- Indexed time ranges for monitoring APIs and future anomaly consumers

The Phase 1 monitoring service calculates DC/AC power, conversion efficiency,
energy, expected power and relative yield. Its expected baseline is normalized by
rated AC capacity, irradiance and temperature. Night, low-light, startup,
shutdown, curtailment and missing-data samples are excluded from performance
classification. Electrical indicators remain separate from image-derived anomaly
records. Phase 3A can correlate them through explicit, provenance-bearing
inverter → MPPT → string → physical-group relationships while preserving the
difference between correlated evidence and a verified physical cause.

### Electrical-to-physical topology

- Site-scoped blocks, variable MPPT inputs and variable PV strings
- Contiguous physical module groups connected through explicit join records
- Foreign keys, uniqueness constraints, indexes and cross-site guard triggers
- Mapping classification (`verified_as_built`, `synthetic_demo`, `unverified`)
- Explicit complete, partial or unknown coverage
- Human-reviewed alert/finding associations with append-only transition history

See [PHASE3A_TOPOLOGY.md](PHASE3A_TOPOLOGY.md) for demo assumptions and real-deployment verification requirements.

### Inverter alert

- Separate from image-inspection anomalies
- Stable event identity from inverter, category, start time and detector version
- Start/end timestamps and contributing telemetry references
- Expected and observed measurement evidence
- Severity and transparent detection deviation—not a fake confidence score
- Operator-controlled Open → Acknowledged → Investigating → Resolved lifecycle
- Append-only lifecycle history and synthetic/real provenance
- Separate onset, detection-eligibility, last-abnormal, recovery, creation and acknowledgement timestamps
- Active anomalous duration separated from wall-clock and excluded non-operating duration

Phase 2A rules compare AC against a DC-input conversion model, DC production
against an independent irradiance/temperature baseline, and temperature against
ambient plus operating load. Persistence windows turn individual samples into
events. Known non-fault states are excluded, and missing, stale or invalid data
is emitted only as `data_quality`. A recovered signal updates the event end but
does not automatically claim that a physical repair occurred.

Phase 2A.1 deduplicates overlapping analysis windows using category, inverter and
temporal overlap while preserving operator history. Overnight gaps are bridged
only without valid recovery evidence. A configured inverter with prior telemetry
can produce a complete-outage data-quality event; a newly registered device with
no history cannot. Evaluation uses causal eligibility timestamps and exclusive
one-to-one event matching.

## Production target

```text
Web/PWA: Next.js or React + MapLibre + deck.gl
API: FastAPI/NestJS
Geospatial DB: PostgreSQL + PostGIS
Time series: TimescaleDB
Object storage: S3/MinIO
Raster: COG + tile service
Async jobs: Celery/RQ → Temporal
AI: PyTorch/OpenCV task-specific model packages
MLOps: MLflow + DVC + data/model lineage
Review: CVAT/Label Studio or built-in QA console
Auth: SSO/OIDC + RBAC + tenant isolation
Observability: OpenTelemetry + logs/metrics/traces
```

## Model-package strategy

Avoid one monolithic detector. Use independent packages with a shared output contract:

- Module/row segmentation
- Radiometric thermal anomaly segmentation/classification
- Tracker geometry analysis
- Vegetation segmentation
- Soiling analysis
- Wiring/component detection
- Crack candidate generation and verification
- Erosion/change detection

Shared output:

```json
{
  "inspection_id": "INS-001",
  "asset_id": "SITE-B7-R21-S04-M18",
  "type": "cell_hotspot",
  "confidence": 0.93,
  "geometry": {"type": "Point", "coordinates": [0, 0]},
  "priority": "high",
  "evidence_ids": ["IMG-RGB-1", "IMG-IR-1"],
  "measurements": {"delta_t_c": 21.7},
  "model": {"name": "thermal-hotspot", "version": "1.3.2"}
}
```

## Asset association

Production asset association is a first-class service:

1. Read camera GPS, orientation and flight telemetry.
2. Produce an orthomosaic or image footprints.
3. Import/reconstruct module polygons.
4. Project detections into site coordinates.
5. Intersect with equipment geometry.
6. Resolve row/string/module order.
7. Expose a human correction workflow.
8. Retain association confidence and method.

## Human-in-the-loop review

```text
Model candidate → reviewer accepts/rejects/edits
→ operational anomaly → task → repair → reinspection verification
```

Required reviewer actions:

- Accept/reject
- Change class
- Reassign asset
- Edit polygon/point
- Adjust severity
- Add notes
- Mark uncertain
- Track model/version and reviewer identity

## Impact engine

```text
affected_dc_kw = affected_modules × module_nameplate_kw × anomaly_factor
annual_kwh_loss = affected_dc_kw × equivalent_sun_hours × 365 × availability
annual_revenue_loss = annual_kwh_loss × tariff_or_PPA_rate
```

Production should store the factors used for every calculation and support customer-specific calibration.

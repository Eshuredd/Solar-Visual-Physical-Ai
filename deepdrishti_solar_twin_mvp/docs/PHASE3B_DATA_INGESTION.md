# Phase 3B — SCADA ingestion and topology validation

## Audit findings and hardening

Phase 3B began from commit `aae9ad9`. Phase 3A already had a site-scoped inverter → MPPT → string → physical-group graph and audited alert/finding associations. The audit found four deployment blockers: physical correlation used a fixed 32-module ordinal; completeness was stored rather than derived and did not test overlap; reset recreated the database; and monitoring had no dataset boundary or import provenance.

Topology now declares every physical row in `block_rows`, including its own `module_position_count`. Physical groups carry a row plus an inclusive module range. SQLite triggers reject missing rows, cross-site updates, out-of-row ranges and overlaps. Coverage is derived from declared and mapped positions, unmapped strings, overlaps and inconsistencies.

## Topology data model

The persisted path is:

`sites → inverters → inverter_mppts → pv_strings → string_asset_groups → physical_asset_groups → block_rows → site_blocks`

Every topology table is site-scoped. IDs and parent keys are constrained, with indexes for site/inverter, MPPT/string and site/block/range access. Relationships carry classification, review status, provenance, mapping version, effective date and import ID where applicable.

Verification uses the weakest link across the whole inverter-to-physical chain. Spatial proximity is never treated as electrical proof. Imported maps remain `imported_unverified` until engineering review establishes every link. Partial coverage is reported whenever declared positions or strings remain unmapped.

The fictional demo has 12 inverters, heterogeneous MPPT/string counts, 18 rows per block and repeating 32/34/36-position row layouts. Its mappings are deterministic and idempotent, but explicitly synthetic—not customer as-built documentation.

## Canonical SCADA schema

Accepted records require `site_id`, `inverter_id`, `timestamp_utc` and `ac_power_kw`. Optional measurements remain SQL `NULL`; they are never replaced with zero:

- DC power (kW); AC/DC voltage (V); AC/DC current (A)
- inverter and ambient temperature (°C); irradiance (W/m²)
- operating state, grid state and source record ID
- original timestamp/timezone, source, provenance, quality, import and dataset IDs

Power in W, kW or MW is normalized to kW. Naive timestamps require an explicit IANA timezone; original and UTC values are retained. Validation covers missing fields, malformed/non-finite values, plausible ranges, AC materially exceeding DC, unknown or cross-site inverters, repeated timestamps and conflicting duplicates.

## Preview and transaction model

1. Preview validates extension, MIME type, UTF-8, 2 MB/20,000-row limits, column mappings, units, timestamps and ownership. Accepted rows enter staging; invalid rows enter quarantine with all reasons. Exact duplicates are counted.
2. Commit rechecks conflicts and creates one selectable dataset plus every accepted row in one transaction. Any failure rolls everything back. Repeating a committed file hash or import ID is idempotent.

Reports retain time coverage, median cadence, missing intervals, irregular timestamps, partial-record count, freshness and unit warnings. Rejections are downloadable. Import history remains queryable.

Topology CSV uses ordered `record_type` values: `block`, `row`, `mppt`, `string`, `asset_group`, and `string_asset_group`. Preview validates parent order, registered site inverters, identifiers, variable row layouts, ranges and overlaps. An import with rejected topology rows cannot be committed.

## Dataset and detector boundaries

`telemetry_datasets` separates `synthetic_demo`, `imported_scada` and `synthetic_ml_evaluation`. Monitoring accepts one `dataset_id` and never unions datasets. Responses expose identity, source, classification, provenance and quality.

Rules remain the operational detector. Imported results and alerts are marked `uncalibrated_for_imported_site`; they are investigation signals, not maintenance diagnoses. Isolation Forest remains experimental and is not run across imported datasets by the onboarding UI. Frozen synthetic evaluation data is unchanged.

## API summary

- `POST /api/sites/{site_id}/data-imports/scada/preview`
- `POST /api/sites/{site_id}/data-imports/topology/preview`
- `POST /api/sites/{site_id}/data-imports/{import_id}/commit?demo_mode=true`
- `GET /api/sites/{site_id}/data-imports`
- `GET /api/sites/{site_id}/data-imports/{import_id}`
- `GET /api/sites/{site_id}/data-imports/{import_id}/rejections.csv`
- `GET /api/sites/{site_id}/telemetry-datasets`
- inverter telemetry, summary and analysis endpoints accept `dataset_id`

## Security boundary

This local MVP has no production identity. Mutating import endpoints fail closed unless `demo_mode=true` and `X-DeepDrishti-Demo-Operator: acknowledged` are both present. This is visibly labeled as a demo guard and is **not authentication**. Deployment requires tenant identity, role authorization, CSRF controls where applicable, malware/content scanning, rate limits, managed storage, encryption and audit export.

Reset requires explicit demo mode and refuses to run while a committed import exists, so it cannot delete imported customer-like data.

## Real deployment inputs still required

- authoritative inverter registry and stable customer asset IDs;
- engineering as-built single-line diagrams and string schedules;
- module/row layouts, revisions, dates and reviewer approvals;
- vendor register definitions, units, status codes and clock policy;
- expected cadence, outage windows, curtailment/grid signals and retention policy;
- site-specific rule calibration with labeled history;
- authenticated tenant/role model and approved data controls.

All bundled CSVs and measurements are fictional and reproducible.

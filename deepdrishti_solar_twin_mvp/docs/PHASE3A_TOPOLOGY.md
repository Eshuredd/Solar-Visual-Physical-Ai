# Phase 3A inverter-to-physical-asset topology

## Scope and safety boundary

Phase 3A connects operational rule-based inverter alerts to the Digital Twin hierarchy and inspection evidence. It does not turn spatial or temporal correlation into a diagnosis. Visual findings are never modified automatically, maintenance remains operator-controlled, and the Phase 2B Isolation Forest remains an experimental display layer that is not used by evidence correlation.

## Persistent relationship model

```text
sites
  └─ site_blocks
      └─ physical_asset_groups
          └─ string_asset_groups
              └─ pv_strings
                  └─ inverter_mppts
                      └─ inverters

inverter_alerts ── alert_finding_associations ── anomalies
                         └─ alert_finding_association_events
```

- `site_blocks` records block identity, expected rendered rows, coverage state, classification and provenance.
- `inverter_mppts` supports a variable number of inputs per inverter.
- `pv_strings` supports a variable number of strings per MPPT.
- `physical_asset_groups` records contiguous block-local module-position ranges rather than inventing serial numbers for every module.
- `string_asset_groups` is the explicit electrical-to-physical relationship.
- `alert_finding_associations` stores the current human review state.
- `alert_finding_association_events` is append-only audit history for Proposed, Accepted, Rejected and Removed states.

Every topology table is site-scoped. Foreign keys provide lifecycle integrity, uniqueness constraints prevent duplicate input/string/range mappings, indexes support inverter, block and review queries, and database triggers reject cross-site parent-child relationships. The API separately rejects cross-site alert/finding associations.

## Demo topology assumptions

The Sunridge demonstration topology is deterministic and idempotent:

- 4 rendered blocks, 18 rows per block and 32 rendered module positions per row.
- 12 inverters, three per block.
- Inverters have 6, 7 or 8 MPPT inputs rather than a uniform count.
- Inverters have 12, 14 or 16 PV strings rather than a uniform count.
- Each inverter covers 192 rendered module positions; the demo maps 2,304 positions total.
- String ranges partition each inverter's rendered positions without overlap.
- All topology and relationship records are classified `synthetic_demo` with provenance `phase3a_deterministic_demo_topology_v1`.

These counts describe the simulated SVG layout only. They are not customer equipment counts, module serial records or engineering as-built documents. Other sites deliberately return `null` for unavailable hierarchy counts and include explicit `unavailable` coverage metadata.

## Evidence correlation

For a stored operational inverter alert, the read-only correlation service:

1. Resolves the alert's registered inverter.
2. Traverses MPPTs, strings and explicit string-to-asset-group mappings.
3. Finds inspection anomalies whose block-local row/module ordinal is inside a connected group.
4. Reports the inspection-to-alert time difference as same-day, within seven days, within 30 days or distant.
5. Returns the electrical measurements and telemetry references already supporting the rule alert.
6. Lists missing evidence required for causal confirmation.

The response labels each demo relationship `simulated_not_verified`. It never infers a connection solely from map distance and never edits an anomaly.

## Human review workflow

An operator can propose an alert/finding association with their name and explanation. A reviewer can accept, reject or remove it. Each transition appends an immutable audit event with reviewer, explanation, timestamp and `operator_review` provenance. Removal changes state rather than deleting history. A rejected or removed pair may later be proposed again, preserving its earlier events.

Acceptance means “reviewed as related evidence”; it does not mean verified root cause and does not create a task or change either source record's lifecycle.

## APIs

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/sites/{site_id}/assets` | Database-derived counts and coverage metadata |
| GET | `/api/inverters/{inverter_id}/topology` | Connected blocks, MPPTs, strings and physical groups |
| GET | `/api/inverter-alerts/{alert_id}/evidence` | Electrical evidence, related inspection findings, gaps and reviewed links |
| POST | `/api/inverter-alerts/{alert_id}/associations` | Propose a human-reviewed alert/finding association |
| PATCH | `/api/inverter-alert-associations/{association_id}` | Accept, reject or remove an association |

## Required data for real deployment

- Approved single-line diagrams and inverter/combiner schedules.
- Inverter, MPPT and string manufacturer identifiers and stable customer IDs.
- Verified string-to-module serial-number or polygon assignments.
- CAD/GIS/as-built block, table, row and module geometry.
- Change-controlled commissioning and repowering records.
- MPPT/string current, voltage and alarm telemetry with synchronized clocks.
- Inspection image footprints, calibrated positioning and radiometric metadata.
- Field verification, IV-curve tests and maintenance outcomes.
- Mapping review ownership, effective dates and approval provenance.

Until those inputs are imported and verified, the demo topology must remain classified as simulated.

## Verification

```bash
pytest -q
node --check app/static/app.js
python3 -m py_compile app/*.py
```

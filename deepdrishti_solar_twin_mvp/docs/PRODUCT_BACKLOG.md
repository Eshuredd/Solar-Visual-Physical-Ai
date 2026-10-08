# Product Backlog and Commercialization Roadmap

## Phase 1 — Customer-demo vertical slice

Already represented by this MVP:

- Portfolio and site dashboards
- Digital Twin map/list/table
- Thermal/RGB evidence
- Anomaly status and history
- Business-impact estimates
- Task creation and remediation
- Field-mode workflow
- Sample image analysis

## Phase 2 — First pilot

- Import one real site boundary and CAD/as-built layout
- Generate module/row geometry from drone data
- Ingest radiometric thermal + aligned RGB imagery
- Build reviewer console and QA metrics
- Validate hotspot/string/diode taxonomy with solar-domain experts
- Import module nameplate and inverter mapping
- Customer-configurable PPA/tariff and sun-hour assumptions
- PDF/CSV/GeoJSON inspection package
- Roles: customer admin, reviewer, O&M coordinator, technician
- Pilot dashboard with one real inspection and one verified remediation

## Phase 3 — Repeatable product

- PostgreSQL/PostGIS and multi-tenant architecture
- S3/MinIO media storage and COG raster pipeline
- GPU inference workers, queues and job monitoring
- Model registry and data lineage
- SCADA/DAS connectors
- Compare inspection dates and deterioration trends
- Offline PWA/native field app
- Notifications, SLA and overdue escalation
- Vendor task assignment and evidence approval
- Customer API and webhooks

## Phase 4 — Differentiation

- Solar Operations Copilot
- Ask: “What should I fix first?”
- Explain visual + SCADA root-cause evidence
- Autonomous targeted reinspection
- Drone-dock and robot integrations
- Warranty/insurance evidence packages
- Portfolio benchmarking
- Forecasted failure and deterioration risk
- Dynamic maintenance optimization based on energy price and crew routing

## Suggested first commercial package

> Customer provides drone imagery, site layout and equipment metadata. DeepDrishti returns an equipment-level Digital Twin, AI-assisted inspection findings, transparent impact estimates and a prioritized remediation workflow.

Potential pricing components:

- One-time site digitization fee per MW
- Inspection processing fee per MW/run
- Annual Digital Twin platform subscription per MW
- O&M workflow seats or enterprise tier
- SCADA integration add-on
- Advanced analytics/API add-on
- Autonomous inspection service later

## Pilot success metrics

- Module/asset association accuracy
- Finding precision and recall by class
- False-positive review rate
- Time from upload to reviewed report
- Technician time to locate asset
- Percentage of findings converted to tasks
- Verified recovered kW/kWh
- Customer trust in evidence and severity
- Repeat-inspection and renewal rate

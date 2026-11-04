# Phase 3C — Inverter visual and thermal inspection UI

## Scope and prototype integration

Phase 3C integrates the deterministic comparison code from `deepdrishti_inverter_visual_test_kit` into the existing inverter drawer. Reusable logic lives in `app/inverter_vision.py`; the existing PV-module demonstration in `app/vision.py` remains independent and unchanged.

This is **not a trained inverter defect-classification model**. It localizes differences between matched inputs for human review. It does not identify corrosion, burns, damaged cables, blocked ventilation, root cause, severity, failure probability or equipment health.

## RGB requirements and algorithm

RGB comparison accepts PNG, JPEG or WEBP pairs up to 8 MB each and 20 megapixels. Both images must have identical dimensions and should show the same inverter, camera position and geometry under comparable lighting.

The deterministic pipeline:

1. safely decodes and EXIF-normalizes both images;
2. optionally downsamples the analysis copy to a 1,024-pixel maximum side;
3. estimates and removes median scene-wide color drift;
4. calculates mean absolute per-pixel channel change;
5. derives a robust threshold from the median/MAD and operator floor;
6. suppresses isolated noise with a small median filter;
7. extracts connected regions and maps boxes to original image pixels;
8. writes a pixel-aligned annotated PNG.

Candidates use `visual_change_unclassified`, a relative change score and `Pending Review`. No candidate is forced when the change mask has no meaningful connected region. Comparisons with more than 40% changed pixels are marked unreliable instead of producing misleading boxes.

Viewpoint changes, compression, shadows, reflections, cleaning and legitimate maintenance can create false positives. A zero-candidate result does not prove that the inverter is healthy.

## Thermal CSV requirements and algorithm

Thermal mode accepts two rectangular UTF-8 numeric matrices in CSV form. Celsius, Fahrenheit and Kelvin inputs are supported and normalized to Celsius. Each matrix is limited to 2 MB and 2,000,000 cells. Dimensions must match.

Blank cells are retained as missing and excluded pairwise. Malformed, infinite and implausible measurements are rejected. The comparison calculates current minus reference temperature for comparable cells, thresholds positive rises in Celsius, extracts connected regions and generates reference, current and difference heatmaps. Each candidate includes its matrix-coordinate box, cell count, threshold and peak measured temperature rise.

Calibration, emissivity, reflected temperature, load comparability and spatial registration are asserted by the uploader; the application cannot independently verify them. Colorized thermal PNG/JPEG files are not accepted in numeric mode because palette colors do not provide calibrated temperatures.

## API

- `POST /api/inverters/{inverter_id}/visual-inspections/rgb/preview`
- `POST /api/inverters/{inverter_id}/visual-inspections/thermal/preview`
- `POST /api/inverters/{inverter_id}/visual-inspections`
- `GET /api/inverters/{inverter_id}/visual-inspections`
- `GET /api/inverter-visual-inspections/{inspection_id}`
- `PATCH /api/inverter-visual-inspections/{inspection_id}/review`
- private preview and saved-evidence retrieval routes

Preview endpoints analyze inputs and create a random, expiring private preview. They do not create or update inverter alerts. Saving is explicit and idempotent for a preview token.

Mutating operations follow the existing local-demo convention: callers must send `demo_mode=true` and `X-DeepDrishti-Demo-Operator: acknowledged`. This is a fail-closed demonstration guard, **not production authentication**.

## Database schema

`inverter_visual_inspections` stores the site/inverter, modality, capture time, input identifiers, method/version, parameters, provenance, telemetry dataset, evidence manifest and aggregate review state.

`inverter_visual_candidates` stores unclassified candidate type, bounding box, region size, supported RGB score or measured thermal rise, evidence location, detection method and latest review state.

`inverter_visual_candidate_review_events` is append-only review history. Site-isolation triggers verify the inverter, inspection, candidate and event relationships on insert/update. These tables are separate from operational inverter alerts and existing PV-module findings.

Customer-like evidence is stored under the private application data directory, not `/static`, and is retrieved only through server-generated inspection/preview identifiers. File names are reduced to basenames; callers cannot choose server paths. Fictional starter fixtures are the only Phase 3C evidence placed under the public sample directory.

## UI workflow

The existing inverter drawer now includes **Visual & Thermal Inspection**:

- select RGB or Thermal mode;
- upload matched images or calibrated matrices;
- run the actual backend comparison;
- inspect before/current/annotated evidence and candidate measurements;
- save selected inspection evidence;
- record Accepted as Relevant Evidence, Rejected, or Needs Further Investigation with an explanation;
- reopen persisted inspection history;
- inspect the nearest reading and recent rule-alert evidence using the explicitly selected telemetry dataset.

Review labels entered in local demo mode are stored as self-asserted labels with `unauthenticated_demo_assertion`. They are not represented as authenticated approvals. Acceptance means relevant supporting evidence only and never resolves an alert, creates a task, confirms root cause or changes an operational anomaly.

## Fictional demonstrations

The bundled starter fixtures support:

- RGB change example: produces backend-calculated candidate boxes;
- healthy/unchanged example: compares the same fictional image and produces zero candidates;
- thermal rise example: produces a backend-calculated 15 °C peak rise from fictional Celsius matrices.

Results are calculated on every request rather than hardcoded. The UI labels inputs and results as fictional demonstration data.

## Security and operational limitations

Production deployment still requires authenticated tenant identity, roles, upload malware/content scanning, retention/deletion policy, encrypted managed storage, rate limiting, audit export and evidence-access authorization. Random evidence identifiers reduce accidental exposure but are not a substitute for authorization.

The deterministic comparator has no real-world accuracy claim. It cannot register different viewpoints or compensate for occlusion and large geometric movement.

## Future trained-model integration

A trained inverter defect classifier would require representative inverter-specific RGB/radiometric datasets; stable asset and defect taxonomies; expert labels with adjudication; train/validation/test separation by site, asset and time; camera/domain-shift testing; calibration and uncertainty evaluation; false-positive/false-negative analysis; independent field validation; versioned model governance; and continued human review. Such a model should emit separate experimental predictions until independently validated—it must not replace the deterministic evidence and operational alert boundaries by default.

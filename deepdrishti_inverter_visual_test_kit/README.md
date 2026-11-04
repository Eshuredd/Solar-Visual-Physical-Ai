# DeepDrishti inverter visual-inspection starter

This is **prototype code**, not a trained AI model and not integrated into the production-shaped FastAPI application. It offers reproducible visual-change localization with an explicit requirement for a matched reference image or calibrated Celsius matrix.

## Run

In a Python environment with Pillow, NumPy and pytest:

```bash
python -m pytest -q test_inverter_visual_candidates.py
python inverter_visual_candidates.py rgb --reference baseline.png --current inspection.png --output annotated.png
python inverter_visual_candidates.py thermal-csv --reference baseline_celsius.csv --current inspection_celsius.csv --delta-threshold-c 10
```

The `baseline.png` and `inspection.png` must represent the **same inverter and camera viewpoint**, with similar lighting and alignment. A camera shift, shadow, reflection or cleaning may create many false candidates. The script does not correct geometric misalignment.

Thermal CSVs must contain spatially registered, calibrated **Celsius measurements** from a suitable radiometric camera export. Colorized thermal JPEG/PNG images are **not** temperatures and must not be fed into the Celsius mode. The 10 °C default is an exploratory numeric threshold, not a field-certified fault criterion. Review emissions, load and environment before interpretation. For arbitrary colorized images, the RGB mode can only show pixel changes and cannot estimate Delta-T.

This code deliberately does **not** classify burn marks, corrosion, cracked cables, blocked ventilation or internal electrical failures. Such classification would require real representative inverter-specific labeled images and independent validation.

## Integrate with existing repo

1. Place `inverter_visual_candidates.py` in `deepdrishti_solar_twin_mvp/app/`.
2. Place or adapt the tests in `deepdrishti_solar_twin_mvp/tests/` and change test imports to `from app.inverter_visual_candidates import ...`.
3. Have Codex add a separate *experimental inverter visual review* endpoint and frontend section. Preserve existing `app/vision.py` PV-module demo untouched.
4. Require site-scoped inverter IDs, authenticated operator identity for anything beyond local demo, image validation, file-size restrictions, content scanning, independent metadata, and audit history.
5. Separate **candidate visual change** from **model prediction** and **confirmed fault**. Never turn a candidate directly into an operational maintenance diagnosis.

The source research is discussed in the companion chat response; no external images or restricted datasets are redistributed here.

## Test immediately with bundled fictional fixtures

These sample diagrams and Celsius CSV matrices are synthetically generated for software testing. They are **not** real inverter inspections or evidence of a diagnosed fault.

After installing dependencies, from this folder run:

```bash
python -m pytest -q test_inverter_visual_candidates.py
python inverter_visual_candidates.py rgb --reference examples/inverter_before.png --current examples/inverter_after.png --output examples/marked_output.png
python inverter_visual_candidates.py rgb --reference examples/inverter_before.png --current examples/inverter_before.png --output examples/no_change_output.png
python inverter_visual_candidates.py thermal-csv --reference examples/thermal_before_celsius.csv --current examples/thermal_after_celsius.csv --delta-threshold-c 10
```

Expected RGB changed-image result: `"status": "candidate_regions"` and at least one candidate bounding box. Open `examples/marked_output.png` to see it. Expected same-image result: `"status": "no_change_detected"`. Expected thermal result: `"status": "candidate_regions"` and a `15.0` Celsius peak increase. The thermal matrix is made-up numbers expressly for a parsing/difference test; 10 C is not a generic safe/unsafe threshold.

The program does not auto-register images and cannot compare different cameras or viewpoints without pre-alignment. It is *not* yet connected to the DeepDrishti UI or an inverter-specific trained vision model.

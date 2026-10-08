# Prompt to paste into Codex after uploading the ZIP

I uploaded `DeepDrishti_Solar_Twin_MVP.zip`. Please treat it as an existing working application, not as a blank project.

Your job is to **unzip, validate and run the application in this environment**.

Follow these steps:

1. Extract the ZIP and enter the `deepdrishti_solar_twin_mvp` directory.
2. Read `README.md`, `docs/ARCHITECTURE.md` and the existing source before changing anything.
3. Create a Python virtual environment and install `requirements.txt`.
4. Run `pytest -q` and fix only genuine environment or compatibility problems. Do not replace the app with a simpler scaffold.
5. Start the server with:

   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

6. Open the browser preview for port 8000 and verify these workflows:
   - Portfolio dashboard loads.
   - Site dashboard loads.
   - Digital Twin map, filters, list and table work.
   - Clicking a marker opens RGB/thermal evidence.
   - A work order can be created and appears under Tasks.
   - Updating a task to Verified resolves the linked anomaly.
   - Inspections → Analyze imagery → Run bundled sample creates new mapped findings.
   - Field app and Assets views render.
   - `/docs` loads the FastAPI API documentation.
7. Preserve the current DeepDrishti visual design, seeded demo data and end-to-end workflow. Do not copy proprietary Raptor Maps assets, branding or code.
8. Once it is running, report:
   - the preview URL,
   - test results,
   - any changes you had to make,
   - the exact commands used.

After the application is running, improve only issues you can reproduce. Keep the project self-contained and runnable without API keys or map tokens.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Write-Host "DeepDrishti Solar Twin is starting at http://127.0.0.1:8000"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

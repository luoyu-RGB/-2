Set-Location $PSScriptRoot
python -m uvicorn backend.app:app --reload --port 8000

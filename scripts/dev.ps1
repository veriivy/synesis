# Run the backend and frontend together in two windows.
#     powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#
# Pass -Replay to serve fixtures/stream.jsonl instead of live agents — no API keys
# needed. That is the backup demo.

param([switch]$Replay)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "No .venv found. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$envPrefix = ""
if ($Replay) {
    $envPrefix = '$env:REPLAY_FIXTURE = "fixtures/stream.jsonl"; '
    Write-Host "REPLAY MODE" -ForegroundColor Yellow
}

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\server'; $envPrefix& '$py' -m uvicorn server.main:app --reload --port 8000"
)

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\web'; npm run dev"
)

Write-Host "api  -> http://localhost:8000/health"
Write-Host "web  -> http://localhost:5173"

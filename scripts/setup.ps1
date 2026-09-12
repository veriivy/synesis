# One-shot dev setup on Windows. Run from the repo root:
#     powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Creates one shared .venv for engine + server, installs both, installs the web deps,
# and copies .env.example to .env if you do not have one yet.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== python venv ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"

& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r engine\requirements.txt --quiet
& $py -m pip install -r server\requirements.txt --quiet
& $py -m pip install -e .\engine --quiet

Write-Host "== web deps ==" -ForegroundColor Cyan
Push-Location web
npm install --no-audit --no-fund
Pop-Location

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "created .env from .env.example — add your API keys" -ForegroundColor Yellow
}

Write-Host "== git ==" -ForegroundColor Cyan
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host ("git: " + (Get-Command git).Source)
} else {
    Write-Host "git is NOT on PATH." -ForegroundColor Red
    Write-Host "The server needs it to clone the demo target and commit as each agent."
    Write-Host "Install Git for Windows: https://git-scm.com/download/win"
    Write-Host "(server/git_ops.py will fall back to GitHub Desktop's bundled copy, but"
    Write-Host " a real install is what you want before the demo.)"
}

Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Green
Write-Host "  1. put your API keys in .env"
Write-Host "  2. .\.venv\Scripts\python.exe -m engine.cli doctor      (run from engine\)"
Write-Host "  3. scripts\dev.ps1                                      (server + web)"

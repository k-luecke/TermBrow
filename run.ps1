#!/usr/bin/env pwsh
# Launch TermBrow using the bundled virtual environment.
$ErrorActionPreference = "Stop"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Setting up virtual environment..." -ForegroundColor Cyan
    python -m venv (Join-Path $PSScriptRoot ".venv")
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}
& $py -m termbrow

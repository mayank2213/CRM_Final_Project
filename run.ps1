$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating project virtual environment...' -ForegroundColor Cyan
    python -m venv .venv
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Python could not create .venv. Install Python 3.10+ and try again.'
}

Write-Host 'Installing project dependencies into .venv...' -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host 'Starting RPATech CRM at http://127.0.0.1:5000' -ForegroundColor Green
& $venvPython app.py

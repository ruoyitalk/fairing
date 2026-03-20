# fairing — Windows entry point (PowerShell)
#
# Usage:
#   .\run.ps1                  # start interactive shell
#   .\run.ps1 run              # run digest non-interactively
#   .\run.ps1 run --chinese
#   .\run.ps1 run --all
#
# First-time setup (once per machine):
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

param([Parameter(ValueFromRemainingArguments)][string[]]$ExtraArgs)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir ".venv"
$Python    = Join-Path $VenvDir "Scripts\python.exe"

# ── 1. Create venv if missing ─────────────────────────────────────────────────
if (-not (Test-Path $Python)) {
    Write-Host "[setup] Creating virtual environment..."
    python -m venv $VenvDir
}

# ── 2. Install / sync dependencies ───────────────────────────────────────────
Write-Host "[setup] Checking dependencies..."
& $Python -m pip install -q -r (Join-Path $ScriptDir "requirements.txt")

# ── 3. Run — forward all args (interactive if no args) ───────────────────────
Write-Host "[run] Starting fairing..."
Set-Location $ScriptDir
& $Python main.py @ExtraArgs

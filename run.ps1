# fairing — Windows entry point (PowerShell)
#
# Usage:
#   .\run.ps1                  # Obsidian note only (default)
#   .\run.ps1 --notebooklm     # also write NotebookLM file
#   .\run.ps1 --chinese        # also write Chinese note (requires GEMINI_API_KEY)
#   .\run.ps1 --all            # all output formats
#
# Run once to allow execution:
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

# ── 3. Run — forward all args to main.py ─────────────────────────────────────
Write-Host "[run] Starting daily digest ($($ExtraArgs -join ' '))..."
Set-Location $ScriptDir
& $Python main.py run @ExtraArgs

# ── 4. Open today's Obsidian note ────────────────────────────────────────────
$ObsidianDir = if ($env:OBSIDIAN_DIR) { $env:OBSIDIAN_DIR } `
               else { Join-Path $env:USERPROFILE "Documents\ruoyinote" }
$ObsidianDir = $ObsidianDir -replace "^~", $env:USERPROFILE

# ISO week (compatible with Windows PowerShell 5+)
$Today    = Get-Date
$Culture  = [System.Globalization.CultureInfo]::InvariantCulture
$ISOWeek  = $Culture.Calendar.GetWeekOfYear(
    $Today,
    [System.Globalization.CalendarWeekRule]::FirstFourDayWeek,
    [System.DayOfWeek]::Monday
)
# Adjust year for week 53/1 boundary
$ISOYear  = if ($Today.Month -eq 12 -and $ISOWeek -eq 1)  { $Today.Year + 1 } `
            elseif ($Today.Month -eq 1  -and $ISOWeek -ge 52) { $Today.Year - 1 } `
            else { $Today.Year }
$WeekStr  = "$ISOYear-W$($ISOWeek.ToString('D2'))"
$DateStr  = $Today.ToString("yyyy-MM-dd")

$Output = Join-Path $ObsidianDir "$WeekStr\$DateStr.md"
if (Test-Path $Output) {
    Write-Host "[done] Opening $Output"
    Invoke-Item $Output
}

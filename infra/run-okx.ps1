<#
Continuous runner for the OKX signal checker on the VPS.

signal_checker.py self-exits after ~4 minutes by design (it was built to be
relaunched every 5 min by GitHub Actions). This wrapper relaunches it in a
tight loop instead, for gap-free coverage - the "continuous" mode - with no
change to the Python code itself.

Fully isolated from the Forex lab: its own repo (C:\OKXAI), its own venv, its
own .env, its own Task Scheduler task (OKX-SignalChecker), its own logs. It
touches nothing under C:\ForexAI and uses no MT5.

Launched at logon by the OKX-SignalChecker scheduled task (see
bootstrap-okx.ps1). Paths are derived from this script's own location, so the
repo can live anywhere.
#>
$ErrorActionPreference = "Stop"
$Root    = Split-Path -Parent $PSScriptRoot           # repo root, e.g. C:\OKXAI
$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$Script  = Join-Path $Root "signal_checker.py"
$EnvFile = Join-Path $Root ".env"
$LogDir  = Join-Path $Root "logs"
$Log     = Join-Path $LogDir "okx-signal-checker.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$m) {
    "{0:u} [runner] {1}" -f (Get-Date).ToUniversalTime(), $m | Add-Content -Path $Log -Encoding utf8
}

if (-not (Test-Path $Python)) { Log "FATAL venv python missing at $Python - run bootstrap-okx.ps1"; throw "venv missing" }
if (-not (Test-Path $Script)) { Log "FATAL signal_checker.py missing at $Script"; throw "script missing" }
if (-not (Test-Path $EnvFile)) { Log "FATAL .env missing at $EnvFile"; throw ".env missing" }

# --- load .env into THIS process's environment; signal_checker.py reads
#     os.environ directly (it never loaded a .env - GitHub set them). Child
#     python inherits whatever we set here.
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $name = $matches[1]; $val = $matches[2].Trim()
        if ($val -match '^"(.*)"$') { $val = $matches[1] }
        elseif ($val -match "^'(.*)'$") { $val = $matches[1] }
        [Environment]::SetEnvironmentVariable($name, $val, "Process")
    }
}

# UTF-8 so a printed Telegram line containing emoji cannot crash Python with a
# cp1252 UnicodeEncodeError when stdout is redirected to the log file.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Run from the repo dir so signal_cache.json (the dedup state) persists there
# and survives every relaunch - which is what keeps continuous mode from
# re-alerting or re-trading on a signal it already handled.
Set-Location $Root

Log "runner starting (continuous)"
while ($true) {
    $started = Get-Date
    try {
        & $Python $Script *>> $Log
    } catch {
        Log ("launch error: " + $_.Exception.Message)
    }
    $ran = ((Get-Date) - $started).TotalSeconds

    # A healthy run lasts ~4 min. A near-instant exit means a crash (bad .env,
    # missing dependency, OKX auth failure) - back off so it can't spin-crash
    # and flood the log or hammer the API.
    if ($ran -lt 15) {
        Log ("exited after {0:N0}s - looks like a crash, backing off 60s" -f $ran)
        Start-Sleep -Seconds 60
    } else {
        Start-Sleep -Seconds 3
    }

    # Keep the log bounded: at 20 MB, roll to .1 (single generation is plenty
    # for a worker that also reports to Telegram and Supabase).
    if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 20MB)) {
        Move-Item -Force $Log ($Log + ".1")
    }
}

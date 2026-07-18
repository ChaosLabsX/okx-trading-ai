<#
One-time VPS setup for the OKX signal checker. Run ONCE, in an elevated
PowerShell, after cloning this repo to C:\OKXAI and creating C:\OKXAI\.env.

It is idempotent (safe to re-run) and touches nothing outside this repo folder
and its own scheduled task. It does NOT go near C:\ForexAI, the MT5 terminals,
or any ForexAI-* task.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # repo root, e.g. C:\OKXAI

Write-Host "OKX bootstrap - repo root: $Root" -ForegroundColor Cyan

# 1. .env must exist and carry the keys the worker reads (fail early, clearly).
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    throw ".env not found at $EnvFile - create it first (see infra\VPS-SETUP.md), then re-run."
}
$need = 'OKX_API_KEY','OKX_SECRET_KEY','OKX_PASSPHRASE','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','SUPABASE_URL','SUPABASE_KEY'
$have = @(Get-Content $EnvFile | ForEach-Object { if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\S') { $matches[1] } })
$missing = $need | Where-Object { $_ -notin $have }
if ($missing) { Write-Warning ("`.env is missing or blank for: " + ($missing -join ', ')) }
else { Write-Host "  .env: all required keys present" -ForegroundColor Green }

# 2. Separate venv - its own dependency world, so nothing here can affect the
#    Forex engine's venv (MetaTrader5 etc.) and vice versa.
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "  creating venv..." -ForegroundColor Cyan
    python -m venv (Join-Path $Root ".venv")
}
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet requests
Write-Host "  venv ready ($((& $Py --version)))" -ForegroundColor Green

# 3. logs dir
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

# 4. Scheduled task: launch the continuous wrapper at logon (the VPS auto-logs
#    in, same mechanism the Forex engine relies on), restart it if it ever dies,
#    and never time it out - the wrapper is meant to run forever.
$runner   = Join-Path $Root "infra\run-okx.ps1"
$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
              -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
              -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
              -MultipleInstances IgnoreNew `
              -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "OKX-SignalChecker" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "  task 'OKX-SignalChecker' registered" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Start it now with:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName 'OKX-SignalChecker'"
Write-Host "Then watch:" -ForegroundColor Cyan
Write-Host "  Get-Content $Root\logs\okx-signal-checker.log -Tail 20 -Wait"

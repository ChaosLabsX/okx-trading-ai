# Running the OKX worker on the VPS (continuous)

Moves `signal_checker.py` off GitHub Actions + cron-job.org onto the Windows
VPS, running **continuously** (relaunched the instant it self-exits) for
gap-free coverage. No Python code changes - a wrapper sets the env and relaunches.

**Isolation:** everything lives in `C:\OKXAI`, with its own venv, `.env`, logs,
and one scheduled task (`OKX-SignalChecker`). It shares nothing with `C:\ForexAI`
and never touches MT5. Deleting `C:\OKXAI` and the task removes it completely.

## Steps (on the VPS, via RDP)

**1. Clone the repo to `C:\OKXAI`**
```powershell
git clone https://github.com/ChaosLabsX/okx-trading-ai.git C:\OKXAI
```

**2. Create `C:\OKXAI\.env`** from the template, and paste in the same secrets
you have in GitHub Actions:
```powershell
Copy-Item C:\OKXAI\infra\.env.example C:\OKXAI\.env
notepad C:\OKXAI\.env
```

**3. Bootstrap** (elevated PowerShell - creates the venv, installs `requests`,
registers the task):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\OKXAI\infra\bootstrap-okx.ps1
```

**4. Start it and watch the first run**
```powershell
Start-ScheduledTask -TaskName "OKX-SignalChecker"
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 25 -Wait
```
You want to see scans running and, within a few minutes, a Telegram message
from the OKX bot. (Ctrl+C stops the `-Wait` tail; it does not stop the worker.)

**5. Only once step 4 looks healthy, retire the old triggers** so both can't
run at once (double runs = duplicate trades):
- GitHub → repo **Actions** tab → **Crypto Signal Checker** → **…** → **Disable workflow**
- **cron-job.org** → pause/disable the OKX job

## Everyday commands

```powershell
# tail the log
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 30

# stop / start / restart
Stop-ScheduledTask  -TaskName "OKX-SignalChecker"; Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
Start-ScheduledTask -TaskName "OKX-SignalChecker"

# deploy an update
cd C:\OKXAI; git pull; Stop-ScheduledTask -TaskName "OKX-SignalChecker"; Start-ScheduledTask -TaskName "OKX-SignalChecker"
```

To go back to GitHub Actions: stop/unregister the task and re-enable the
workflow + cron-job.org. Nothing here is one-way.

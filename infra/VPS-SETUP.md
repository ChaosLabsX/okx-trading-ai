# Running the OKX worker on the VPS (continuous)

**This is how the worker runs. There is no other runner.** `signal_checker.py`
runs **continuously** on the Windows VPS (relaunched the instant it self-exits)
for gap-free coverage. A wrapper sets the env and relaunches; no Python changes.

This replaced GitHub Actions + cron-job.org, and that path is gone - the
workflow file has been deleted and there is no `.github/workflows/` directory.
The only GitHub Actions runs you will see are `pages-build-deployment`, which
publishes the dashboard and has nothing to do with trading.

**Isolation:** everything lives in `C:\OKXAI`, with its own venv, `.env`, logs,
and one scheduled task (`OKX-SignalChecker`). It shares nothing with `C:\ForexAI`
and never touches MT5. Deleting `C:\OKXAI` and the task removes it completely.

## Steps (on the VPS, via RDP)

**1. Clone the repo to `C:\OKXAI`**
```powershell
git clone https://github.com/ChaosLabsX/okx-trading-ai.git C:\OKXAI
```

**2. Create `C:\OKXAI\.env`** from the template. `infra/.env.example` lists every
variable the worker needs - it is the canonical list:
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

**5. Nothing to retire.** The old triggers are already gone: the workflow file
has been deleted from the repo. If a **cron-job.org** job still exists in your
account, delete it and revoke its GitHub PAT - it fires at nothing now, but it
is a scheduled request carrying a write-scoped token for no reason.

## Everyday commands

```powershell
# tail the log
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 30

# stop / start / restart
Stop-ScheduledTask  -TaskName "OKX-SignalChecker"; Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
Start-ScheduledTask -TaskName "OKX-SignalChecker"

# deploy an update
# Stop the task AND kill the running python first: Stop-ScheduledTask ends the
# task, not the process the wrapper already launched. Pulling without the kill
# leaves the old code running until it happens to self-exit, so a "successful"
# deploy can silently change nothing.
Stop-ScheduledTask -TaskName "OKX-SignalChecker"
Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
cd C:\OKXAI; git pull
Start-ScheduledTask -TaskName "OKX-SignalChecker"
```

Verify a deploy actually landed by checking the log for behaviour from the new
code, not just that `git pull` printed something:
```powershell
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 40
```

**Going back to GitHub Actions** is no longer a toggle - the workflow file was
deleted. Recover it with
`git show 151be53:.github/workflows/signal-checker.yml`, restore it to
`.github/workflows/`, re-add the secrets from `infra/.env.example` as GitHub
Secrets, and recreate the cron-job.org job. Stop the VPS task first: two
runners at once means duplicate trades.

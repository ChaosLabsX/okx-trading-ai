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

**There is no window to look for.** The task runs as `S4U` ("run whether user is
logged on or not"), in its own session, so nothing appears on the desktop and
signing out of RDP does not stop it. The log is the only view. To check it is
alive:
```powershell
Get-ScheduledTask -TaskName "OKX-SignalChecker" | Select-Object State, @{n='RunsAs';e={$_.Principal.LogonType}}
Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Select-Object Id, SessionId
```
`Running` / `S4U`, and a python process in **session 0**, means it is detached from
your login. A non-zero session id means it is living inside an RDP session and
will die when that session signs out.

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

## "OKX STILL SILENT · task=Ready · py=0"

That is `watchdog-okx.ps1` reporting that the log has stopped growing, the task is
idle and no worker process exists. Before 2026-09-21 the task was registered
`Interactive` + at-logon only, so signing out of RDP (or a reboot without
auto-logon) killed the worker and nothing restarted it. It happened twice.

A task registered by an older `bootstrap-okx.ps1` still has that setup. Convert it
in place (elevated PowerShell, one time), then start it:
```powershell
Stop-ScheduledTask -TaskName "OKX-SignalChecker"
Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$t = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn))
Set-ScheduledTask -TaskName "OKX-SignalChecker" -Principal $p -Trigger $t
Start-ScheduledTask -TaskName "OKX-SignalChecker"
```
Re-running `bootstrap-okx.ps1` does the same thing. The stop-and-kill first
matters: a logon may already have started a runner under the old setup, and two
runners at once means duplicate trades.

**Going back to GitHub Actions** is no longer a toggle - the workflow file was
deleted. Recover it with
`git show 151be53:.github/workflows/signal-checker.yml`, restore it to
`.github/workflows/`, re-add the secrets from `infra/.env.example` as GitHub
Secrets, and recreate the cron-job.org job. Stop the VPS task first: two
runners at once means duplicate trades.

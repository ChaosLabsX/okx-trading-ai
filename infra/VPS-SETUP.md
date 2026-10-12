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

# keep it stopped (maintenance, key rotation). Since 2026-10-12 the task restarts
# itself within 5 minutes, so a plain Stop is undone - disable it instead:
Disable-ScheduledTask -TaskName "OKX-SignalChecker"; Stop-ScheduledTask -TaskName "OKX-SignalChecker"; Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
Enable-ScheduledTask  -TaskName "OKX-SignalChecker"; Start-ScheduledTask -TaskName "OKX-SignalChecker"

# deploy an update
# Stop the task AND kill the running python first: Stop-ScheduledTask ends the
# task, not the process the wrapper already launched. Pulling without the kill
# leaves the old code running until it happens to self-exit, so a "successful"
# deploy can silently change nothing.
Stop-ScheduledTask -TaskName "OKX-SignalChecker"
Get-Process python -EA SilentlyContinue | ? Path -like 'C:\OKXAI\*' | Stop-Process -Force
cd C:\OKXAI; git pull
Start-ScheduledTask -TaskName "OKX-SignalChecker"

# after changing CLAUDE_MODEL or CLAUDE_EFFORT: prove the AI calls work with this
# box's key (two short real calls, ~1 cent). A failing AI call does not crash the
# worker, it turns every trade into a logged skip, so without this a broken model
# switch looks exactly like a quiet market.
cd C:\OKXAI; .venv\Scripts\python.exe ai_check.py
```

Verify a deploy actually landed by checking the log for behaviour from the new
code, not just that `git pull` printed something:
```powershell
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 40
```

## "OKX STILL SILENT · task=Ready · py=0"

That is `watchdog-okx.ps1` reporting that the log has stopped growing, the task is
idle and no worker process exists — the runner itself has ended.

- Before 2026-09-21 the task was `Interactive` + at-logon only, so signing out of
  RDP killed the worker and nothing restarted it. Fixed with `S4U` + at-startup.
- On 2026-10-12 it happened a third time under `S4U`, with nobody signing out.
  Whatever the cause, the task now also has a **repeating trigger every 5
  minutes**: when the runner is not running it is started, when it is,
  `MultipleInstances IgnoreNew` makes the firing a no-op (never two runners —
  that would mean duplicate trades). An outage now lasts at most ~5 minutes.

A task registered by an older `bootstrap-okx.ps1` lacks some of that. Bring it up
to date in place (elevated PowerShell, one time):
```powershell
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$t = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn), (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)))
Set-ScheduledTask -TaskName "OKX-SignalChecker" -Principal $p -Trigger $t
Start-ScheduledTask -TaskName "OKX-SignalChecker"
```
Re-running `bootstrap-okx.ps1` does the same thing. Check the repeat stuck with
`(Get-ScheduledTask -TaskName "OKX-SignalChecker").Triggers | % { $_.Repetition.Interval }`
— one line should read `PT5M`.

To find out *why* a runner ended, before restarting it (a restart overwrites the
last result):
```powershell
$i = Get-ScheduledTaskInfo -TaskName "OKX-SignalChecker"; "last run $($i.LastRunTime), result 0x{0:X}" -f $i.LastTaskResult
"booted " + (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-Content C:\OKXAI\logs\okx-signal-checker.log -Tail 20
```
`0x0` = the runner script ended on its own; `0x41306` = stopped by someone or
something; `0x40010004` / `0xC000013A` = its process was killed (shutdown, logoff,
console closed); a boot time close to the log's last line = a reboot.

**Going back to GitHub Actions** is no longer a toggle - the workflow file was
deleted. Recover it with
`git show 151be53:.github/workflows/signal-checker.yml`, restore it to
`.github/workflows/`, re-add the secrets from `infra/.env.example` as GitHub
Secrets, and recreate the cron-job.org job. Stop the VPS task first: two
runners at once means duplicate trades.

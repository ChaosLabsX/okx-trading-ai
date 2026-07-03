# Scheduling: GitHub Actions cron + cron-job.org

The worker must run roughly every 5 minutes, 24/7. Two triggers cooperate, defined in `.github/workflows/signal-checker.yml`:

```yaml
on:
  schedule:
    - cron: '*/5 * * * *'   # every 5 minutes (backup — GitHub cron can be delayed)
  workflow_dispatch:          # manual trigger from the GitHub UI
  repository_dispatch:        # external trigger via GitHub API (used by cron-job.org)
    types: [run-signal-checker]
```

## Why cron-job.org is used at all

GitHub's `schedule` cron is **best-effort**: on busy runner pools a `*/5` schedule routinely fires 10–30+ minutes late or skips slots entirely. That's unacceptable when open trades need monitoring. So the primary trigger is external: **cron-job.org** (a free web scheduler) fires a `repository_dispatch` event on a reliable 5-minute cadence, and GitHub's own cron stays enabled purely as a backup.

## How the cron-job.org job is configured

The job is a plain scheduled HTTPS request configured on <https://cron-job.org> (account-side configuration — not stored in this repo):

| Setting | Value |
|---|---|
| URL | `https://api.github.com/repos/ChaosLabsX/trading-ai/dispatches` |
| Method | `POST` |
| Schedule | every 5 minutes |
| Header | `Authorization: Bearer <GITHUB_PERSONAL_ACCESS_TOKEN>` |
| Header | `Accept: application/vnd.github+json` |
| Header | `Content-Type: application/json` |
| Body | `{"event_type": "run-signal-checker"}` |

Requirements & gotchas:

- The `event_type` string must exactly match the `types:` entry in the workflow (`run-signal-checker`), or GitHub accepts the request (HTTP 204) but no workflow runs.
- The PAT needs permission to create repository dispatches: classic token with `repo` scope, or a fine-grained token with **Contents: Read and write** on `ChaosLabsX/trading-ai`. If the token expires, the external trigger silently stops — the only symptom is runs arriving on GitHub's laggy backup cron. Check cron-job.org's execution history (it shows the HTTP status; 204 = success, 401/404 = bad/expired token).
- `repository_dispatch` workflows always run on the **default branch** (`main`).

## Double-trigger protection

Both triggers firing near-simultaneously is expected and safe:

```yaml
concurrency:
  group: signal-checker
  cancel-in-progress: false   # queue instead of cancel
```

Only one run executes at a time; a second trigger queues. Combined with the ~4-minute self-exit (`LOOP_DURATION` in `signal_checker.py`) and `timeout-minutes: 6`, the effective result is near-continuous coverage: each run scans every 60 s for ~4 min, the next trigger picks up moments later.

Duplicate *alerts* are additionally prevented one layer down by the `signal_cache.json` zone/cooldown dedup state, which is restored from `actions/cache` at the start of every run and saved (`if: always()`) at the end — see [SIGNAL-CHECKER.md](SIGNAL-CHECKER.md#alert-deduplication-zone-system--cache).

## Operating it

- **Pause all automated trading:** disable the cron-job.org job **and** disable the workflow in the GitHub Actions UI (disabling the workflow alone is enough — repository_dispatch events to a disabled workflow do nothing).
- **Force an immediate run:** GitHub → Actions → "Crypto Signal Checker" → *Run workflow* (`workflow_dispatch`).
- **Reset alert state** (e.g. after enabling `TEST_FORCE_SIGNAL`): GitHub → Actions → Caches → delete the `signal-cache-*` entry. The next run then does a silent warm-up scan (no alerts, no trades) to rebuild state.
- **Verify the pipeline end-to-end:** watch a run's log — it prints per-coin signal lines, `[AutoTrade]`, `[Trade]`, `[Option3]`, `[Supabase]`, and `[Telegram]` step markers.

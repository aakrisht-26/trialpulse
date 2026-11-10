# Deployment runbook: the API on Render and the dashboard on Streamlit Community Cloud

Research demo. Not medical advice. Not for patient decision-making.

This runbook is for Aakrisht. It deploys the read-only API (Step 15, ADR 0065) and the dashboard (Step 16). Every value below is a placeholder written `<LIKE_THIS>`; real values live only in `.env` on the laptop and in each platform's own secret store. Never paste a secret into a chat, an issue, a commit or a file in the repository.

**Before you start**

- Neon is rebootstrapped and its reference check is equal (docs/progress.md, "the Neon runbook"), and the display, reason and statement loads have run on Neon.
- The API's read-only role exists on Neon and `API_DATABASE_URL` (Neon's **pooled** URL for role `trialpulse_api`, ADR 0049 and ADR 0064) is in `.env`.
- CI is green on `main` (`gh run list --limit 1`).

Sources: Render's documentation ([web services](https://render.com/docs/web-services), [Docker](https://render.com/docs/docker), [Blueprint specification](https://render.com/docs/blueprint-spec), [health checks](https://render.com/docs/health-checks), [free instances](https://render.com/docs/free), [environment variables](https://render.com/docs/configure-environment-variables)) and Streamlit's ([deploy your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [app dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies), [file organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization), [secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [multipage apps](https://docs.streamlit.io/develop/concepts/multipage-apps)), read on 2026-11-08.

## Part 1: the API on Render

What the repository already holds:

- `render.yaml`, a Blueprint with one free web service, `trialpulse-api`, built from `Dockerfile.api` in region `virginia` (Neon's project is in AWS us-east-1), with the health check on `/health` (which never touches Neon), deploys only after CI passes on `main`, `PORT` set to 10000, and `API_DATABASE_URL` marked `sync: false`, so it has no value in the file.
- `Dockerfile.api` binds `0.0.0.0:${PORT}` (Render routes public traffic to the port in `PORT`; its default is 10000). The image holds `pyproject.toml`, `uv.lock`, `config/` and `src/` only: `.dockerignore` is an allowlist, so `data/` and `.env` can never enter it (`tests/api/test_render.py`).

Steps:

1. Sign in at <https://dashboard.render.com> (with GitHub). Allow Render to read the `trialpulse` repository only, not all repositories.
2. Click **New**, then **Blueprint**. Pick the `trialpulse` repository and the `main` branch. Render reads `render.yaml` and lists one service, `trialpulse-api`.
3. Render asks for the value of each `sync: false` variable, once, at this step. For **API_DATABASE_URL**, open `.env` on the laptop, copy the value after `API_DATABASE_URL=` and paste it into Render's field. Do not paste it anywhere else. Render never reads it from the repository and later Blueprint syncs leave it alone.
4. Click **Apply** (or **Deploy Blueprint**). Render builds the image with BuildKit (no CA secret is needed on Render).
5. Watch the deploy's logs. Expect `Uvicorn running on http://0.0.0.0:10000` and then the deploy going live. Render routes traffic only once `/health` answers 2xx or 3xx within five seconds; a deploy that never passes is cancelled after 15 minutes.
6. Note the service's address, `https://<SERVICE>.onrender.com` (on the service's page).

What to check after Part 1 (from the laptop, PowerShell):

```
curl.exe -s https://<SERVICE>.onrender.com/health
```
Expect `{"status":"ok","data_as_of":...}` (the date is null until the first `/v1/` request has read it).

```
curl.exe -s -o NUL -w "%{http_code}" https://<SERVICE>.onrender.com/docs
```
Expect `200` (open it in a browser too: the OpenAPI page renders).

```
curl.exe -s https://<SERVICE>.onrender.com/v1/trials/<A_SCORED_NCT_ID>/risk
```
Expect a JSON body with `risk_12m`, `risk_24m`, five drivers per horizon and the notice. The first `/v1/` request after Neon has idled may answer 503 "the database is waking up" with `Retry-After: 30`; try again after 30 seconds.

Free-plan facts to expect: the service spins down after 15 minutes without inbound traffic and takes about a minute to start again; a workspace has 750 free instance hours a month; Render may restart a free service at any time. The dashboard's client waits out a cold start (ADR 0054).

### The X-Forwarded-For measurement (ADR 0065, decision 3)

Render does not document its proxy chain, so the per-client rate limit (60 requests a minute on `/v1/`, ADR 0049) is not relied on until this is measured. `api.trusted_proxies` in `config/project.yaml` is 1 until then.

1. From the laptop, run the plain measurement:

```
uv run python scripts/measure_forwarded.py --url https://<SERVICE>.onrender.com
```
Expect about 60 answers `200` and the rest `429`.

2. Wait a minute, then the forged one (each request carries its own made-up `X-Forwarded-For` entry):

```
uv run python scripts/measure_forwarded.py --url https://<SERVICE>.onrender.com --forge
```

3. Wait a minute, then run the plain measurement from the laptop and, at the same time, from a second network (a phone's hotspot on a second computer, or ask someone).

How to read it:

| Result | Meaning | Action |
| --- | --- | --- |
| Step 1 limits at 60, step 2 limits at 60, step 3 gives each network its own 60 | The key is the client's address as Render appends it | Keep `trusted_proxies: 1`; record the measurement in ADR 0065 |
| Step 2 shows no 429 | The key is the entry a client writes: anyone can dodge the limit | Do not count on the limit; record it and propose a change to `trusted_proxies` with its evidence |
| Step 3: the two networks share one budget | The key is a proxy's address, not the client's | Same as above |

## Part 2: the dashboard on Streamlit Community Cloud

What the repository already holds:

- The entry point `app/Home.py`, which lists its six pages (`st.navigation`) from `app/pages/` beside it.
- `app/requirements.txt`, the dashboard's packages only (`httpx`, `plotly`, `streamlit`) at `uv.lock`'s versions (`tests/app/test_imports.py`). Community Cloud looks for a dependency file in the entry point's folder first, then at the repository's root, and uses only the first it finds, so this file is used instead of the root's `uv.lock` and `pyproject.toml`.
- `.streamlit/config.toml` at the repository's root.
- The dashboard reads the API's address from the secret `API_URL` (`app/dashboard/ui.py`) and reaches nothing but the API.

Steps:

1. Sign in at <https://share.streamlit.io> with GitHub. Community Cloud needs admin access to the repository to deploy it.
2. Click **Create app**, then deploy from a GitHub repository.
3. Fill in: repository `aakrisht-26/trialpulse`, branch `main`, main file path `app/Home.py`. An app address (subdomain) is optional.
4. Open **Advanced settings**:
   - **Python version:** 3.12 (the project's version; it is also Community Cloud's default).
   - **Secrets:** paste exactly one line in TOML form, with the API's address from Part 1:

```
API_URL = "https://trialpulse-api.onrender.com"
```
   The API's address is not a secret, but this is where the app reads it. No database URL or key goes here: the dashboard never talks to the database.
5. Click **Deploy**. The first install takes a few minutes.

What to check after Part 2:

1. The home page loads and shows the notice "Research demo. Not medical advice. Not for patient decision-making." and the source attribution with the data-as-of date (ADR 0008).
2. Open each page in the sidebar: Overview, Portfolio, Trial detail, Landscape, Why trials stop and Model health. Each shows the notice and the attribution.
3. Trial detail: enter `NCT02957968` (a scored trial). Expect the score card (risks at 12 and 24 months, five drivers per horizon) and the timeline. Enter `NCT00000000`, which is not served: expect the plain "not found" message, not an error.
4. Cold start: after the API has idled for over 15 minutes, open a page. Expect the waiting message (the service starting), then the page; never a traceback (ADR 0054).
5. Secrets can be changed later in the app's settings in your workspace; the app restarts with them.

## Part 3: the state after go-live

1. The API has been live on Render since 2026-11-08, at https://trialpulse-api.onrender.com, and the README links it. The dashboard is not deployed yet; its link replaces the README's "not deployed yet" after Part 2.
2. The X-Forwarded-For measurement is recorded in ADR 0065 (section "Measured (2026-11-10)"): the rate limit is keyed by `CF-Connecting-IP`.
3. `daily.yml` has been enabled since 2026-11-08: its first run on Neon was a manual dispatch that day, and it runs on its schedule since. It must not be disabled except to stop a failing job; a failed run opens a "Daily live run failed" issue.
4. If anything fails, the API can be suspended from its service page on Render, and the app can be deleted or rebooted from the Community Cloud workspace. Neither holds data.

## Part 4: Backups

ADR 0045 (decision 11): a weekly scheduled task on the laptop backs up the live system to `data/backups/` (gitignored, never published). The command is `uv run python -m trialpulse.live.backup backup --target neon`: one read-only snapshot of every `live`, `serving` and `monitoring` table, each file checked by row count and SHA-256 after it is written, the last 8 backups kept (`live.backup_keep`). The task runs it through `scripts/backup_weekly.ps1`, which logs to `data/logs/backup_<date>.log` and, if the task starts late inside the daily job's window (21:30 to 00:30 UTC), waits until 00:30 UTC.

**When:** Sundays at 19:00 India time (13:30 UTC). That is outside the daily job's cron window (21:30 to 00:30 UTC, 03:00 to 06:00 India time) and outside the span in which GitHub has really started the job so far (about 01:00 to 08:00 UTC, 06:30 to 13:30 India time), at an hour the laptop is likely on. `StartWhenAvailable` runs a missed backup when the laptop wakes. One backup takes about a minute (51 s on 2026-11-10, 68 MB), so it may run on battery.

Register the task (PowerShell, once, as Aakrisht; no administrator rights needed):

```
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File E:\trialpulse\scripts\backup_weekly.ps1" -WorkingDirectory "E:\trialpulse"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "19:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "TrialPulse weekly backup" -Action $action -Trigger $trigger -Settings $settings -Description "Weekly backup of the live system to data/backups (ADR 0045)"
```

The time limit of 3 hours covers the wait of a late start inside the job's window (at most 3 hours) plus the backup itself.

See it, run it now, see its last result, and remove it:

```
Get-ScheduledTask -TaskName "TrialPulse weekly backup" | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName "TrialPulse weekly backup"
Get-Content (Get-ChildItem E:\trialpulse\data\logs\backup_*.log | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
Unregister-ScheduledTask -TaskName "TrialPulse weekly backup" -Confirm:$false
```

`LastTaskResult` 0 is a backup written; 2 is a refusal (for example, a backup for that date already exists, which is never overwritten). `uv` must be on the PATH of Aakrisht's account, as it is in his terminal.

**Restore,** only into the laptop's Postgres, never Neon (it refuses any host but localhost and 127.0.0.1): it creates a fresh database `trialpulse_restore` (dropped first if it exists), migrates it to the backup's Alembic revision, loads every table and checks each table's rows and digest against the backup:

```
uv run python -m trialpulse.live.backup restore --from data/backups/2026-11-10
```

Expected: `"equal": true` and exit code 0.

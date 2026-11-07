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
3. Fill in: repository `<GITHUB_USER>/trialpulse`, branch `main`, main file path `app/Home.py`. An app address (subdomain) is optional.
4. Open **Advanced settings**:
   - **Python version:** 3.12 (the project's version; it is also Community Cloud's default).
   - **Secrets:** paste exactly one line in TOML form, with the API's address from Part 1:

```
API_URL = "https://<SERVICE>.onrender.com"
```
   The API's address is not a secret, but this is where the app reads it. No database URL or key goes here: the dashboard never talks to the database.
5. Click **Deploy**. The first install takes a few minutes.

What to check after Part 2:

1. The home page loads and shows the notice "Research demo. Not medical advice. Not for patient decision-making." and the source attribution with the data-as-of date (ADR 0008).
2. Open each page in the sidebar: Overview, Portfolio, Trial detail, Landscape, Why trials stop and Model health. Each shows the notice and the attribution.
3. Trial detail: enter `<A_SCORED_NCT_ID>`. Expect the score card (risks at 12 and 24 months, five drivers per horizon) and the timeline. Enter an id that is not served: expect the plain "not found" message, not an error.
4. Cold start: after the API has idled for over 15 minutes, open a page. Expect the waiting message (the service starting), then the page; never a traceback (ADR 0054).
5. Secrets can be changed later in the app's settings in your workspace; the app restarts with them.

## Part 3: after both are live

1. Put the two public addresses in the README's "Live links" (Step 18 owns the README's wording).
2. Record the X-Forwarded-For measurement in ADR 0065.
3. Keep `daily.yml` disabled until the Neon runbook says to enable it.
4. If anything fails, the API can be suspended from its service page on Render, and the app can be deleted or rebooted from the Community Cloud workspace. Neither holds data.

# 0065. The API runs on Render's free web service

- Date: 2026-11-04
- Status: **Accepted.** Decided by Aakrisht on 2026-11-04 (review of Steps 14 to 16, finding 2).
- Amends CLAUDE.md Section 13 (Deployment) and Step 15 (Build). Refines ADR 0049 (the API's connection and rate limit).

## Context

Section 13 put the API on Hugging Face Spaces as a Docker image. The review of Steps 14 to 16 (finding 2) found that it cannot run there. Hugging Face's Spaces overview page says Spaces allow outbound requests on ports 80, 443 and 8080 only, and the API reaches Neon on port 5432. Docker Spaces also now need a paid plan.

The research of 2026-11-01 (docs/progress.md, "Finding 2: where the API can run") compared Render, Vercel, Koyeb and Spaces against what the API needs: a container or a Python ASGI app, outbound TCP to Neon on port 5432, no payment card, a wake that fits the dashboard's 3-minute budget (ADR 0054), and terms that allow a non-commercial research demo.

## Decision

1. The API runs on Render's free web service, built from `Dockerfile.api` as it is.
2. Its secrets (`API_DATABASE_URL`) live in Render's environment settings and nowhere in the repository, as Section 13 asks of every platform.
3. Render's `X-Forwarded-For` behaviour is measured on the deployed service, and `api.trusted_proxies` in config/project.yaml is set from that measurement, before the per-client rate limit of ADR 0049 is relied on. Render does not document its proxy chain: its staff say the first entry is the client, the service sits behind Cloudflare, and the header can be spoofed. Until the measurement, the rate limit is not counted on.

## Alternatives

- **Hugging Face Spaces, as planned.** Outbound port 5432 is blocked, and a Docker Space needs a paid plan.
- **Vercel (Hobby).** No card and terms that match the non-commercial rule, but serverless instances each keep their own copy of ADR 0049's in-process cache and rate limit, every cold instance reaches Neon, and the default dependencies would exceed its 500 MB bundle limit. It is the fallback if Render's terms or limits change.
- **Koyeb.** It needs a payment card.

## Consequences

- The free instance spins down after 15 minutes without traffic and takes about a minute to wake; the dashboard's client waits out the loading page (ADR 0063) within its 3-minute budget (ADR 0054).
- Without a payment method, a free service is suspended for the rest of the month once its bandwidth is used up. Render's terms describe free instances as meant for hobby and test projects and grant use for the owner's own use only, which fits a non-commercial research demo (Section 2).
- `api.trusted_proxies` keeps its value until decision 3's measurement; its comment says so.
- The README's deployment link and Step 15's public URL point to Render once the API is deployed.

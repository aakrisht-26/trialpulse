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

## Measured (2026-11-10)

**The symptom.** On the deployed service (commit `6ceaf9e`), `scripts/measure_forwarded.py` sent 70 requests to `/v1/model` in 23 s and got 70 answers of 200, no 429: the per-client limit of ADR 0049 did not hold.

**The diagnostic.** A temporary log line on `/v1/` requests (`api.forwarded_diagnostic`, commit `41df250`, deployed by Render's auto-deploy and shown live on the service's events page) recorded the shape of the headers only: the number of X-Forwarded-For entries, whether CF-Connecting-IP and True-Client-IP were present, the X-Forwarded-For position (from the end) equal to CF-Connecting-IP, and whether each value stayed the same as the previous request's. No address and no hash of one was logged. Three runs from the laptop, a minute apart, read in Render's application log:

| Run | Answers | Shape in the log |
| --- | --- | --- |
| Plain, 70 requests | 69 x 200, 1 dropped connection (counted, not raised) | 3 X-Forwarded-For entries; CF-Connecting-IP and True-Client-IP present; CF-Connecting-IP equal to the third entry from the end (the first) and the same on every request; the last and the second-to-last entries changed between requests |
| X-Forwarded-For forged per request | 70 x 200 | 4 entries (the forged one first); CF-Connecting-IP still the third from the end and the same on every request; the fourth from the end (the forged entry) changed every request |
| CF-Connecting-IP and True-Client-IP forged per request | 70 x 403 in 3 s, answered by Cloudflare (`Server: cloudflare`, "error code: 1000"), not by the API | none: no request reached the service |

**The cause, confirmed.** Behind Render the header reads "client, hop, hop", and the two hops nearest the API change from request to request (Cloudflare's and Render's proxies). `trusted_proxies: 1` took the last hop, so the key changed almost every request.

**The key: CF-Connecting-IP, with the X-Forwarded-For rule as fallback** (`api.client_ip_header: cf-connecting-ip`; `trusted_proxies: 1` applies only when the header is absent, as in local runs and tests). Against the three conditions:

- (a) the same client gets the same key: CF-Connecting-IP was the same on every logged request of the plain and forged runs from one laptop;
- (b) a forged X-Forwarded-For cannot change it: with a forged entry per request, CF-Connecting-IP stayed the same; a client that sends its own CF-Connecting-IP is refused at Cloudflare's edge (measured: 70 of 70), and Cloudflare's documentation lists a request carrying a CF-Connecting-IP header as a cause of error 1000 (https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1000/). Cloudflare documents the header as the address of the client that connected to it, sent on traffic from its edge to the origin, and advises reading it rather than X-Forwarded-For for the visitor's address (https://developers.cloudflare.com/fundamentals/reference/http-headers/);
- (c) two networks get different keys: CF-Connecting-IP is the connecting address, so two networks differ; the phone test below checks it on the service.

**Rejected:**

- **`trusted_proxies: 2`:** the second entry from the end also changed between requests in the plain run, so (a) fails.
- **`trusted_proxies: 3`:** stable in both runs, but it rests on Render keeping exactly two hops in front of the API, which Render does not document; a change of hops would make the key a proxy's address (one budget for everyone) or a forgeable entry, silently.
- **True-Client-IP:** present in every request, but Cloudflare documents it as an Enterprise header whose value can be spoofed in some setups, and a forged True-Client-IP alone was not refused (200). CF-Connecting-IP has neither problem.

The diagnostic is turned off (`api.forwarded_diagnostic: false`); its code stays, off by default, for a later change of host.

## Alternatives

- **Hugging Face Spaces, as planned.** Outbound port 5432 is blocked, and a Docker Space needs a paid plan.
- **Vercel (Hobby).** No card and terms that match the non-commercial rule, but serverless instances each keep their own copy of ADR 0049's in-process cache and rate limit, every cold instance reaches Neon, and the default dependencies would exceed its 500 MB bundle limit. It is the fallback if Render's terms or limits change.
- **Koyeb.** It needs a payment card.

## Consequences

- The free instance spins down after 15 minutes without traffic and takes about a minute to wake; the dashboard's client waits out the loading page (ADR 0063) within its 3-minute budget (ADR 0054).
- Without a payment method, a free service is suspended for the rest of the month once its bandwidth is used up. Render's terms describe free instances as meant for hobby and test projects and grant use for the owner's own use only, which fits a non-commercial research demo (Section 2).
- `api.trusted_proxies` keeps its value until decision 3's measurement; its comment says so.
  Measured 2026-11-10: see below.
- The README's deployment link and Step 15's public URL point to Render once the API is deployed.

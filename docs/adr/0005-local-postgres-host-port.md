# 0005. Map the local Postgres container to host port 15432

- Date: 2026-09-23
- Status: Accepted (requested by Aakrisht on 2026-09-23)

## Context

`docker compose up -d` failed on Aakrisht's machine with "ports are not available: exposing port TCP 127.0.0.1:5432 ... bind: An attempt was made to access a socket in a way forbidden by its access permissions". Two causes were possible: another process holding the port, or a Windows excluded port range (Hyper-V and WSL reserve ranges that nothing else may bind).

Checks on 2026-09-23:

- **Something is listening on 5432.** A native PostgreSQL server holds `0.0.0.0:5432` and `[::]:5432`. Its process belongs to the Windows service `postgresql-x64-18` (PostgreSQL 18, automatic start). A second native server, `postgresql-x64-16`, is also installed and running.
- **5432 is not excluded.** `netsh interface ipv4 show excludedportrange protocol=tcp` (and the IPv6 equivalent) lists 11 ranges, all between 50000 and 63877.

So the bind failed because the port was taken. Windows reports that as an access-permission error when the holder binds the port exclusively.

## Decision

Publish the container's port 5432 on host port **15432**, still bound to 127.0.0.1 only (`"127.0.0.1:15432:5432"`). 15432 had no listener and fell outside all 22 excluded ranges (IPv4 and IPv6) when checked. It is also below Windows' default dynamic port range (49152 to 65535), where Hyper-V and WSL place their reservations, so a reboot should not move an exclusion onto it.

The local connection URL becomes `postgresql+psycopg://trialpulse:trialpulse@localhost:15432/trialpulse`. Inside the container Postgres still listens on 5432, so the healthcheck is unchanged. `docker-compose.yml` and `.env.example` are the only files that carry the local port.

## Alternatives

- **Stop or reconfigure the native PostgreSQL 18 service.** It frees 5432, but it changes system services on Aakrisht's machine that other work may rely on, and it would break again if the service is restarted.
- **Keep 5432 and let each developer override it.** It works, but the default would fail on this machine, and a quickstart that fails by default is worse than an unusual port.
- **Pick a port in the 49152 to 65535 range.** Hyper-V and WSL reserve blocks there dynamically, so a port that is free today can become excluded after a reboot.

## Consequences

- Anything that connects to the local database (Alembic from Step 3, local scoring and API runs) uses port 15432 through `DATABASE_URL`. The port is not hard-coded anywhere else.
- A native Postgres on 5432 and the TrialPulse container can run side by side.
- Neon (cloud) is unaffected, because its URL comes from `NEON_DATABASE_URL`.

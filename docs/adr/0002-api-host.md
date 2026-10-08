# ADR 0002: API host

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Fly.io

Fly.io, one app per environment (`musicdata-dev` scales to zero, `musicdata-prod` stays on). No request timeout, so the queue page and long aggregations never hit the 30-second wall that forced the old workarounds. About $6.58 a month for both.

## Amendment (2026-10-08): prod scales to zero

Matt chose to run prod like dev: `shared-cpu-1x`, 256 MB, `min_machines_running = 0`. Both
apps stop when idle and bill only for running time, so the steady cost is near $0. The
trade-off is a cold start of a few seconds on the first request after idle. Switch back to
the always-on prod above by editing `fly.prod.toml` if that cold start bothers the queue page.

Alternatives: Render ($7 prod + free dev with cold starts), Railway Hobby ($5 incl. usage), a VPS.

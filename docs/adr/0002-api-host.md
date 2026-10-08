# ADR 0002: API host

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Fly.io

Fly.io, one app per environment (`musicdata-dev` scales to zero, `musicdata-prod` stays on). No request timeout, so the queue page and long aggregations never hit the 30-second wall that forced the old workarounds. About $6.58 a month for both.

Alternatives: Render ($7 prod + free dev with cold starts), Railway Hobby ($5 incl. usage), a VPS.

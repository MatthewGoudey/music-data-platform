# ADR 0005: Local database for tests

- Status: Accepted
- Date: 2026-10-07
- Decision: Neon dev branch

`docker compose up -d db` gives a local Postgres 17; tests run against it with `DATABASE_URL` set. CI uses a service container the same way. A Neon dev branch is the fallback if Docker Desktop on Windows is painful. Decide after the first `uv run pytest` on the laptop.

## Outcome (2026-10-07)

The laptop has no Docker, so local database tests run against a Neon dev branch: set
`DATABASE_URL` to the dev branch's pooled connection string and run `uv run pytest -q`.
Without `DATABASE_URL`, the 5 database tests skip and the other 72 pass. CI keeps its
Postgres service container. Revisit if Docker Desktop is installed later.

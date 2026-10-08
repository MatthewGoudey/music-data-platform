# ADR 0005: Local database for tests

- Status: Proposed
- Date: 2026-10-07
- Decision: Docker Postgres

`docker compose up -d db` gives a local Postgres 17; tests run against it with `DATABASE_URL` set. CI uses a service container the same way. A Neon dev branch is the fallback if Docker Desktop on Windows is painful. Decide after the first `uv run pytest` on the laptop.

# music-data-platform

A personal listening tracker. ListenBrainz in, a queue out.

It holds every album you might want to hear (from any list), knows what you have
actually heard (from ListenBrainz), tells you what to hear next, and shows your
progress. One user, two environments (dev and prod), nothing on the laptop.
The full plan — goals, data model, edge rules, phases — is `docs/REDESIGN_PLAN.md`;
decisions are one file each in `docs/adr/`.

## What exists right now (Phase 1)

- `musicdata` CLI with five jobs — `ingest`, `resolve`, `derive`, `shows`, `dq` — that are
  no-ops but record a `pipeline_run` row each. `musicdata dq --fail` forces a failure to
  prove the alert path.
- FastAPI app with `GET /health` and `GET /ops/status` (last run per job + latest checks).
- `musicdata.identity.norm_key` / `album_key`: the one normalizer, with a golden-file test.
- Alembic migrations (plain SQL), Dockerfile, compose file for a local Postgres,
  Fly configs for dev and prod, GitHub workflows for CI, the nightly sync, deploys,
  backfills, and the scraper spike.

Phase 1 is done when: a tag deploys to prod, the nightly sync runs in both environments,
and a forced failure sends a notification.

## Local setup (laptop)

Prerequisites: [uv](https://docs.astral.sh/uv/), git, and Docker Desktop (for the local
Postgres; a Neon branch works instead if Docker is a pain).

```powershell
cd C:\Users\MattG\projects\music-data-platform
uv sync                       # creates .venv from uv.lock, installs everything incl. dev tools
uv run pre-commit install     # ruff + gitleaks + hygiene hooks on every commit
copy .env.example .env        # then edit .env

docker compose up -d db       # local Postgres 17 on localhost:5432
uv run alembic upgrade head   # creates pipeline_run and dq_result

uv run pytest -q              # unit tests always; integration tests when DATABASE_URL is set
uv run musicdata --plain-logs ingest     # a no-op run; look at the row it wrote:
uv run musicdata --plain-logs dq --fail  # a forced failure (exit code 1)

uv run uvicorn musicdata.api.app:app --reload
# http://127.0.0.1:8000/health
# http://127.0.0.1:8000/docs          (generated OpenAPI)
# curl -H "Authorization: Bearer dev-token-change-me" http://127.0.0.1:8000/ops/status
```

`uv run musicdata --env dev ingest` loads `.env.dev` on top of `.env` — a convenient way to
point a job at the dev database from the laptop without changing `.env`.

## Bootstrapping the cloud (one-time, ~45 minutes)

Do these in order. Nothing here touches the old pipeline.

1. **GitHub.** Create the repository (public is the recommendation; see ADR 0001), then
   `git init`, commit, and push `main`. In *Settings → Environments* create `dev` and `prod`.
2. **Neon.** Create two projects on the Free plan: `musicdata-dev` and `musicdata-prod`.
   Copy each project's **pooled** connection string (it contains `-pooler`) and keep
   `sslmode=require`.
3. **Secrets and variables**, per GitHub Environment:
   - secrets: `DATABASE_URL`, `FLY_API_TOKEN` (step 4), `NTFY_TOPIC` (step 5),
     `LISTENBRAINZ_TOKEN`, `LASTFM_API_KEY`, `TICKETMASTER_API_KEY` (the last three can
     wait for Phase 2/3)
   - variables: `LISTENBRAINZ_USER`, `MUSICBRAINZ_USER_AGENT`
     (format: `musicdata/0.1 (https://github.com/<you>/music-data-platform)`)
4. **Fly.** Install `flyctl`, `fly auth login`, then from the repo root:
   `fly launch --no-deploy --config fly.dev.toml` and the same for `fly.prod.toml`
   (accept the app names). Set each app's secrets:
   `fly secrets set --config fly.dev.toml DATABASE_URL=... API_TOKEN=... QUEUE_PAGE_TOKEN=...`
   and again for prod with prod values. Create a deploy token with `fly tokens create deploy`
   and store it as `FLY_API_TOKEN` in both GitHub Environments.
5. **Alerts.** Install the ntfy app on your phone, subscribe to a topic with a random
   suffix (e.g. `musicdata-7f3k9`), and store the topic name as `NTFY_TOPIC`.
   GitHub also emails you when a scheduled workflow fails.
6. **First deploy.** Push to `main` → the `deploy-api` workflow migrates dev and deploys the
   dev API. Open `https://musicdata-dev.fly.dev/health`. Then `git tag v0.1.0 && git push --tags`
   → the same for prod.
7. **Prove the plumbing.** Run `daily-sync` from the Actions tab for `dev`, then
   `spike-scrapers`. To test alerting, run `backfill` with job `dq` and args `--fail`:
   you should get a push and an email, and `/ops/status` should show `healthy: false`.

## How a change reaches prod

Branch → pull request (CI: ruff, migrations, tests, a CLI smoke run) → merge to `main`
(deploys dev; tomorrow's dev sync uses it) → when dev is green, `git tag vX.Y.Z` → prod
deploys and prod's sync runs that tag from then on. A tag is the only road to prod.

## Layout

```
src/musicdata/
  config.py  log.py  db.py        settings, JSON logs, one pool factory with retry
  identity/normalize.py           norm_key, album_key, split_featured (+ golden file)
  jobs/cli.py  runs.py  stubs.py  the five commands; pipeline_run bookkeeping; Phase 1 no-ops
  api/app.py                      /health, /ops/status, bearer auth
migrations/                       Alembic, plain SQL per revision
tests/unit  tests/integration  tests/dq
.github/workflows/                ci, daily-sync, deploy-api, backfill, spike-scrapers
docs/adr/                         one decision per file
seeds/                            CSV/YAML seed data (lists, the atlas, venues) — Phase 3/4
```

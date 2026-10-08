# CLAUDE.md — music-data-platform

## What this is
A personal listening tracker for Matt: ListenBrainz in, a queue out. It holds every album he
might want to hear (from any list), knows what he has actually heard (from ListenBrainz),
tells him what to hear next, and shows progress. Two cloud environments (dev, prod); nothing
runs on the laptop except development and tests. The old pipeline in
`..\music-pipeline` is today's prod and stays untouched until this system passes its Phase 2 gate.

Read first: `docs/REDESIGN_PLAN.md` (sections "What this is for", "Data model", "Edge rules",
"Phases and gates") and `docs/adr/` (one decision per file; recommendations are the defaults).

## Working conventions
- Always run commands through uv: `uv run <cmd>`; always run `uv lock` after editing `pyproject.toml`.
- Always run `uv run ruff check . ; uv run ruff format . ; uv run pytest -q` before committing.
- Always keep identity logic in `src/musicdata/identity/normalize.py`, and always change
  `tests/unit/golden/normalize.json` in the same commit when the key semantics change.
- Always write migrations as plain SQL inside `op.execute` in `migrations/versions/`; one concern per revision.
- Always wrap a job body with `musicdata.jobs.runs.record` so every run leaves a `pipeline_run` row.
- Always keep secrets in `.env` / `.env.<env>` (gitignored), GitHub Environment secrets, or `fly secrets`;
  always treat `.env.example` as the authoritative list of settings.
- Always ask Matt before creating an account, choosing anything that costs money, or deleting data.
- Always stop and say exactly what to do when a step needs a human (browser login, phone app, card on file).
- Always write small commits whose message says why; always end a block with a short report.
- Always use positive, active phrasing in any prompt, README or doc written for AI ingestion.

## Current state (2026-10-07)
Phase 1 scaffold committed on `main` (`26f9146`): identity normalizer with golden file, five
no-op jobs that record `pipeline_run`, FastAPI `/health` and `/ops/status`, Alembic migration
0001, Dockerfile, compose, Fly configs, workflows, 14 ADRs. In the authoring environment:
migrations apply, 77 tests pass, `musicdata dq --fail` exits 1 and shows `healthy: false`.

Local state on this laptop: no `.venv` yet, pre-commit hook not installed, the six guarded
files still sit in `_setup\` (see `_setup\README.md`).

## Phase 1 checklist — complete in order, report after each block

### Block A — laptop
1. Move the guarded files into place and remove the leftovers:
   `Move-Item _setup\.pre-commit-config.yaml .`
   `New-Item -ItemType Directory -Force .github\workflows | Out-Null`
   `Move-Item _setup\.github\workflows\*.yml .github\workflows\`
   `Remove-Item -Recurse -Force _setup`
   `Remove-Item .github\workflows\_probe.yml`
2. `uv sync` then `uv run pytest -q` — expect `72 passed, 5 skipped`.
3. `uv run pre-commit install`, then commit: "Move workflows and pre-commit config into place".
4. If `docker --version` works: `docker compose up -d db`, `Copy-Item .env.example .env`,
   `uv run alembic upgrade head`, `uv run pytest -q` (expect `77 passed`),
   `uv run musicdata --plain-logs ingest` (expect `job ok`),
   `uv run musicdata --plain-logs dq --fail` (expect exit code 1). Without Docker, skip and
   note it in `docs/adr/0005-local-database.md` as "Neon dev branch".

### Block B — GitHub (needs: repo name and visibility from Matt; ADR 0001 recommends public)
5. `gh auth status` (run `gh auth login` if needed), then
   `gh repo create <name> --<public|private> --source . --remote origin --push`.
6. Create the environments: `gh api -X PUT repos/{owner}/{repo}/environments/dev` and the same for `prod`.
7. Set variables for both environments: `gh variable set LISTENBRAINZ_USER --env dev --body MatthewG606`
   and `gh variable set MUSICBRAINZ_USER_AGENT --env dev --body "musicdata/0.1 (https://github.com/<owner>/<repo>)"`.

### Block C — Neon (needs: Matt logged in at console.neon.tech)
8. Ask Matt to create two Free-plan projects, `musicdata-dev` and `musicdata-prod`, and paste
   each project's POOLED connection string (contains `-pooler`, keep `sslmode=require`).
   Alternative he may prefer: `npm i -g neonctl`, `neonctl auth`, `neonctl projects create --name musicdata-dev`.
9. `gh secret set DATABASE_URL --env dev --body "<dev url>"`; same for prod.
10. Verify and migrate dev from the laptop once: `$env:DATABASE_URL="<dev url>"; uv run alembic upgrade head; uv run pytest -q` (expect `77 passed`).

### Block D — Fly (needs: Matt logged in via `fly auth login`)
11. Install flyctl (`winget install -e --id Fly.flyctl` or the PowerShell installer from fly.io/docs), then `fly auth login`.
12. `fly launch --no-deploy --copy-config --config fly.dev.toml --name musicdata-dev --region ord` and the
    same for `fly.prod.toml` / `musicdata-prod`. If a name is taken, choose `musicdata-dev-<suffix>` and update `app =` in that toml.
13. Generate tokens with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` (one per value), then
    `fly secrets set --config fly.dev.toml DATABASE_URL="<dev url>" API_TOKEN="<t1>" QUEUE_PAGE_TOKEN="<t2>"`;
    same for prod with prod values. Save each environment's `API_TOKEN` in `.env.dev` / `.env.prod` (gitignored) and tell Matt where.
14. `fly tokens create deploy -x 8760h --config fly.dev.toml` → `gh secret set FLY_API_TOKEN --env dev --body "<token>"`; same for prod.

### Block E — alerts (needs: Matt's phone)
15. Ask Matt to install the ntfy app and subscribe to `musicdata-<random suffix>`; then
    `gh secret set NTFY_TOPIC --env dev --body "<topic>"` and the same for prod.

### Block F — the Phase 1 gate
16. `git push` → `gh run watch` the `deploy-api` run → `curl https://musicdata-dev.fly.dev/health` shows `"status":"ok"` and `"database":"up"`.
17. `git tag v0.1.0; git push --tags` → prod deploys → `/health` on `musicdata-prod.fly.dev` is ok.
18. `gh workflow run daily-sync -f env=dev` → watch → `curl -H "Authorization: Bearer <dev API_TOKEN>" https://musicdata-dev.fly.dev/ops/status` lists all five jobs with `"status":"ok"`.
19. `gh workflow run spike-scrapers` → read the do312 status code and event count → record the result and the decision in `docs/adr/0006-do312-fallback.md`.
20. `gh workflow run backfill -f env=dev -f job=dq -f args=--fail` → confirm the ntfy push and the GitHub email arrive and `/ops/status` shows `"healthy":false`; then `gh workflow run backfill -f env=dev -f job=dq` to clear it.
21. Final report: a table of steps passed, steps that needed Matt, and anything that diverged from `docs/REDESIGN_PLAN.md`. Phase 1 is complete when 16–20 all pass; Phase 2 (ListenBrainz ingest, identity, sessions) starts next.

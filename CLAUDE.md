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

## Environments (live since 2026-10-08)
| | dev | prod |
| --- | --- | --- |
| API | https://musicdata-dev.fly.dev (`fly.dev.toml`) | https://musicdata-prod.fly.dev (`fly.prod.toml`) |
| Database | Neon `musicdata-dev`, pooled URL in `.env.dev` | Neon `musicdata-prod`, pooled URL in `.env.prod` |
| Deploys | every push to `main` | every `v*` tag |
| Daily sync | `daily-sync.yml` runs `main` | `daily-sync.yml` runs the latest `v*` tag |

- GitHub: `MatthewGoudey/music-data-platform` (public), environments `dev` and `prod`.
- Both Fly apps are `shared-cpu-1x`, 256 MB, and scale to zero (ADR 0002 amendment).
- `API_TOKEN` and `QUEUE_PAGE_TOKEN` per environment live in `.env.dev` / `.env.prod`.
- `POST /query` runs as the read-only role `musicdata_ro` (`DATABASE_URL_READONLY` in
  `.env.<env>` and `fly secrets`); `scripts/readonly_role.py <env>` creates it or rotates its password.
- Failure alerts: GitHub email plus an ntfy push to the topic in `NTFY_TOPIC` (`daily-sync`, `backfill`, `full-load`).
- Laptop tools: uv, `gh` (`C:\Program Files\GitHub CLI\gh.exe`), flyctl (`%USERPROFILE%\.fly\bin\flyctl.exe`).
  No Docker: integration tests run against Neon dev with `--env dev` or `DATABASE_URL` set (ADR 0005).
- Run any job against an environment from the laptop: `uv run musicdata --env dev --plain-logs <job>`.

## Current state (2026-10-08, Phase 2 gate in progress)
Phase 2 code (Blocks A–F) is on `main`, tagged `v0.2.1`, and deployed to dev and prod.
Dev passes all nine acceptance checks on the full history (245,707 listens, equal to
ListenBrainz; 16,695 resolved albums; 7,022 album sessions). Prod is loading through
the `full-load` workflow (ingest → resolve ×2 → derive → dq, about 7 hours); when its dq
job passes, Phase 2 gate step 18 is done.

Load or reload an environment with `gh workflow run full-load -f env=<dev|prod>`; every
step resumes, so a re-run is safe. `ingest --rekey` re-applies identity rules to stored
listens after an identity fix (it repaired 3,484 comma-joined listens in dev).

Open items that need Matt:
- Paste `docs/claude-project-instructions.md` into the claude.ai Project (gate step 19).
- Phase 0: disable the old Task Scheduler task, rotate the old Neon password and Render
  `API_SECRET`, export the seed CSVs (`seeds/manual_tracklists.csv` feeds resolve).

Notes for the next session:
- `daily-sync` commits `docs/status/heartbeat.txt` monthly; run `git pull` before pushing.
- GitHub ran the first scheduled `daily-sync` 7 hours late (15:36 for the 08:17 slot);
  schedules are best-effort, and `/ingest/catch-up` covers freshness between runs.
- ListenBrainz drops large pages deep in the history; the client shrinks the page size,
  and `ingest --full` resumes below the oldest stored listen.
- Integration tests run against Neon dev; they use made-up MBIDs and clean up their rows.

## Phase 2 checklist — listening core; complete in order, report after each block
Gate: the acceptance suite is green in dev, the dev listen count is within 0.5% of
ListenBrainz `listen-count`, the version is tagged and live in prod, and the old API is retired
for listening questions.

### Block A — identity and listen schema
1. Migration 0002: `artist`, `artist_alias`, `release_group`, `release_group_alias`.
2. Migration 0003: `listen` with `artist_id` NOT NULL and UNIQUE (`listened_at`, `artist_id`, `norm_title`).
3. `title_key` for track titles in `normalize.py`, with golden cases.
4. Integration tests for the constraints; apply to dev.

### Block B — ListenBrainz ingest
5. `clients/listenbrainz.py`: page backwards with `max_ts`, honour the `X-RateLimit-*` headers.
6. `ingest/`: identity assignment at ingest (MBIDs first, `norm_key` fallback, aliases recorded),
   batched upserts, a 3-day overlap below the watermark so late listens land.
7. `musicdata ingest` replaces the no-op; `musicdata ingest --full` loads the whole history.
8. Full load into dev; compare against `listen-count`.

### Block C — resolve
9. `clients/musicbrainz.py` at 1 request/second with the configured User-Agent.
10. `resolve/`: release-group metadata (type, secondary types, first year), the canonical
    release by the edition rule, and its tracklist into `release_group_tracklist` / `release_group_track`.
11. Unmapped tail: MusicBrainz search; Last.fm only once `LASTFM_API_KEY` exists. Manual overrides from seeds.
    Merge an unmapped release group into a mapped one of the same artist and key: the one whose
    tracklist overlaps the listened titles most wins; ties go to review. (Ingest leaves these
    apart on purpose, e.g. The Fall's "Live at the Witch Trials" under two MBIDs plus one unmapped.)
12. Resumable by `--limit`; the first full pass runs as `backfill` chunks.

### Block D — derive
13. `album_session` by the edge rules (30-minute gap; full ≥ 0.8; partial ≥ 0.25 with ≥ 3 tracks).
14. `artist_stat`, `release_group_stat`, rebuilt after each ingest. Manual sessions table support.

### Block E — acceptance checks
15. `tests/dq` queries with thresholds from the plan; `musicdata dq` writes `dq_result` and exits 1 on a failure.

### Block F — API
16. `/listens/*`, `/artists`, `/albums`, `/sessions`, `POST /sessions`, `POST /query` (read-only role, 10 s timeout, row cap).
17. A first `/queue` page: recent sessions plus the verdict form.

### Block G — the Phase 2 gate
18. Dev green on the gate criteria → tag → prod full load and green.
19. Matt switches his listening questions to the new API; final report.

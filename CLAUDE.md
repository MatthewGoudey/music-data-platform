# CLAUDE.md — music-data-platform

## What this is
A personal listening tracker for Matt: ListenBrainz in, a queue out. It holds every album he
might want to hear (from any list), knows what he has actually heard (from ListenBrainz),
tells him what to hear next, and shows progress. Two cloud environments (dev, prod); nothing
runs on the laptop except development and tests. The old pipeline in
`..\music-pipeline` is today's prod and stays untouched until this system passes its Phase 2 gate.

Read first: `docs/REDESIGN_PLAN.md` (sections "What this is for", "Data model", "Edge rules",
"Phases and gates") and `docs/adr/` (one decision per file; recommendations are the defaults).

**The queue, lists and the atlas: always read `docs/QUEUE_SPEC.md` first and follow it exactly.**
It is the authoritative spec and overrides the plan and the ADRs where they differ. Its core rule:
the queue's new albums always come from `list_entry` (the lists in `seeds/`); listening history
only marks entries heard / started / unheard, fills the revisit slice, and adds a small affinity
boost. Always ask Matt before changing a rule in that spec, and log the change at its bottom.

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

## Current state (2026-10-09, Phase 4 Block A done, waiting for Matt)
`v0.2.3` is deployed to prod (shows on a scheduled Fly machine, albums-only `/queue`); `main`
adds Phase 4 Block A (lists and atlas), deployed to dev. Earlier: `v0.2.2` on dev and prod. Dev passes all acceptance checks on the full history
(listen count equal to ListenBrainz, 97% of listens on an album with a tracklist, 7,196
album sessions). v0.2.2 added one-album-one-group rules (album-artist keys, local merges,
stray-track redirects; ADR 0015 amendment) and started Phase 3: shows from Oh My Rockness
and Ticketmaster (ADR 0006 amendment, ADR 0016), `GET /shows?match=true`, interests, and
the E1–E3 checks. Prod is running `full-load` in repair mode; when its dq passes, Phase 2
gate step 18 is done.

Load or reload an environment with `gh workflow run full-load -f env=<dev|prod>`
(`-f mode=repair` re-applies identity rules: rekey, retry unresolved, full derive).
`musicdata shows --sweep` reads all ~600 Oh My Rockness venues once (~20 min); nightly
runs read active venues plus a rotating 85.

Current priority (Matt, 2026-10-09): the queue. Follow `docs/QUEUE_SPEC.md` section 0:
Step 0 cleanup, then Phase 4 Blocks A–G. Shows and verdict work waits until the Phase 4 gate.

Open items that need Matt:
- Done: `docs/claude-project-instructions.md` pasted into the claude.ai Project (gate step 19).
- Keep the old pipeline and its Task Scheduler task running until Matt says otherwise.
- Rotating the old Neon password and Render `API_SECRET` waits for Matt.
- The old-database exports (Claude canon, venues, manual tracklists): analysed read-only in
  Block A; what to export waits for Matt's answer to the Block A report.
- Three Acclaimed Music rows collapse onto another entry's keys (Weezer *Blue*/*Green*,
  Crystal Castles *I*/*II*, Elvis 1956 vs the '68 NBC-TV Special); the fix touches the key
  rule, so it waits for Matt.

Notes for the next session:
- Never migrate a shared database (dev, prod) ahead of `main`: a cloud job running the
  older code fails on the unknown revision (it broke a dev derive on 2026-10-08).
- A key-rule change needs golden cases for every key it touches; a change to the edition
  rule moved `title_key` too and the rekey then stored 81 duplicates (now fixed and checked).
- Patches containing backslashes go through Edit/Write or PowerShell, never bash heredocs,
  which collapse `\b` and `\w` on this machine.
- Saved web pages used as fixtures are scrubbed of third-party scripts and keys
  (gitleaks blocks them otherwise).
- `daily-sync` commits `docs/status/heartbeat.txt` monthly; run `git pull` before pushing.
- GitHub ran the first scheduled `daily-sync` 7 hours late; schedules are best-effort, and
  `/ingest/catch-up` covers freshness between runs.
- ListenBrainz drops large pages deep in the history and has short outages; the client
  shrinks pages, waits out outages, and `ingest --full` resumes below the oldest listen.
- Integration tests run against Neon dev; they use made-up MBIDs and coordinates far from
  Chicago, and clean up their rows.

## Phase 4 checklist — lists, the atlas and the queue (spec: `docs/QUEUE_SPEC.md`)
Gate: dev green on checks L1–L5 and tests Q1–Q5, the version tagged and live in prod with
lists loaded and resolved, and Matt using the `/queue` page for a week.

The seed data is in the working tree, uncommitted (added 2026-10-09): `seeds/lists/_lists.csv`
registers five lists — `v_atlas` (2,942, `seeds/atlas/albums.csv` + `album_notes.csv`),
`rolling_stone_500` (500), `1001_albums` (978), `aoty_2007_2024` (899), `acclaimed_music_3000`
(3,000) — and `seeds/atlas/` holds lanes, paths, scenes, labels, tags and artists. The repo is
public: commit `seeds/lists/` only the way Matt chooses in Step 0. The `/queue` page carries albums only: Step 0 takes the shows
section and the verdict cards off it, and Block E puts "Up next" there. Verdicts are dormant.

- [x] Step 0 — shows section and verdict cards off `/queue` (read-only recent sessions until
      Block E); v0.2.3 in prod; `seeds/lists/` gitignored and loaded from the laptop
      (`musicdata --env <env> lists load`).
- [x] Block A — `seeds/atlas/` committed; migration 0011 (adds `list.file_rows` so L1 runs in
      the cloud); `musicdata lists load` (idempotent); L1 green in dev with all five lists
      loaded (prod loads at the gate). Old-database analysis done (read-only); exporting the
      Claude canon, venues and manual tracklists and registering `claude_canon` wait for
      Matt's decision on the Block A report.
- [ ] Block B — `musicdata lists resolve` (known release groups first, then MusicBrainz search);
      backfill passes in dev; checks L2–L3; report resolved / ambiguous / unresolved per list.
- [ ] Block C — `list_entry_status`, `GET /lists`, `GET /gaps`; checks L4–L5; report atlas coverage.
- [ ] Block D — migrations 0012–0013; `src/musicdata/queue/`; `GET /next`; tests Q1–Q5;
      report the first `default` and `home-genre` queues with their why lines; wait for Matt.
- [ ] Block E — Up next, profile switcher, shuffle, pin, bump, snooze, mark played, not for me,
      progress strip.
- [ ] Block F — Tag control on cards, `POST /tags/{name}/apply` and `/remove`, `POST /tag-now-playing`.
- [ ] Block G — the gate; add `/next`, `/gaps`, `/lists` to `docs/claude-project-instructions.md`
      and remove its "Record a verdict" row.

## Phase 3 checklist — Chicago shows
Gate (plan): E1 and E3 at 0, E2 ≥ 95%, shows endpoints in prod.

- [x] Schema (migration 0009), Oh My Rockness and Ticketmaster sources, lineup parsing
      with a golden file, artist resolution, `GET /shows` with match score, interests,
      E1–E3 checks.
- [ ] First full sweep in prod: `gh workflow run backfill -f env=prod -f job=shows -f args=--sweep`.
- [ ] Venue seed with travel times once `seeds/venues.csv` exists (ADR 0008).
- [ ] Festivals from `seeds/festivals/*.csv`.
- [ ] Presales (`presales=true`) from Ticketmaster `sales.presales`.
- [ ] A shows section on the `/queue` page.

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

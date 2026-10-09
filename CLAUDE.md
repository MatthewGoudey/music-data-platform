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

## Current state (2026-10-09, Phase 4 live in prod; Matt's week on the page has begun)
`v0.3.1` is in prod: lists and the atlas, `/next`, the Up next page with Shuffle, Undo,
tags and Check listens. Prod lists were settled by `lists copy-resolutions --source dev`
(22,156 matched), prod ran `full-load -f mode=repair` with the reported-album rule (At
Folsom Prison: 12 full sessions), and every prod check passes, L1–L5 included.

On `main`, dev only (spec v7 trial, waiting for Matt): "Recently finished" and "Shows you
might like" sections on the page, and venue travel times by CTA and on foot (migration
0016, `musicdata venues travel`, ADR 0008 amendment; dev venues timed). Prod gets them as
v0.3.2 only if Matt keeps the sections.

Identity change (ADR 0015 amendment 2026-10-09, migration 0015): listens go home to the
album the player reported (`listen.reported_key`, `derive/reported.py`), and sessions match
tracks loosely (`loose_title_keys`).

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
- Old-database exports done (2026-10-09): `claude_canon` and the 40 hand-verified track
  counts. Venues (432, with Google travel times) stay in the old database for the venue
  work after the gate. `GOOGLE_ROUTES_API_KEY` is in `.env.dev` / `.env.prod` for that work.
- A fresh BestEverAlbums download converts with `scripts/convert_besteveralbums.py`.

Notes for the next session:
- Never migrate a shared database (dev, prod) ahead of `main`: a cloud job running the
  older code fails on the unknown revision (it broke a dev derive on 2026-10-08).
- After `ingest --rekey`, always run resolve with the unmapped tail and retries before
  derive (`full-load -f mode=repair` does all three): a rekey alone re-creates unmapped
  duplicates ("DAMN. COLLECTORS EDITION.") that resolve had merged into the real album.
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
- [x] Block B — `musicdata lists resolve` (known release groups first, then MusicBrainz search);
      backfill passes in dev; checks L2–L3; report resolved / ambiguous / unresolved per list.
      Done 2026-10-09 (L3 bar for claude_canon 80%, spec v6). Built with `claude_canon` (weight 0.5), `besteveralbums_overall` (10,000),
      year-marked keys and `seeds/manual_tracklists.csv` (spec v3). Dev backfill: chained
      `gh workflow run backfill -f env=dev -f job=lists -f args="resolve --limit 5000"`.
- [x] Block C — `list_entry_status` (migration 0014, after 0012–0013 because it reads
      queue_state), `GET /lists`, `GET /gaps?by=list|lane|zone`; checks L4–L5.
- [x] Block D — migrations 0012–0013; `src/musicdata/queue/`; `GET /next` with Shuffle from
      the top 200 and `exclude` (spec v4); tests Q1–Q5. First queues reported to Matt.
- [x] Block E — the Up next page (`api/static/queue.html`, `GET /queue/data`,
      `POST /queue/{id}/{action}`), Undo on played / hide / snooze (spec v5), progress strip.
- [x] Block F — Tag control, `GET /tags`, `POST /tags/{name}/apply` and `/remove`,
      `POST /tag-now-playing`.
- [ ] Block G — the gate. Done: v0.3.1 tagged and live in prod, lists loaded and resolved,
      prod checks green, `docs/claude-project-instructions.md` updated (Matt pastes it into
      the Project). Left: Matt uses the `/queue` page for a week (from 2026-10-09).

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

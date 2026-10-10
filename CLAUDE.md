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
the queue's new albums always come from `list_entry` (the lists in `seeds/`, plus the generated
lists of `docs/graph/COMPANION_SPEC.md`, which only the profiles that name them read); listening history
only marks entries heard / started / unheard, fills the revisit slice, and adds a small affinity
boost. Always ask Matt before changing a rule in that spec, and log the change at its bottom.

**The music graph: always read `docs/graph/GRAPH_SPEC.md` and `docs/graph/edge-vocabulary.md`
first and follow them exactly.** The vocabulary says what a claim is; the spec says how the graph
is built. Their core rule: the graph stores claims with a source and verbatim evidence, the
checks decide whether the source says it, and Matt answers only reading questions.
**What the graph does for Matt — queue effects, album and hub pages, liner notes and deep dives —
is `docs/graph/COMPANION_SPEC.md` (Phase 6); always read it first for that work.** Graph work runs
in dev until Matt gives the go for prod (Phase 6 Block B). Always ask Matt before changing a rule in
any of these files, and log the change at its bottom.

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

`v0.3.2` (same day) adds Recently finished, Presales & on-sales and Shows you might like
to the page (spec v7–v8), same-night show twins merged in `/shows`, and venue travel times
by CTA and on foot (migration 0016, `musicdata venues travel`, ADR 0008 amendment); dev and
prod venues are timed, and the nightly sync times new ones.

Later on 2026-10-09 (v0.3.3–v0.3.7, all in prod, checks green): "Not on sale yet" section
(announced shows whose public sale is ahead; Ticketmaster read 365 days ahead), Shuffle
re-draws every card but pins (spec v9–v11), the edition rule skips parts of split sets
(Blonde on Blonde; `musicdata recheck-tracklists`), a group given an MBID after being marked
unresolved resolves at once (Modern Sounds), and derive folds listens off groups that can
never get a tracklist into their mapped namesake (`derive/fold.py`; RAM: 98 listens, 5 full).

Identity change (ADR 0015 amendment 2026-10-09, migration 0015): listens go home to the
album the player reported (`listen.reported_key`, `derive/reported.py`), and sessions match
tracks loosely (`loose_title_keys`).

Load or reload an environment with `gh workflow run full-load -f env=<dev|prod>`
(`-f mode=repair` re-applies identity rules: rekey, retry unresolved, full derive).
`musicdata shows --sweep` reads all ~600 Oh My Rockness venues once (~20 min); nightly
runs read active venues plus a rotating 85.

Current priority (Matt, 2026-10-10): Phase 6 (`docs/graph/COMPANION_SPEC.md` section 13);
Blocks A and B are done and prod is the graph's home. Reading batches P05–P07 run against prod
(`--env prod`); Block C (default scope, Firecrawl spend) waits for Matt's go.
The queue's gate week continues untouched until Phase 6 Block H. Shows, venues and verdict work
wait until the Phase 4 gate.

Open items that need Matt:
- Done: `docs/claude-project-instructions.md` pasted into the claude.ai Project (gate step 19).
- Keep the old pipeline and its Task Scheduler task running until Matt says otherwise.
- Rotating the old Neon password and Render `API_SECRET` waits for Matt.
- Old-database exports done (2026-10-09): `claude_canon` and the 40 hand-verified track
  counts. Venues (432, with Google travel times) stay in the old database for the venue
  work after the gate. `GOOGLE_ROUTES_API_KEY` is in `.env.dev` / `.env.prod` for that work.
- A fresh BestEverAlbums download converts with `scripts/convert_besteveralbums.py`.

Notes for the next session:
- Always migrate a shared database (dev, prod) only from code already on `main`: a cloud job running the
  older code fails on the unknown revision (it broke a dev derive on 2026-10-08).
- After `ingest --rekey`, always run resolve with the unmapped tail and retries before
  derive (`full-load -f mode=repair` does all three): a rekey alone re-creates unmapped
  duplicates ("DAMN. COLLECTORS EDITION.") that resolve had merged into the real album.
- A key-rule change needs golden cases for every key it touches; a change to the edition
  rule moved `title_key` too and the rekey then stored 81 duplicates (now fixed and checked).
- Always apply patches containing backslashes through Edit/Write or PowerShell; bash heredocs
  on this machine collapse `\b` and `\w`.
- Saved web pages used as fixtures are scrubbed of third-party scripts and keys
  (gitleaks blocks them otherwise).
- `daily-sync` commits `docs/status/heartbeat.txt` monthly; run `git pull` before pushing.
- GitHub ran the first scheduled `daily-sync` 7 hours late; schedules are best-effort, and
  `/ingest/catch-up` covers freshness between runs.
- ListenBrainz drops large pages deep in the history and has short outages; the client
  shrinks pages, waits out outages, and `ingest --full` resumes below the oldest listen.
- Integration tests run against Neon dev; they use made-up MBIDs and coordinates far from
  Chicago, and clean up their rows.

## Phase 6 checklist — the graph in the queue, and the album companion (spec: `docs/graph/COMPANION_SPEC.md`)
Gate: C1–C5 and G1–G8 green in prod, and Matt has used the cards, pages and one deep dive while
listening for a week. Prod runs only `v*` tags, so each block that changes code ends with a tag on
Matt's go.

- [x] Block A — graph API (Block A rows of spec section 11), the brief with `pages` and per-track
      credits, the import's qualifier upsert and the pilot's re-import, Project instructions rows.
      Done 2026-10-10 in dev (`api/routers/graph.py`, `graph/queries.py`): 1,025 MusicBrainz
      credits carry their track list after the re-import; G1–G8 green. Prod gets the code with
      the next `v*` tag.
- [x] Block B — `graph import --scope` with `rg` labels, `graph verify --pending`, the nightly
      graph workflow on prod; on Matt's go, `graph copy --source dev` and prod becomes the graph's
      home (P05–P07 then run against prod). Done 2026-10-10: v0.4.0 (Blocks A and B) in prod;
      dev graph tables 26.7 MB before the copy, prod database 298.5 MB after it; G1–G8 green in
      prod. The duplicate-entity merge rule is graph spec v10 (every merge logged in the kept
      entity's `attrs.merged`; `graph unmerge KEPT MERGED` undoes one). Until Matt's go for
      Block C, `graph-nightly.yml` runs `graph link`, `graph verify --pending` and `dq` only.
- [ ] Block C — the near-queue set and default scope in the nightly workflow, on Matt's go for the
      Firecrawl spend; report coverage, credits and database size after three nights.
      Built 2026-10-10 (v0.4.3, Matt's go): `graph near-queue` (each profile's top 300, slice
      `near_queue`), `--scope default` (opened → near-queue → heard → atlas, only albums that still
      need the step); import 400 a night, stops at 650 MB; fetch 500 credits a night, 15,000 in all.
      First prod run started by hand 2026-10-10; report after three nights (2026-10-13).
- [x] Block D — migration 0021; `graph connections` (nightly) and `graph threads` (end of
      `derive`); the brief gains `connections`.
      Done 2026-10-10 in dev (`graph/connections.py`, checks C3 and C5).
- [x] Block E — pages router (`page_or_bearer` in `deps.py`), album, hub and search pages,
      now-playing, opened-page baselines; Matt tries them on his phone.
      E1 done 2026-10-10 in dev (`pages/`, `api/routers/pages.py`; queue card titles link to album
      pages). E2 done 2026-10-10 in dev: Follow, Walk back, generated lists `following` and
      `graph_walk` with their profiles (migrations 0025-0026, `graph/generated.py`). Order is now A-B-C-D-H1-E-F-G-I; H1 (card lines, graph_affinity, three thread
      slots, migrations 0022-0023) is in dev; the album_document table (0024) and
      `/albums/{id}/documents/{kind}` hold the Boat Songs deep dive in dev.
- [x] Block F1 — migration 0022; document request and worker endpoints with leases and the
      sweep, server-side fetches, marker checks and rendering, document tabs. Done 2026-10-10 in dev
      (`worker.py`, `api/routers/documents.py`; the table is migration 0024); the dev app has
      `FIRECRAWL_API_KEY`.
- [x] Block F2 — `.claude/skills/album-companion/SKILL.md`; one deep dive for an album Matt picks,
      fact-checked by a separate agent. Done 2026-10-10 in dev: Déjà vu (document 7, requested by
      Matt from its page), 5,397 words, 11 pages fetched through the API, 16 new claims in batch D7
      (reader: 15 SUPPORTS, 1 PARTIAL), 249 passages checked, 8 rewritten. Liner notes dropped
      (companion spec change 2026-10-10).
- Block G — the worker is a Claude Code routine (`docs/graph/WORKER_ROUTINE.md`). First run done
      2026-10-10: Matt requested The Velvet Underground & Nico (document 8) and pressed Run now; the
      routine claimed it in 24 s and handed in a ready deep dive (4,645 words, 203 sentences checked,
      17 Firecrawl credits) with no terminal. Requests now fire the routine at once (needs Matt's
      ROUTINE_FIRE_URL / ROUTINE_FIRE_TOKEN on the dev app); a daily run is the safety net.
- [ ] Block G — one manual worker run in a claude.ai cloud session; on Matt's go, the scheduled task.
- [ ] Block H — migrations 0023–0025; card connection lines and page link, `graph_weight`, thread slot,
      generated-list handling, follow and walk back; the rules written into `docs/QUEUE_SPEC.md`;
      tag on Matt's go.
- [ ] Block I — sign-off: C1–C5 and G1–G8 green in prod; a week of use.

## Phase 5 checklist — the music graph (spec: `docs/graph/GRAPH_SPEC.md`)
Gate (milestone M2): the pilot slice verified (Block E) at the first-pass reader bar with G1–G8
green. Blocks F and G moved to Phase 6 (2026-10-10). The prototype (`scripts/graph/`) is ported and deleted; run T1 is in `data/graph/runs/T1/` and `tests/unit/fixtures/graph/T1/`.

- [x] Block 0 — ADR 0017 (the claims graph); `FIRECRAWL_API_KEY` and `DISCOGS_TOKEN` as optional
      settings, GitHub Environment secrets (dev, prod) and backfill workflow env. Done
      2026-10-09: `FIRECRAWL_API_KEY` and `DISCOGS_TOKEN` set in dev and prod and in `.env`.
- [x] Block A — migrations 0018–0019 (empty tables may ride along to prod with any queue fix);
      `seeds/graph/predicates.csv`; `musicdata graph seed`; integration tests; applied to dev.
      Done 2026-10-09: dev seeded (17 predicates, 54 lanes, 162 lane_parent claims, 8 lists,
      2,838 atlas albums). `predicate."symmetric"` is quoted (a reserved word).
- [x] Block B — MusicBrainz, Discogs and Wikidata client methods; `graph import`, `graph link`;
      `--slice crazy_horse` in dev; coverage report per facet (milestone M1).
      Done 2026-10-09: 169 of 181 baselined (12 no match), 3,735 claims; clients follow
      MusicBrainz redirects (a merged release group answers 301).
- [ ] Block C — `clients/firecrawl.py`, `graph fetch`, `graph facts`; one-page live test; the
      slice in dev (estimate first; ask Matt above 1,500 credits). Code done; the live test
      waits for Matt's go on Firecrawl spend.
- [x] Block D — `graph verify`, `graph batch …`, `graph report`; T1 fixtures built from
      `data/graph/`; T1 golden and negative tests pass; then `scripts/graph/` deleted; tell Matt
      the skill can switch to `musicdata graph batch`. Done 2026-10-09: G1–G8 in `dq`; the
      slice's 3,735 baseline claims verified in dev, all accepted.
- [x] Block E — the 181 albums in batches of 25; reading questions to Matt; first-pass reader
      SUPPORTS ≥ 90% per predicate type; G1–G8 green in dev (M2).
      Done 2026-10-10: P01–P07 verified (169 of 169 baselined albums; P05–P07 in prod). First-pass
      SUPPORTS 97.0% over 1,525 claims; every kind ≥ 90% except covers at 83% (25 of 30; the
      writer-versus-first-recording miss). G1–G8 green in prod. Spec v5–v12; batch status in
      `docs/graph/PILOT_STATUS.md`.
- [x] Blocks F and G — moved to Phase 6 (API → Block A; prod copy → Block B; walk → Block H).

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

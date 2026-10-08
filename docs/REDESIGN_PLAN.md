# Music Pipeline Redesign Plan

*2026-10-07, revised the same day · Matt Goudey. Snapshot of the live doc "Music Pipeline Redesign Plan" in the claude.ai "music pipeline" Project; the doc is the editable version and carries two drawings (architecture, roadmap) that this file describes in text.*

Build a greenfield, cloud-only music data platform with separate dev and prod environments for under $40/month, keeping the five things the current pipeline does well and making its known data-quality bugs impossible by construction. Foundations (repo, environments, migrations, tests, CI, alerting) come first; feature work starts only once a commit can reach prod without the laptop.

## What this is for

A personal listening tracker: it holds every album you might want to hear, from any list; knows what you have actually heard, from ListenBrainz; tells you what to hear next; and shows your progress. One user. Claude is the analytical consumer through the API; a single phone-sized queue page is the everyday one.

Three goals, and everything else is a detail of one of them:

| Goal | What it means | How it is measured |
| --- | --- | --- |
| Breadth | Wide exposure across genres and six languages (English, Spanish, French, Korean, Japanese, Chinese) | Coverage: a genre × language grid with the gaps visible |
| Canon | The published lists (Rolling Stone 500, 1001 Albums, AOTY) plus Claude-curated essentials | Progress: heard / unheard per list |
| Depth | Expertise in the home genre, the Neil Young lineage of indie rock × country, mapped in the V Album Atlas | Coverage of the atlas by lane, zone and priority, plus your notes |

The core loop:

1. Nightly, and on demand when the queue page opens: pull new listens from ListenBrainz, resolve each to an artist and a release group, recompute album sessions and stats, run the data-quality checks.
2. The queue is a query, not a stored list: unheard entries plus a revisit slice, filtered by a profile, ordered by priority. Open the page, press play; the next sync marks the album heard because a full session appeared.
3. After a full session, a two-tap form: a verdict (again / later / never), optional tag chips, an optional one-line note.

Not a recommender (Claude and the lists supply the candidates), not a player, not social.

## Constraints

Five requirements are fixed, and every later choice in this plan is checked against them.

| Requirement | What it means in practice |
| --- | --- |
| Greenfield, not migration | New repo, new schema, loaded from the raw source. Old code is reference only. The old Neon database is never copied table-for-table; the few things in it that cannot be re-derived (Claude's canon, manual tracklist overrides, venues, show interests) are exported once to CSV and committed as seed data. |
| Nothing runs on the laptop | Scheduled jobs, scrapers and the API run in the cloud. The laptop is for writing code and running tests. The Task Scheduler job is disabled on day one; ListenBrainz keeps the history server-side, so pausing ingestion loses nothing. |
| Two environments, dev → prod | Dev receives every change. Prod runs only versions that passed dev's acceptance checks and were promoted by a release tag. Separate databases, secrets and hosts. |
| $40/month total | Both environments, all vendors, including the old system while it overlaps. The recommended stack lands near $7–12 (section Environments and cost). |
| Data-quality backlog closed by design | Every P0/P1 from the April 2026 audits is either impossible under the new schema or covered by an automated check with a threshold (section Acceptance checks). |

Two scope requests ride along: cut the functional bloat (section Scope) and redesign tagging and the canonical checklist (section Tagging and canon). Foundations come before features: the repo, environments, migrations, tests, CI and alerting are Phase 1, and no pipeline code is written until a commit can reach prod without the laptop.

## Source of truth: ListenBrainz only

ListenBrainz already holds everything the Spotify export has, so the new pipeline has exactly one raw source. ListenBrainz reports 245,707 listens for MatthewG606 as of 2026-10-07. The Spotify extended-history export on disk holds 282,365 plays, of which 232,169 are 30 seconds or longer (the scrobble threshold), ending 2026-03-13.

| Year | Spotify plays ≥30 s | ListenBrainz listens | Difference |
| --- | --- | --- | --- |
| 2016 | 36 | 36 | 0 |
| 2017 | 3,472 | 3,609 | +137 |
| 2018 | 13,655 | 13,710 | +55 |
| 2019 | 36,051 | 36,066 | +15 |
| 2020 | 27,687 | 27,444 | −243 |
| 2021 | 24,932 | 24,878 | −54 |
| 2022 | 30,644 | 30,729 | +85 |
| 2023 | 26,921 | 26,977 | +56 |
| 2024 | 30,036 | 30,074 | +38 |
| 2025 | 31,252 | 31,352 | +100 |
| 2026 (Spotify ends Mar 13) | 7,483 | 20,864 | not comparable |

The 2020–21 shortfall is 297 listens out of 52,600 (0.6%), consistent with ListenBrainz de-duplicating same-timestamp plays. Keep the Spotify zip as a cold backup and do not build an importer for it.

Two facts from a 1,000-listen sample of the live feed shape the whole design:

- 88% of listens carry ListenBrainz's own MusicBrainz mapping: recording, release, release-group and artist MBIDs, plus the credited artist list. The old pipeline stored raw strings and ignored this; the new one keys everything on these IDs and falls back to a normalized local key only for the unmapped 12%.
- Full history comes from the export API (`POST /1/export/`, poll, download one zip). Daily increments come from `GET /1/user/{name}/listens` with `min_ts`, 1,000 listens per page. Each day's run also reconciles the database count against `GET /1/user/{name}/listen-count`.

## Scope: keep, cut, defer

The new pipeline keeps the five jobs the product is for (history, questions about it, Chicago shows, the lists, album sessions) and drops everything that existed only to patch the old design.

| Feature | Decision | How it changes |
| --- | --- | --- |
| Listening history ingestion | Keep | ListenBrainz only. Identity comes from LB's MBID mapping at ingest. No `raw_payload` column. A catch-up ingest runs when the queue page opens, rate-limited to once per five minutes. |
| Album tracklist resolution | Keep, simplify | The release-group MBID is known for 88% of listens, so MusicBrainz lookups are by ID, not string search. The tracklist itself is stored, not just a count. String search and Last.fm only for the unmapped tail; manual overrides as seed data. |
| Album session detection | Keep | Same rules (30-minute gap, full at ≥80%, partial at ≥25% with ≥3 tracks), one implementation, keyed on `release_group_id`, measured against the standard tracklist. Manual sessions for vinyl and shows. |
| Artist and album stats | Keep | Materialized tables refreshed by the `derive` job after each ingest, not a separate flow. |
| Chicago shows (Ticketmaster + do312) | Keep | Headliner cleaning at scrape time with tests; headliner resolved to `artist_id` at ingest. Reaching do312 from a datacenter IP is a week-1 spike. |
| Show recommendations (`match`) | Keep | Same score, `ln(listens+1) × min(tracks/5, 3) × recency`, joined on `artist_id`. |
| Show interest tracking | Keep | Small table; feeds the Live Shows profile. |
| Festival lineups | Keep, as data | One CSV per festival in `seeds/festivals/`, loaded by the same show loader. No `FESTIVALS` dict in code. |
| Lists (RS500, 1001 Albums, AOTY, the V atlas, Claude canon) | Keep, redesign | Lists are sources with rank and priority; heard/unheard is a join on IDs. See Lists, the atlas and the queue. |
| The queue and its page | New | One server-rendered, phone-sized page in the same app: the next ten for a profile, play / pin / snooze / mark played, and the post-session verdict form. The only UI. |
| Genre vocabulary | Replace | The atlas's lanes (with parent lanes), scenes, labels and tags are the controlled vocabulary for the home genre; MusicBrainz genre tags cover everything else. The old 124-genre list is retired. |
| Context tags and profiles | New | Your own small tag vocabulary on release groups, and saved profiles (filters + weights + composition) that the queue runs under. See Lists, the atlas and the queue. |
| Last.fm tags | Defer | Stored raw if fetched; nothing depends on them. |
| `/query` read-only SQL | Keep | Read-only role, 10 s statement timeout, row cap. It is what keeps the rest of the API small. |
| Data-quality audit | Keep as tests | The audit's queries become the acceptance suite, run after every sync. |
| Venue travel times (Google Routes) | Defer | The 268 known venues are seeded with their computed times. New venues get times from an optional, isolated job that can never fail the sync. |
| Similarity and `/discover` | Cut for now | Hours of backfill for unclear value; Claude can reason about similar artists itself. Revisit after Phase 4. |
| Exploration modes served by the API | Replace | A mode becomes a queue profile plus a prompt kept in the Claude Project. The API serves data only. |
| Hand-written `/api/schema` registry | Cut | FastAPI's generated OpenAPI. |
| `/api/admin/migrate` over HTTP | Cut | Real migrations run from CI. |
| SeatGeek, Dice, venue-site scrapers | Defer | Only if do312 proves unreachable from the cloud. |
| Spotify audio-feature CSVs | Cut | A static 2024 snapshot; Spotify retired the audio-features API. |
| Old sync API, old track resolver, 8 legacy tables, Prefect | Gone | Nothing is migrated, so nothing needs dropping. |

## Target architecture

*(Drawing in the doc: four sources → five jobs in GitHub Actions → two Neon projects ↔ two Fly apps ← Claude sessions; a repository band underneath.)*

```
Sources                GitHub Actions (daily cron, dev + prod)     Neon Postgres          Fly.io              Claude
ListenBrainz ─┐        ingest → resolve → derive → shows → dq      musicdata-prod  ◀──▶   FastAPI prod  ◀──  curl via
MusicBrainz   ├──────▶                                      ──▶   musicdata-dev   ◀──▶   FastAPI dev        OpenAPI
Ticketmaster  │
Repo seeds   ─┘
Repository: code, Alembic migrations, seeds, workflows; secrets per GitHub Environment.
Alerts: Actions failure email and ntfy push; GET /ops/status shows the last run and check results.
```

Jobs write both databases on a daily cron; each API reads its own database and writes only the tables Claude curates (lists, interests); nothing runs on the laptop. One Docker image serves the API and runs every job, so there is one codebase and one set of dependencies to keep green.

## Environments and cost

The recommended stack runs both environments for about $7–12 a month, leaving most of the $40 unspent. Prices are from the vendors' pricing pages as of 2026-10-07 (see Sources).

| Component | Dev | Prod | Monthly |
| --- | --- | --- | --- |
| Database | Neon project `musicdata-dev` (Free plan: 1 GB storage, 100 CU-hours) | Neon project `musicdata-prod` (same) | $0 |
| Scheduled jobs | GitHub Actions, environment `dev`, daily 02:17 America/Chicago | GitHub Actions, environment `prod`, daily 02:47 | $0 (2,000 min/month on a private repo; unlimited on a public one) |
| API | Fly.io shared-cpu-1x, 256 MB, may scale to zero | Fly.io shared-cpu-1x, 256 MB, scales to zero (ADR 0002 amendment) | usage-based; near $0 while idle (at most ~$2.19 each if always running) |
| Secrets | GitHub Environment secrets + `fly secrets`, per environment | same | $0 |
| Alerts | Actions failure email + an ntfy.sh push topic | same | $0 |
| Old system during overlap | — | Old Neon project + Render free API, until Phase 2 promotes | $0–5 (check the Neon billing page; Render free is $0) |
| Total | | | ~$7–12 |

Sizing checks behind the $0 database line:

- Storage: the new schema holds ~246K listen rows at roughly 200 bytes each plus indexes, on the order of 100–150 MB, inside the 1 GB per project. The current 495 MB is mostly `raw_payload` JSONB and legacy tables, neither of which carries over.
- Compute: 100 CU-hours a month per project at Neon's 0.25 CU minimum is 400 active hours. A 10-minute daily job plus a few hours of Claude sessions a day uses under half.
- Actions minutes: two daily syncs at ~5 min each is ~300 min/month. A full MusicBrainz backfill (~5.5 h at 1 request/s) runs once, in resumable chunks of under an hour (the per-job limit is 6 h).
- If a free tier pinches, Neon Launch is pay-as-you-go with no base fee: $0.106 per CU-hour and $0.35 per GB-month, roughly $5 a month per project at this usage.

Alternatives considered: Render at $7 for an always-on prod API plus a free dev API (30–50 s cold starts, 30 s request timeout, the limit that forced the old workarounds); Railway Hobby at $5 including $5 of usage; a single ~$4 VPS running everything, cheaper on paper but one unmanaged host with no run history. Fly is recommended because it has no request timeout and keeps prod warm for under $5.

## Development environment

One repository, one Python package, one lockfile, one Docker image that runs both the API and the jobs. The laptop writes code and runs tests; everything else happens in GitHub Actions, Fly and Neon.

```
music-data-platform/
├── pyproject.toml, uv.lock          # Python 3.12, uv-managed
├── Dockerfile, fly.toml             # API image; the same image runs jobs in Actions
├── docker-compose.yml               # local Postgres 17 for tests
├── .github/workflows/
│   ├── ci.yml                       # ruff + pytest on every pull request
│   ├── daily-sync.yml               # cron; matrix over environments dev and prod
│   ├── backfill.yml                 # manual dispatch: job name, environment, batch size
│   └── deploy-api.yml               # dev on merge to main; prod on tag v*
├── migrations/                      # Alembic revisions, plain SQL inside
├── src/musicdata/
│   ├── config.py, db.py, log.py     # settings from env; one pool factory with retry; JSON logs
│   ├── identity/                    # norm_key, MBID handling, alias upserts
│   ├── clients/                     # listenbrainz, musicbrainz, lastfm, ticketmaster, do312
│   ├── ingest/  resolve/  derive/   # listens → identity → tracklists → sessions and stats
│   ├── shows/  canon/               # scrapers and loaders; lists and genres
│   ├── api/                         # FastAPI app and routers
│   └── jobs/                        # CLI: musicdata ingest | resolve | derive | shows | dq
├── seeds/                           # taxonomy.yaml, lists/*.csv, canon_export.csv, venues.csv, manual_tracklists.csv, festivals/*.csv
├── tests/
│   ├── unit/                        # pure functions: norm_key, sessions, headliner parsing
│   ├── integration/                 # against a real Postgres (service container in CI)
│   └── dq/                          # acceptance checks, run against dev and prod after each sync
└── docs/adr/                        # one short file per decision
```

Tooling, all free: uv (dependencies, venv, lockfile), ruff (lint and format), pytest with pytest-asyncio, pre-commit, Alembic, Docker.

The local loop:

1. `uv sync` builds the venv from the lockfile.
2. `docker compose up db` starts a local Postgres; `pytest` runs unit and integration tests against it.
3. `musicdata --env dev ingest` runs any job against the dev database with exactly the code CI runs.
4. Open a pull request; CI runs ruff and pytest with a Postgres service container.
5. Merge to `main`: the API deploys to dev, and the next daily sync in dev uses the new code.
6. Tag `v0.3.0`: the API deploys to prod and prod's sync picks up the tag. A tag is the only road to prod.

Secrets live in GitHub Environments (`dev`, `prod`) and in `fly secrets` per app. A local `.env` is gitignored and never holds prod credentials. A secrets scanner runs in pre-commit from the first commit, so the password-in-settings incident cannot repeat.

Observability is part of Phase 1, not a later improvement. Every job writes one row to `pipeline_run` (job, environment, started, finished, status, rows affected, error). `GET /ops/status` returns the last run per job and the latest acceptance-check results, so Claude can check health at session start. A failed Actions run emails and pushes to ntfy. One gotcha: on a public repo, GitHub disables a scheduled workflow after 60 days without a commit, so either keep the repo private or let the sync commit a small status file monthly.

## Data model

Every fact table joins on integer IDs assigned once at ingest. Raw strings are kept for display and audit but are never join keys, which is the single change that removes most of the April findings.

Identity layer:

| Table | Key | Notes |
| --- | --- | --- |
| `artist` | `artist_id`; UNIQUE `mbid` (nullable); UNIQUE `norm_key` | MBID when ListenBrainz supplies one; `norm_key` from one deterministic function with a frozen golden-file test; `CHECK (norm_key <> '')` |
| `artist_alias` | `raw_name` → `artist_id` | every raw spelling ever seen, with source and first_seen; lookups by name go through here |
| `release_group` | `release_group_id`; UNIQUE `mbid` (nullable); UNIQUE (`artist_id`, `norm_key`) | title, primary type (Album / EP / Single), secondary types, first release year; `is_compilation` and `is_box_set` flags |
| `release_group_alias` | (`artist_id`, `raw_album`) → `release_group_id` | edition variants land here: "London Calling (Remastered)" and "London Calling" are one release group |
| `release_group_tracklist` | `release_group_id` → `track_count`, canonical `release_mbid`, `source`, `resolved_at`, `note` | one row per release group; source is musicbrainz, lastfm, manual or unresolved |
| `release_group_track` | (`release_group_id`, position) → title, `recording_mbid` | the canonical release's tracklist; what completion is measured against (~20 MB for 19K albums) |

Facts and derived tables:

| Table | Grain | Notes |
| --- | --- | --- |
| `listen` | one ListenBrainz listen | `listened_at`, `artist_id` NOT NULL, `release_group_id` (nullable), `norm_title`, raw strings, `recording_mbid`, `release_mbid` (the edition played), client; UNIQUE (`listened_at`, `artist_id`, `norm_title`) |
| `album_session` | one detected or manual session | `release_group_id`, start, end, tracks played, track count, completion, session type, `source` (scrobbled / manual) |
| `artist_stat`, `release_group_stat` | one artist / one release group | materialized; rebuilt by `derive` after each ingest |
| `venue`, `show`, `show_artist`, `show_interest`, `festival` | | `show_artist` carries role (headliner/support), `raw_name`, `clean_name`, `artist_id` (nullable) |
| `list`, `list_entry`, `tag`, `entry_tag`, `queue_profile`, `verdict` | | see Lists, the atlas and the queue |
| `pipeline_run`, `dq_result` | one job run / one check result | see Development environment and Acceptance checks |

Rules that make whole classes of bugs impossible:

- Identity is assigned once, at ingest: ListenBrainz mapping gives the MBID; otherwise `norm_key`. It is never re-derived per query. (Closes B2, B3, B4, A3, A4.)
- Edition policy: completion and sessions are computed per release group. The denominator is the standard edition's track count (the smallest official release without deluxe, anniversary or expanded markers). Completion is capped at 1.0. Release groups flagged box set or compilation (over 30 tracks, or a compilation secondary type) are excluded from sessions. (Closes A1, A2, C3 and the Pet Sounds case.)
- Heard/unheard for any list is a join on `release_group_id` inside a view. There is no stored match table to go stale. (Closes A5 and the two overlapping match tables.)
- Show artists are cleaned at scrape time and resolved to `artist_id`; `raw_name` stays beside `clean_name`. A show whose headliner is not an artist (DJ nights, tributes) has a null `artist_id` and cannot match. (Closes E1, E2, E3 and the "Queen!" false positive.)
- `norm_key` has exactly one implementation, in Python, with a golden-file test. SQL never re-implements it. (Closes the four-copy drift.)
- No `raw_payload`. Raw listens are reproducible from ListenBrainz; the export zip is archived outside the database.

## Edge rules

Identity is decided once at ingest, by ID where ListenBrainz supplies one and by one deterministic function otherwise; anything the rules cannot decide goes to a review queue instead of a guess.

- Sessions: per release group, listens ordered by time, a new session whenever the gap exceeds 30 minutes. Completion = distinct tracks that are on the standard tracklist ÷ standard track count, capped at 1.0; full at ≥ 0.8, partial at ≥ 0.25 with at least 3 tracks. Distinct tracks are counted by recording MBID when mapped, by normalized title otherwise. Albums and EPs only; singles, compilations and box sets (secondary type, or over 30 tracks) are excluded. Recomputed incrementally for release groups with new listens, each rebuilt as a unit.
- Editions: deluxe and standard are releases under one release group, and everything user-facing is per release group. The canonical release is official, within 12 months of the group's first release date, fewest tracks; it is overridable, and a check flags any album where more standard tracks were heard than the count allows. Bonus tracks are real listens that do not advance completion. For unmapped strings, edition markers (deluxe, remaster, expanded, anniversary, bonus, edition, version, reissue, mono, stereo, complete) are stripped from the local key.
- Same-name EPs and albums: mapped listens are different release groups with a primary type. The local key keeps "EP" in the title. When a search returns several release groups with one title, the one whose tracklist overlaps most with the titles you listened to wins; ties go to the ambiguous queue.
- Special characters: MBIDs are script-agnostic. The local key is NFKC → casefold → diacritics stripped on Latin script only → `&` to "and" → cut only at feat. / ft. / featuring → keep letters and digits of any script → collapse spaces → drop a leading English "the" → fall back to the casefolded raw string if the result would be empty. Display names are always the raw string. The golden-file test holds the audit's cases (ぶだら, @, !!!, ¥$, Ms. Lauryn Hill, "Awaken, My Love!", Matchbox Twenty) and the classics (R.E.M., AC/DC, The The, Sigur Rós, Motörhead).

## Lists, the atlas and the queue

Every list is a source, an album on it is a candidate, and a session is evidence. There are no wishlists to maintain and no Heard column to fill in.

| Table | Purpose |
| --- | --- |
| `list` | one row per source: rolling_stone_500, 1001_albums, aoty_2019…, v_atlas, claude_canon, personal; with year, URL, weight |
| `list_entry` | `list_id`, `release_group_id`, rank, priority (Essential / Recommended / Deep cut), lane, zone, layer, `start_here`, note, `added_by`, `added_at`, `review_status` |
| `tag`, `entry_tag` | your context vocabulary (study, workout, cooking, with-people, rainy, morning…) attached to release groups, each with a source: manual, playlist, rule, suggested |
| `queue_profile` | a named filter over facets and tags, goal weights, and a composition (new / revisit / pinned fractions) |
| `verdict` | per release group: again / later / never, an optional rating, a one-line note, and the session that prompted it |

The V Album Atlas is the depth goal's seed and its vocabulary. Layer (era), Zone (Core / V / Context), Lane with parent lanes, Priority, Start here, Scene, Label, Tags and Paths load as seven CSVs in `seeds/atlas/`. Its three blank columns are never filled in the sheet: Heard is computed, and Rating and Notes arrive through the verdict. Measured against the Spotify export on 2026-10-07 it is 9% heard (266 of 2,942 albums), 21% of Essentials, 27% of Core Essentials; 415 of its 1,201 artists have any plays.

The queue is a query: unheard entries plus a revisit slice, filtered by the active profile, ordered by priority = number of lists × list weight × rank percentile × priority tier, boosted by goal weights and by coverage gaps. Composition defaults to 70% new, 20% revisit, 10% pinned or wildcard. The revisit slice draws from four pools: spaced repeats of atlas albums with one or two full sessions (due after about 2 weeks, 2 months, 8 months, widening each time a revisit becomes a full session); abandoned loves (heavy play, silent for 6+ months); rescues (verdict *later*, due after 3 to 6 months); unfinished partials. Nothing resurfaces within 30 days of its last session, and the slice never exceeds its fraction.

Per-entry actions: pin, bump, snooze for 30 or 90 days, mark played (a manual session), verdict. A shuffle re-rolls the order within priority tiers so the top ten is not the same ten every day.

Tags are added where you already have an opinion, never from a blank page: chips on the post-session form; `POST /tag-now-playing` from a phone Shortcut, with ListenBrainz `playing-now` identifying the album; a Spotify playlist named `ctx: <tag>` read by the sync, if the API check passes; batch calls from Claude; and suggestions from session timestamps, confirmed with a tap and never applied on their own. Profiles filter on facets first (lane, zone, mood and production tags, language, decade), so manual tags are for exceptions. A tag no profile uses after a month is retired.

Curation keeps provenance: Claude adds entries with `added_by = claude:<session>` and `review_status = proposed`; one command approves in bulk; rejects are marked, not deleted. The atlas's Borderline Log and Needs Verification sheets are the same idea and load as review rows.

## API surface

About twenty endpoints and one page replace fifty endpoints. OpenAPI is generated from the code, and `/query` covers the long tail so no endpoint needs to anticipate every question.

| Endpoint | Purpose |
| --- | --- |
| `GET /health`, `GET /ops/status` | liveness; last run per job and latest acceptance-check results, which Claude reads at session start |
| `GET /openapi.json` | replaces the hand-written `/api/schema` |
| `GET /queue` | the phone page: the next ten for a profile, play / pin / snooze / mark played, the post-session verdict form; token in the URL; opening it triggers a rate-limited catch-up ingest |
| `GET /next` | the same queue as JSON for Claude: `profile`, `n`, `shuffle` |
| `POST /entries/{id}/pin`, `/bump`, `/snooze` | per-entry actions; snooze takes 30 or 90 days |
| `POST /sessions` | a manual session (vinyl, a show): release group, date, completion |
| `POST /verdicts` | again / later / never, optional rating, note, tags |
| `POST /tag-now-playing`, `POST /tags/batch` | tag the album ListenBrainz says is playing now; tag many release groups at once |
| `POST /ingest/catch-up` | pull listens since the watermark and recompute their sessions; rate-limited |
| `GET /listens/summary`, `/listens/timeline`, `/listens/recent` | totals, periods, latest plays; one shared `start_date` / `end_date` / `days` / `limit` filter |
| `GET /artists`, `GET /artists/{id}`, `POST /artists/batch` | top and search; the one-stop artist page (stats, albums with completion and sessions); bulk lookup by name resolved through `artist_alias`, with trigram fallback that returns candidates |
| `GET /albums`, `GET /albums/{id}` | completion, sessions, list memberships, lanes, tags; `min_completion` / `max_completion` filters |
| `GET /sessions` | session browser with the same filters |
| `GET /shows`, `PUT` and `DELETE /shows/{id}/interest` | filters: date range, venue, festival, `match=true` (scored), `presales=true`, `just_announced_days` |
| `GET /lists`, `GET /lists/{id}`, `POST /lists/{id}/entries`, `GET /gaps` | lists and the atlas; `/gaps` is the priority-sorted unheard list, filterable by lane, zone, list and language |
| `GET /profiles`, `PUT /profiles/{name}` | the saved queue profiles |
| `POST /query` | read-only role, 10 s statement timeout, row cap |

Conventions kept from the April rewrite spec: bearer auth, `format=compact` by default for Claude with `format=json` for structured output, ISO dates, empty results rather than errors. An exploration mode is now a queue profile plus a prompt kept in the Claude Project. If curl ever becomes the limiting factor, an MCP wrapper can be generated from the OpenAPI file without changing the service.

## Acceptance checks

Each April audit finding becomes a query in `tests/dq` with a threshold. The suite runs after every sync, its results are what `/ops/status` shows, and all of it must be green in dev before a version is tagged for prod.

| Check | Source finding | Threshold |
| --- | --- | --- |
| Listen count in the database vs ListenBrainz `listen-count` | new | within 0.5% |
| Listens with NULL `artist_id` | B2 (137 rows) | 0, enforced by NOT NULL |
| Artists sharing a `norm_key`; release groups sharing (`artist_id`, `norm_key`) | B3, A3, A4 (20 + 140 + 174 rows) | 0, enforced by UNIQUE |
| Release groups with 10+ listens and no track count | C1, tracklist audit (336 albums) | ≤ 5% |
| Release groups with 10+ listens that are resolved | tracklist audit (78% today) | ≥ 90% |
| Sessions split across aliases of one release group | C3 (29) | 0, impossible by key |
| Completion overshoot: distinct tracks heard > 1.5 × track count | tracklist audit (149) | ≤ 2% of resolved albums, each flagged for review |
| Show headliners whose `clean_name` still has residue (quotes, `w/`, ` / `, presents, vs, tribute) | E1 (636–658) | 0 |
| Upcoming shows whose headliner resolves to an `artist_id`, among headliners present in listens | E2 | ≥ 95% |
| Support acts containing unsplit ` & ` or ` / ` | E3 | 0 |
| Checklist heard/unheard stale vs recomputed | A5 | not applicable: computed view, no stored table |
| Canon or checklist case duplicates | A4 | 0, keyed on ID |
| Genre values outside the taxonomy | D3 | 0, enforced by foreign key |
| Summary artist count equals `COUNT(DISTINCT artist_id)` | B1 | exact |
| Daily sync wall time | operations | under 15 min; each step isolated so one failure cannot block the others |
| Database connection drops | operations (32 failures May–Sep) | retried with backoff; alert after 3 failed attempts |

The thresholds that are not zero (resolution rate, overshoot) are starting targets; raise them as the manual override list grows. A check that fails in prod pages the ntfy topic and shows red in `/ops/status`, so a silent three-month outage cannot happen again.

## Phases and gates

*(Drawing in the doc: six bands left to right — Week 0, Foundations, Listening core, Chicago shows, Lists + queue, Retire — with a gate diamond after each of the four main phases.)*

Each diamond is a promotion gate: a phase is done only when its criteria hold in dev, and the version is then tagged and deployed to prod. Phase 1 ends with a deliberately empty system that already deploys, schedules, logs and alerts in both environments; that is the point at which the laptop stops mattering. Phase 2 is the first time the new prod answers a real question, and the old Render API is retired for listening queries then. The old Neon project and Render service are deleted at the Phase 4 gate, which also frees whatever they cost today. Dates assume 6–8 focused hours a week; the gate criteria are the rule, the dates are targets.

| Phase | Dates | Deliverable | Gate to promote |
| --- | --- | --- | --- |
| 0 · Week 0 | Oct 8–14 | Scheduler off, secrets rotated, ListenBrainz export saved, seed CSVs exported, decisions answered | all items in Next seven days ticked |
| 1 · Foundations | Oct 15 – Nov 8 | Repo with uv, ruff, pytest, pre-commit, Docker; Neon dev + prod; Alembic; GitHub Environments; CI; hello-world API on Fly dev + prod with `/health` and `/ops/status`; a no-op daily job writing `pipeline_run`; failure alerts; do312 spike result | a tag deploys to prod; a scheduled job runs in both environments; a forced failure notifies |
| 2 · Listening core | Nov 9 – Dec 13 | Identity + listen schema; full ListenBrainz load into dev; incremental and catch-up ingest; resolution via MBIDs with MusicBrainz and Last.fm fallback, tracklists stored; sessions (scrobbled and manual) and stats; `tests/dq`; listens, artists, albums, sessions and `/query` endpoints; a first `/queue` page that only shows recent sessions and takes a verdict | acceptance suite green; database count within 0.5% of ListenBrainz; promoted to prod; old API retired for listening questions |
| 3 · Chicago shows | Dec 14 – Jan 10 | Venue seed; Ticketmaster + do312 (or the fallback decided in Phase 1); headliner cleaning with tests; match scoring; interests; festivals from CSV | E1 and E3 at 0, E2 ≥ 95%; shows endpoints in prod |
| 4 · Lists, the atlas and the queue | Jan 11 – Feb 7 | The seven atlas CSVs and the published lists loaded; `list` / `list_entry` / `tag` / `queue_profile` / `verdict`; `/next` and `/gaps`; the full queue page with pin, snooze, mark played and tag chips; the revisit slice; three starting profiles; `tag-now-playing`; Spotify playlist tagging if the API check passes | atlas ≥ 95% resolved to release groups; the queue page is the daily surface; old Neon project and Render service deleted |
| 5 · Retire | Feb 8–21 | Archive old repos; README and portfolio write-up; decide on similarity | nothing left running outside the new stack |

## Open decisions

Fourteen choices are yours; each has a recommendation, and each answer becomes one ADR file in `docs/adr/`.

| # | Decision | Options | Recommendation and why |
| --- | --- | --- | --- |
| 1 | Repo visibility | Public (portfolio, unlimited Actions minutes) vs private (2,000 min/month, no 60-day schedule auto-disable) | Public. It is portfolio work; the listening data lives in Neon, not the repo; a secrets scanner in pre-commit guards the rest. Let the sync commit a status file monthly to keep the schedule alive. |
| 2 | API host | Fly.io (~$6.58 for both) vs Render ($7 prod + free dev with cold starts) vs Railway Hobby ($5) | Fly. No request timeout, prod always on, dev can sleep. Render is the fallback if Fly's deploy flow is a chore. |
| 3 | Schema tooling | Alembic with plain SQL in revisions vs a SQL-only tool (yoyo, dbmate) vs SQLAlchemy ORM | Alembic with raw SQL. Industry-standard, no ORM to learn, works identically in CI and locally. |
| 4 | Derived layer | dbt-core for views, stats and data tests vs plain SQL files run by `musicdata derive` | Depends on whether you want dbt on the résumé from this project. Plain SQL first is fine; dbt can be adopted in Phase 2 without changing the schema. |
| 5 | Local database for tests | Docker Desktop Postgres on the laptop vs a Neon dev branch | Docker: fast, offline, deterministic. Neon branches as the fallback if Docker on Windows is painful. |
| 6 | do312 fallback | If Cloudflare blocks GitHub runners: Ticketmaster + venue-site scrapers, or run scrapers on the Fly machine instead | Decide after the week-1 spike; do not design around it yet. |
| 7 | Chicago shows scope | Keep presales and interest tracking as today, or trim | Keep both; both are cheap and used by the Live Shows profile. |
| 8 | Venue travel times | Seed only vs keep a Google Routes job | Seed only now. Add new venues by hand or with an optional job that can never fail the sync. |
| 9 | Similarity and discover | Confirm the cut | Cut; revisit after Phase 4 if a profile needs it. |
| 10 | Weekly time budget | Hours per week you can give this | Sets the phase dates. The roadmap assumes 6–8 focused hours a week. |
| 11 | Atlas vocabulary | Adopt Layer / Zone / Lane / Priority / Start here as the depth goal's vocabulary, or keep a generic tier | Adopt it. It is better than the generic version and already has 2,942 rows behind it. |
| 12 | Queue surface | The `/queue` page, a Spotify "Next up" playlist, or both | Page first; the playlist if the Spotify API check (20 minutes) passes. |
| 13 | Starting profiles | Which three to ship first | `default` (the three-goal mix), `home-genre` (atlas, Core first), one language. Add `study` and `workout` once a few dozen entries carry tags. |
| 14 | Tag vocabulary and verdict words | Your five to eight context tags; *again / later / never* | Start with study, workout, cooking, with-people, rainy, morning. Keep the three verdict words; add a 1–5 rating only if you will actually use it. |

## Next seven days

Everything below is small, reversible, and makes the planning session concrete. None of it writes pipeline code.

- [ ] Disable the Windows Task Scheduler task `foo`. Nothing is lost; ListenBrainz keeps the history.
- [ ] Rotate the Neon database password and the Render `API_SECRET`. Delete the permission rule holding the password in `music-pipeline\.claude\settings.local.json`.
- [ ] Request a full ListenBrainz export (`POST /1/export/`, or the website's export page) and save the zip beside `spotifydata.zip`.
- [ ] Export seed data from the old database to CSV: `canonical_albums`, `checklist_sources` (list memberships with rank), `album_tracklist` where `source = 'manual'`, `venues`, `show_interests`. The atlas is already a workbook; it becomes seven CSVs in Phase 4.
- [ ] Create the GitHub repository with the uv, ruff, pytest and pre-commit scaffold and an empty `musicdata` package (decision 1 first).
- [ ] Create Neon projects `musicdata-dev` and `musicdata-prod`; store each `DATABASE_URL` in the matching GitHub Environment.
- [ ] Spike: one Actions workflow that fetches one do312 page and one Ticketmaster page and prints the status codes. This decides the shows source early.
- [ ] Answer the fourteen open decisions in a 30-minute pass; the answers become ADR files and Phase 1 starts.
- [ ] Listen to three of the 65 unheard Core Essentials in `exports\atlas_coverage_2026-10-07.csv`. The tool catches up to you, not the reverse.

## Parked

Good ideas that plug in later without changing anything above: the general facets graph beyond what the atlas encodes (a `node` / `edge` table with a source and confidence on every edge); Wikidata "influenced by" and Discogs styles reached through the IDs MusicBrainz already returns (the API, not the dumps, which are too large for this budget); recording-level credits, the ~17-hour MusicBrainz tail, gated by listen count; language starter canons for the five non-English languages; similarity and discover; SeatGeek, Dice and venue-site scrapers; venue travel-time automation; the Global Jukebox, which is culture-level data and does not join to MBIDs.

## Sources

Project material read on 2026-10-07: `PROJECT_CONTEXT.md`, `planning-context/01_ARCHITECTURE_apr2026.md`, `03_data_quality_audit_2026-04-18.md`, `04_tracklist_audit_2026-04-13.md`, `05_chicago_sources_research.md`, `06_original_api_rewrite_spec.md`, `02_GENRE_TAXONOMY.md`, and the code in `music-pipeline/` and `claudemusic/`. The Spotify counts come from the 19 JSON files in `spotifydata/`; the ListenBrainz counts and the 1,000-listen sample from the public API.

- [Neon pricing](https://neon.com/pricing): Free plan 1 GB and 100 CU-hours per project; Launch $0.106 per CU-hour, $0.35 per GB-month.
- [Render pricing](https://render.com/pricing): free web service; $7/month for 0.5 CPU, 512 MB; cron at $0.00016/minute.
- [Fly.io pricing](https://fly.io/pricing.md): shared-cpu-1x 256 MB $2.19/month; shared-cpu-2x 512 MB $4.39/month.
- [Railway pricing](https://railway.com/pricing): Hobby $5/month including $5 of usage.
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions): 2,000 minutes/month and 500 MB on GitHub Free for private repositories; public repositories free.
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits): 6-hour job limit on hosted runners.
- [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows): 5-minute minimum interval; public-repo schedules disabled after 60 days without activity; delays at the top of the hour.
- [ListenBrainz core API](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html): `listens` endpoint with `min_ts`/`max_ts`, 1,000 per page; `listen-count`.
- [ListenBrainz export API](https://listenbrainz.readthedocs.io/en/latest/users/api/export.html): `POST /1/export/`, poll, download zip for full history.

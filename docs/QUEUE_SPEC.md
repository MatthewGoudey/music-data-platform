# Queue spec — Phase 4: lists, the atlas and the queue

- Status: authoritative for everything about the queue. Where `docs/REDESIGN_PLAN.md`, an
  ADR or an earlier commit describes the queue differently, this file wins.
- Version 2, 2026-10-09 (after Claude Code's progress report of Oct 8, 9:50 PM CT).
- Owner: Matt. Change a rule here only after Matt agrees, and record why at the bottom.

## 0. Priorities and what happens first

**The queue is the top priority.** It is what the whole system exists for. Everything else
waits until the Phase 4 gate, except the short Step 0 below.

**Step 0 — clean up before Block A (small, one commit each, report when done):**
1. Remove the "Upcoming shows for you" section from the `/queue` page. Shows stay available
   through `GET /shows` and the claude.ai Project; the queue page carries albums only.
2. Remove the verdict cards and forms from the `/queue` page. Until Block E puts "Up next"
   there, the page shows the recent full sessions as a read-only list with the existing history
   line. Leave the `verdict` table, `POST /verdicts` and the album page's verdict list exactly as
   they are: verdicts are dormant (section 11), and nothing in the queue depends on them.
3. Ask Matt whether v0.2.3 (presales, shows on Fly, Step 0) goes to prod. Tag it only on his go;
   then let prod's shows sweep run once.
4. Ask Matt how to keep the third-party lists unpublished, because the GitHub repo is public:
   (a) make the repo private (check the last month's Actions minutes against the 2,000 free
   minutes a private repo gets first), or (b) keep `seeds/lists/` out of git (add it to
   `.gitignore`) and load those files into dev and prod from the laptop with
   `musicdata --env <env> lists load`. Commit `seeds/lists/` only after he answers. `seeds/atlas/`
   is Matt's own work and can be committed either way.

**Then Phase 4, Blocks A–G (section 15).**

**Waiting until after the Phase 4 gate:** venue travel times, festivals, any further shows work,
and any further verdict work.

**Data exports are Claude Code's job, not Matt's.** The atlas and the published lists are
already in `seeds/`. The old-database tables (the Claude canon, venues, manual tracklists) come
out in Block A through a read-only SELECT using the old `DATABASE_URL` in
`..\music-pipeline\music-pipeline\.env`; ask Matt once before connecting.

**The old pipeline keeps running.** Leave its Task Scheduler task and its Neon database alone
until Matt says otherwise.

## 1. The one idea

The queue answers one question: **which album should Matt put on next?**

- **Candidates come from lists.** A list is a source (the V Album Atlas, Rolling Stone 500,
  1001 Albums, the AOTY lists, later the Claude canon and language lists). An album on a list
  is a *list entry*, and every entry is a candidate.
- **Listening history is evidence, and it has exactly three jobs:**
  1. mark each entry **heard** (a full session exists), **started** (some listens, no full
     session) or **unheard**;
  2. supply the **revisit slice** (section 9), the only part of the queue that comes from history;
  3. add a small **artist-affinity** boost to the score (section 7).
- Always draw the "new" part of the queue from `list_entry`. An album earns a new slot by
  being on a list and having no full session.
- The goal behind this: Matt wants wide exposure, the canon filled in, and depth in his home
  genre (the Neil Young lineage of indie rock × country, mapped in the atlas). The queue
  measures progress against lists, so the lists are the backbone.
- Matt's effort per album stays near zero: he presses Play, listens, and the next sync marks it
  heard. Every other action is optional.

## 2. Glossary

| Term | Meaning |
| --- | --- |
| listen | one row in `listen`: one play of one track |
| session | one row in `album_session`: a run of plays of one release group with gaps ≤ 30 min; `full` at completion ≥ 0.8, `partial` at ≥ 0.25 with ≥ 3 tracks; `source` is `scrobbled` or `manual` |
| heard | the release group has at least one **full** session, scrobbled or manual |
| started | the release group has listens or a partial session, and no full session |
| unheard | the release group has neither |
| list | one row in `list`: a source of candidates, with a goal (`canon`, `depth`, `breadth`, `personal`) and a weight |
| list entry | one album on one list, with its position, priority and list-specific facets |
| candidate | a resolved, unheard list entry that passes the active profile's filters |
| queue item | one album shown in the queue, in one slot: `pinned`, `new`, `revisit` or `wildcard` |
| profile | a saved set of filters, weights and slot composition the queue runs under |
| pool | one of the three sources of revisit items (section 9) |
| hidden | Matt pressed "Not for me": the album leaves every slot until he unhides it |
| verdict | dormant: the existing table and endpoint stay, and the queue reads nothing from them |

## 3. Seed data (already in `seeds/`)

| File | Rows | What it is |
| --- | --- | --- |
| `seeds/lists/_lists.csv` | 5 | the registry: `slug, name, goal, weight, ranked, default_priority, file, source` |
| `seeds/atlas/albums.csv` | 2,942 | list `v_atlas`: `atlas_id, artist, album, year, type, label, layer, zone, primary_lane, secondary_lanes, style, region, base, scene, descriptors, key_tracks, priority, start_here, in_field_guide, lineage_basis, confidence` |
| `seeds/atlas/album_notes.csv` | 2,942 | prose per atlas album, joined on `atlas_id`: `description, lineage, terry_url, source_urls` |
| `seeds/atlas/lanes.csv` | 54 | `lane_id, name, zone, era_span, definition, parent_lanes, child_lanes, key_scenes, key_labels` |
| `seeds/atlas/paths.csv` | 111 | listening paths: `path, step, atlas_id, artist, album, year, lane, connection_to_next` |
| `seeds/atlas/scenes.csv`, `labels.csv`, `tags.csv`, `artists.csv` | 52, 38, 50, 1,201 | reference gazetteers; load into tables only when a feature needs them |
| `seeds/lists/rolling_stone_500.csv` | 500 | list `rolling_stone_500`, ranked by `position` |
| `seeds/lists/1001_albums.csv` | 978 | list `1001_albums`; `position` is chronological, so the list is unranked; `descriptors` holds the mood words |
| `seeds/lists/aoty_2007_2024.csv` | 899 | list `aoty_2007_2024`; 50 per year, unranked; the year is in `year` and `note`; carried over from the old project, origin unrecorded |
| `seeds/lists/acclaimed_music_3000.csv` | 3,000 | list `acclaimed_music_3000`: Acclaimed Music's Top 3,000 albums of all time, aggregated from critics' lists, ranked; final update 2020-11-29; `note` holds the site's album id and alternate titles (`aka White Album`) |

All list files share one shape: `position, artist, album, year, priority, genre, descriptors, note`.
The atlas maps onto it as: `position` empty, `priority` from the atlas, everything else into
`list_entry.facets` (JSON).

Added in Block A by Claude Code (one export script, read-only, old database):
- `seeds/lists/claude_canon.csv` from `canonical_albums` (~5.3K: `artist, album, genre, subgenre,
  tier, description`), registered in `_lists.csv` as goal `canon`, weight `1.0`, tier mapped to
  priority (essential → Essential, the rest → Recommended), genre and subgenre into `genre`.
- `seeds/venues.csv` (venues with travel times) and `seeds/manual_tracklists.csv` (rows of
  `album_tracklist` with `source = 'manual'`), for later phases; exported now to make one trip.

### 3a. More lists (investigated 2026-10-09)

| Source | What it offers | Access | Status |
| --- | --- | --- | --- |
| Acclaimed Music (acclaimedmusic.net) | Top 3,000 albums from thousands of critics' lists; decade and year pages; year-end summaries as Excel files for 2007–2019 | one static page per list; no robots.txt; frozen since 2020-11-29; a plain scripted request gets HTTP 403 | **added** as `acclaimed_music_3000` (copied once, 39 garbled or bracketed titles repaired by hand) |
| BestEverAlbums (besteveralbums.com) | an overall chart aggregated from 60,000+ charts (critics' and members'), updated daily; decade, year and genre charts | free members may download any chart as CSV, 20 a day; bigger downloads for premium; terms prohibit reproducing site content | **Matt's step:** create a free account, download the overall chart CSV (and any decade or genre charts he wants), drop them in `seeds/lists/`; Claude Code converts each to the shared shape and registers it |
| Album of the Year (albumoftheyear.org) | critic and user aggregate scores; year-end aggregate lists | terms §9 forbid bots and scrapers without written permission; they invite API and data-partnership requests at info@albumoftheyear.org | **Matt's call:** email for permission before any automated use; the existing `aoty_2007_2024` list stays as it is |

**Adding any list:** put a CSV in the shared shape in `seeds/lists/`, add one row to
`_lists.csv` (goal, weight, ranked, default priority, source), then run `musicdata lists load`
and `musicdata lists resolve`. Lists from websites arrive through their own download or with
the site's permission; keep them unpublished (section 0, step 4).

## 4. Schema (migrations 0011–0013)

**0011 lists**
- `list`: `list_id` serial PK, `slug` UNIQUE, `name`, `goal` CHECK in (canon, depth, breadth, personal),
  `weight` numeric default 1.0, `ranked` boolean, `default_priority`, `source`, `created_at`.
- `list_entry`: `entry_id` bigserial PK, `list_id` FK, `position` int NULL, `raw_artist`, `raw_album`,
  `year` int NULL, `priority` CHECK in (Essential, Recommended, Deep cut), `lane_id` NULL,
  `zone` NULL, `layer` NULL, `start_here` boolean default false, `facets` jsonb default '{}',
  `note`, `artist_key`, `album_key`, `release_group_id` FK NULL,
  `resolve_status` CHECK in (pending, resolved, ambiguous, unresolved) default pending,
  `resolve_detail` jsonb, `added_by` default 'seed', `added_at`,
  `review_status` CHECK in (accepted, proposed, rejected) default accepted;
  UNIQUE (`list_id`, `artist_key`, `album_key`); index on `release_group_id`.
- `atlas_lane` (`lane_id` PK, `name`, `zone`, `era_span`, `definition`), `atlas_lane_parent`
  (`lane_id`, `parent_lane_id`), `atlas_path` (`path`, `step`, `atlas_id`, `connection_to_next`).

**0012 queue state and profiles**
- `queue_state`: `release_group_id` PK, `pinned_at`, `bumped_until`, `snoozed_until`,
  `hidden_at`, `updated_at`.
- `queue_profile`: `name` PK, `description`, `filters` jsonb, `goal_weights` jsonb,
  `zone_weights` jsonb, `composition` jsonb, `affinity_weight` numeric, `updated_at`;
  seeded with the rows in section 10.

**0013 tags**
- `tag`: `tag_id` PK, `name` UNIQUE, `description`, `active` boolean; seeded with
  study, workout, cooking, with-people, rainy, morning.
- `release_group_tag`: (`release_group_id`, `tag_id`) PK, `source` CHECK in (manual, playlist, rule, suggested),
  `status` CHECK in (applied, suggested, rejected), `created_at`.

Always key queue state and tags on `release_group_id`, so an album keeps its state whichever
list it appears on.

## 5. Loading and resolving lists

`musicdata lists load` (idempotent): read `_lists.csv`, upsert each list, upsert every entry from
its file. Always compute `artist_key` and `album_key` with `musicdata.identity` (the same
functions ingest uses). For `v_atlas`, set `lane_id` from the leading token of `primary_lane`
(`C1 Indie twang …` → `C1`), copy `zone`, `layer`, `priority`, `start_here = (start_here == 'Y')`,
and put the remaining atlas columns plus `secondary_lanes` into `facets`. Load lanes, lane
parents (split `parent_lanes` on commas) and paths.

`musicdata lists resolve --limit N` (resumable, 1 MusicBrainz request per second): for each
entry with `resolve_status = pending`, in this order:
1. **Already known.** Match an existing release group through the identity layer
   (`artist_alias` / artist key, then the release group title key for that artist). This catches
   every album Matt has already played, at zero API cost.
2. **MusicBrainz search.** Search release groups by artist and title; keep primary type
   Album or EP; prefer the candidate whose first-release year is within ±1 of the entry's year,
   then the best title-key match. Create the artist and release group through the same
   identity functions resolve uses, and leave the tracklist to the normal resolve job.
3. **Outcome.** One clear match → `resolved`. Several → `ambiguous`, with the candidates in
   `resolve_detail` for review. None → `unresolved`.

Always keep unresolved and ambiguous entries out of the queue; `/gaps` lists them under
"needs matching" so Matt or Claude can fix them by hand.

Initial pass: `gh workflow run backfill -f env=dev -f job=lists -f args="resolve --limit 2000"`,
repeated until nothing is pending (~6,000 searches after the overlap between lists, about 2 hours in total). Nightly:
`daily-sync` runs `musicdata lists resolve --limit 300` after `resolve`.

## 6. Status: heard, started, unheard

One view, `list_entry_status`: every accepted, resolved entry with `status` (heard / started /
unheard), `full_sessions`, `partial_sessions`, `listens`, `tracks_heard`, `track_count`,
`last_session_at`, `pinned`, `snoozed_until`, `hidden`. Every progress number and every queue
exclusion reads this view. Always compute heard from `album_session` at read time.

## 7. Scoring a candidate

Put every constant below in `src/musicdata/queue/config.py`, so tuning is a one-line change.

For a candidate release group *g* under profile *p*:

```
base(g)  = Σ over accepted entries e of g:
           list.weight × p.goal_weights[list.goal] × tier(e) × position_factor(e)
           × start_here(e) × p.zone_weights[e.zone or 'none']

tier(e)            = Essential 3.0 · Recommended 2.0 · Deep cut 1.0
                     (entry priority, else the list's default_priority, else Recommended)
position_factor(e) = ranked list: 1.0 − 0.5 × (position − 1) / (list size − 1)   (#1 → 1.0, last → 0.5)
                     unranked list: 1.0
start_here(e)      = 1.25 when the atlas marks it Start here, else 1.0

score(g) = base(g)
         × (1 + p.affinity_weight × min(ln(1 + artist listens) / ln(501), 1))
         × lane_gap(g)        = 1 + 0.3 × (1 − heard share of g's atlas lane); 1 off the atlas
         × bump(g)            = 2.0 while queue_state.bumped_until > now, else 1.0
```

An album on three lists scores roughly three times one on a single list. That is intended:
overlap between independent lists is the strongest signal of "you should hear this".

**Excluded from new and wildcard slots:** heard albums. **Excluded from every slot:** hidden
albums, `snoozed_until > now`, entries not `accepted`, entries not `resolved`, release groups
outside the profile's filters (pins excepted, section 8).

## 8. Building a queue

Input: profile, `n` (default 10, max 50), `shuffle` (default false), `seed` (optional).

1. **Pinned.** Every pinned release group, oldest pin first, up to `n`. Pins ignore the
   profile's filters and are exempt from the artist rule below.
2. **Revisit.** `round(n × composition.revisit_share)` slots (default 0.2 → 2 of 10), filled
   round-robin from the pools in section 9 in the order spaced → abandoned → unfinished, most
   overdue first. Slots a pool cannot fill pass to the next pool, then to new.
3. **Wildcard.** `composition.wildcard` slots (default 1), only when there are no pins: one
   candidate drawn at random from ranks 51–500 of the profile's scored list.
4. **New.** The remaining slots: candidates by descending score.
5. **One album per artist** across the whole queue (pins exempt): when an artist repeats, take
   the next candidate.
6. **Determinism.** Same profile, same Chicago calendar day and same data give the same queue:
   seed the wildcard draw with `profile + date`. With `shuffle=true`, fill the new slots by a
   weighted sample (weight = score) without replacement from the top `SHUFFLE_POOL` (200)
   candidates, leaving out the release groups in `exclude`, using `seed` or a fresh random
   seed; the wildcard is drawn with the same seed. Always return the seed used.
7. **Shuffle again.** The page's Shuffle is a button pressed as often as Matt likes: each
   press calls `/next?shuffle=true` with a fresh seed and `exclude` = every new album shown
   since the last "Top picks", so no album repeats. When fewer than the new slots remain
   outside `exclude`, the page clears it and starts over. "Top picks" returns to the
   ranked queue.

## 9. Revisit pools (the only candidates that come from listening history)

| Pool | Qualifies when | Due when |
| --- | --- | --- |
| spaced | 1–3 full sessions, and on any list | days since last full session ≥ 14 / 60 / 240 for 1 / 2 / 3 full sessions; after 3 full sessions the album leaves this pool |
| abandoned | ≥ 3 full sessions or ≥ 40 listens | last listen ≥ 180 days ago |
| unfinished | ≥ 1 partial session, 0 full, and on any list or ≥ 2 partials | last session ≥ 14 days ago |

Across all pools: exclude hidden and snoozed albums, and require the last session to be
≥ 30 days ago (unfinished: ≥ 14). These pools replace the old `revisit` and `finish`
exploration modes. "Give it another shot later" is a Snooze.

## 10. Profiles (seeded rows)

| name | filters | goal_weights | zone_weights | composition | affinity |
| --- | --- | --- | --- | --- | --- |
| `default` | all accepted lists | canon 1, depth 1, breadth 1 | all 1.0 | n 10, revisit 0.2, wildcard 1 | 0.2 |
| `home-genre` | lists: `v_atlas` | depth 1 | Core 1.5, V 1.0, Context 0.6 | n 10, revisit 0.2, wildcard 1 | 0.2 |
| `canon` | goals: `canon` | canon 1 | all 1.0 | n 10, revisit 0.1, wildcard 1 | 0.0 |

Filter keys a profile may use: `lists`, `goals`, `zones`, `layers`, `lanes`, `primary_types`,
`tags`, `descriptors_any` (matches atlas descriptors and 1001 moods), `decades`, `languages`.
Language profiles arrive once language lists exist (section 15, later); until then `canon`
fills the third starting slot (ADR 0013 amendment).

## 11. Actions

Every action is one tap on a queue card, and every one is optional.

| Action | Effect |
| --- | --- |
| Play | open `https://open.spotify.com/search/<artist> <album>` (URL-encoded) |
| Pin / Unpin | set or clear `queue_state.pinned_at` |
| Bump | `bumped_until = now + 14 days` (score × 2 meanwhile) |
| Snooze 30 / 90 | `snoozed_until = now + 30 or 90 days` |
| Mark played | `POST /sessions` with completion 1.0 at now (a manual full session, for vinyl or a show); the album leaves new slots |
| Not for me | set `queue_state.hidden_at`; the album leaves every slot; `/unhide` reverses it |
| Tag | reveal the tag chips; a tap writes `release_group_tag` with source `manual` |

Suggested tags stay `suggested` until Matt confirms them on the page.

Verdicts are dormant: the `verdict` table, `POST /verdicts` and the album page's list stay as
they are, and the queue reads nothing from them. Revisit them with Matt after the Phase 4 gate.

## 12. API

| Endpoint | Purpose |
| --- | --- |
| `GET /next?profile=default&n=10&shuffle=false&seed=&exclude=` | the queue as JSON (compact with `format=compact`; `exclude`: comma-separated release group ids kept out of the shuffled new slots): `profile`, `generated_at`, `seed`, `items[]` with `slot`, `release_group_id`, `artist`, `album`, `year`, `primary_type`, `score`, `status`, `tracks_heard`, `track_count`, `why[]` (list, position, priority, lane, zone, start_here), `pool` and `reason` for revisits, `tags`, `play_url` |
| `GET /gaps?list=&goal=&lane=&zone=` | progress per list and per atlas lane: total, resolved, heard, started, unheard, % heard, needs matching |
| `GET /lists`, `GET /lists/{slug}?status=unheard&limit=` | lists with counts; one list's entries with status |
| `POST /lists` | create a list: `slug, name, goal, weight, ranked, default_priority` |
| `POST /lists/{slug}/entries` | add entries `[{artist, album, year, priority, note}]`; entries from Claude arrive as `proposed` with `added_by = claude:<context>` |
| `POST /lists/{slug}/entries/approve` | accept proposed entries by id, or all |
| `POST /queue/{release_group_id}/pin`, `/unpin`, `/bump`, `/snooze?days=30`, `/hide`, `/unhide` | queue state |
| `GET /profiles`, `PUT /profiles/{name}` | read and edit profiles |
| `POST /tags/{name}/apply`, `POST /tags/{name}/remove` | tag or untag release groups: `{"release_group_ids": [...]}` |
| `POST /tag-now-playing?tag=workout` | tag whatever ListenBrainz `playing-now` reports (Block F) |

## 13. The `/queue` page

One phone-width column, albums only:
1. **Profile switcher**: links for each profile; the current one highlighted; a Shuffle
   button that draws a fresh set on every press, and "Top picks" after a shuffle (section 8).
2. **Up next**: the `/next` items for the profile. Each card: artist — album (year · type);
   a *why* line (`V Atlas · C1 Indie twang · Essential · start here` or
   `Rolling Stone #2 · 1001 Albums`); for revisits, the pool and reason
   (`spaced · 2nd listen due, last full play 61 days ago`); the existing history line; buttons
   Play · Pin · Snooze 30 · Snooze 90 · Mark played · Not for me · Tag.
3. **Progress strip** at the foot: heard / total for each list and for the atlas's Core zone,
   from `/gaps`.

## 14. Acceptance checks

Add to `musicdata dq` (stored in `dq_result`, shown in `/ops/status`):

| Check | Threshold |
| --- | --- |
| L1 every list in `_lists.csv` has as many entries as data rows in its file (after in-file duplicates) | exact |
| L2 `v_atlas` entries resolved | ≥ 95% |
| L3 each canon list resolved | ≥ 90% (`claude_canon` ≥ 80%) |
| L4 heard count from `list_entry_status` equals heard count recomputed from `album_session` | exact |
| L5 atlas heard share (sanity against the 2026-10-07 name-match estimate of 9%) | between 5% and 40% |

Add to the integration tests (`tests/integration/test_queue.py`), against fixtures:

| Test | Asserts |
| --- | --- |
| Q1 composition | `n` items when enough candidates exist; pins first; revisit count ≤ round(n × share) |
| Q2 provenance | every `new` and `wildcard` item has at least one accepted, resolved list entry |
| Q3 exclusions | no heard album in new or wildcard slots; no hidden or snoozed album anywhere |
| Q4 one per artist | at most one item per artist outside pins |
| Q5 determinism | same profile, day and data → same queue; same `seed` with shuffle → same queue |

Q2 is the guard for the mistake this spec exists to prevent: a queue built from listening
history alone fails it.

## 15. Build order (mirrored in `CLAUDE.md`; report to Matt after each block)

- **Step 0 — clean up** (section 0): shows section and verdict cards off the page; ask about v0.2.3;
  ask how to keep the third-party lists unpublished.
- **Block A — seeds and schema.** Commit `seeds/atlas/` as it is, and `seeds/lists/` the way Matt
  chose in Step 0. Migration 0011;
  `musicdata lists load`; check L1 in dev. Export the Claude canon, venues and manual
  tracklists from the old database (section 3) after asking Matt once, and register the canon.
- **Block B — resolve.** `musicdata lists resolve`; the backfill passes in dev; checks L2 and
  L3; report counts of resolved, ambiguous and unresolved per list, with the 20 most important
  unresolved entries.
- **Block C — status and progress.** `list_entry_status`; `GET /lists`, `GET /gaps`; checks
  L4 and L5. Report atlas coverage by zone and priority next to the 2026-10-07 estimate
  (9% overall, 21% of Essentials, 27% of Core Essentials).
- **Block D — the engine.** Migrations 0012–0013; `src/musicdata/queue/` (`config.py`,
  `score.py`, `pools.py`, `build.py`); `GET /next`; tests Q1–Q5. Report the first `default`
  and `home-genre` queues with their *why* lines, and wait for Matt's reaction before Block E.
- **Block E — the page and actions.** Up next replaces the read-only list; profile switcher,
  shuffle, pin, bump, snooze, mark played, not for me; the progress strip.
- **Block F — tags.** The Tag control on cards, `POST /tags/{name}/apply` and `/remove`,
  `POST /tag-now-playing`.
- **Block G — the gate.** Dev green on L1–L5 and Q1–Q5 → Matt says go → tag → prod load and
  resolve → Matt uses the page for a week. In `docs/claude-project-instructions.md`, add rows for
  `/next`, `/gaps` and `/lists`, remove the "Record a verdict" row, and tell Matt to paste the
  update into the Project instructions.

Later, outside Phase 4: language starter lists (one per language: Spanish, French, Korean,
Japanese, Chinese) built with Claude through `POST /lists`, a `language` column on
`release_group` from the canonical release's text representation, and language profiles; a
Spotify "Next up" playlist; tag suggestions from session timestamps; whatever Matt decides
about verdicts.

## 16. Worked example

Ratboys — *Printer's Devil* (2020) is on `v_atlas`: lane C1 Indie twang, zone Core,
priority Essential. Matt has played other Ratboys records but no session of this one.

1. `lists load` creates the entry; `lists resolve` matches it through MusicBrainz to a release
   group and creates that release group, although Matt has never played it.
2. `list_entry_status` says `unheard`. Under `home-genre`: base = 1.0 × 1 × 3.0 (Essential) ×
   1.0 × 1.5 (Core) = 4.5; affinity lifts it a little (Matt knows Ratboys); lane C1's low
   heard share lifts it more. It appears in an Up next slot with the why line
   `V Atlas · C1 Indie twang · Essential`.
3. Matt taps Play and listens through. Opening the page triggers the catch-up ingest; derive
   records a full session; the entry becomes `heard` and leaves the new slots. The progress strip
   shows one more Core album heard.
4. Optionally he taps Tag → `rainy`. Fourteen days after that session it qualifies for the
   spaced pool and can return in a revisit slot, with the reason `spaced · 2nd listen due`.

## Changes to this spec

- 2026-10-09 v1: first version (Matt, via Claude). Third starting profile is `canon` until
  language lists exist.
- 2026-10-09 v2: after Claude Code's progress report. Added section 0 (the queue is the top
  priority; Step 0 cleanup; exports are Claude Code's job; the old pipeline keeps running).
  Shows and verdict cards come off the `/queue` page, which carries albums only. Verdicts are
  dormant: they were a suggestion from planning that Matt never asked for, so the queue no longer
  uses them. "Not for me" (hide) replaces the `never` verdict, Snooze replaces `later`, and the
  rescue pool is gone. Tags move from the verdict form to a Tag control on each card. A progress
  strip joins the page. Acclaimed Music's Top 3,000 joins the lists; BestEverAlbums and AOTY are
  documented as Matt's steps (section 3a). Step 0 now asks how to keep third-party lists out of
  the public repo.
- 2026-10-09 v3: Matt's answers to the Block A report (Claude Code).
  - `claude_canon` carries artist, album, year and tier only (no genre, subgenre or
    description; they conflict with the atlas), at weight 0.5; tiers map essential →
    Essential, important → Recommended, deep → Deep cut.
  - Only the 40 hand-verified rows of the old `album_tracklist` become
    `seeds/manual_tracklists.csv`; the resolve job applies them as `source = 'manual'`.
    Venues stay in the old database until the venue work after the gate.
  - Two rows of one list with the same keys whose years are more than one apart are two
    albums: the later one's `album_key` gets a year mark (`weezer #2001`), and resolve
    matches on the unmarked key with the year. This recovers Weezer *Green*, Crystal
    Castles *(I)* and Elvis's '68 NBC-TV Special from the Acclaimed list.
  - `lists resolve` (section 5) accepts a known album only when its first-release year is
    within ±1 of the entry's, and settles entries naming the same album on several lists
    together. Release groups it creates get tracklists from the normal resolve job once
    they have a listen.
  - BestEverAlbums' overall chart (10,000, downloaded by Matt) joins as
    `besteveralbums_overall`: canon, weight 1.0, ranked; `scripts/convert_besteveralbums.py`
    turns a fresh download into the shared shape.
- 2026-10-09 v4: Matt, after trying the page mockup. Shuffle is a button pressed as often as
  he likes, each press drawing a fresh set: the weighted sample comes from the top 200
  candidates (was the top `3 × new slots`), and `exclude` keeps albums already shown out
  until the pool runs low (section 8 steps 6–7, section 12, section 13).
- 2026-10-09 v5: Block E built by Claude Code while Matt was away; he can reverse any of it.
  "Mark played", "Not for me" and "Snooze" remove the card at once and show Undo for a few
  seconds (`POST /queue/sessions/{id}/undo` takes back a manual session within an hour;
  `unsnooze` and `unhide` reverse the others). The page reads `GET /queue/data` and acts
  through `POST /queue/{release_group_id}/{action}`, with the page token or the API token.
  The Tag button arrives with Block F.
- 2026-10-09 v6: Matt. Check L3's bar for `claude_canon` is 80%: much of that list was
  written by an AI for the old pipeline, with some songs named as albums and some names
  invented for obscure genres, so about a sixth of it cannot resolve (dev: 83.1%). The
  other canon lists keep 90%.
- 2026-10-09 v7 (trial, dev only): Matt asked to try two sections below Up next again:
  "Recently finished" (full sessions in the last 30 days, `GET /queue/recent`) and "Shows
  you might like" (the 25 best-matched shows of the next 90 days by the `GET /shows?match=true`
  ranking, listed by date; `GET /queue/shows`).
  They load after the queue and never slow it. Prod gets them only if Matt keeps them.
- 2026-10-09 v8: Matt kept the v7 sections and added one: "Presales & on-sales" (`GET /queue/sales`),
  the 25 best-matched shows up to a year out with a presale or public on-sale in the next 30
  days, soonest sale first; a window still open after the public on-sale is a perk, not a
  presale. Shows by one headliner on one night are one show (Oh My Rockness's venue wins:
  shows moved indoors from the Salt Shed's fairgrounds). All three sections go to prod.
- 2026-10-09 v9: Matt clarified that by "presale" he means announced but not on sale yet. The
  section is "Not on sale yet": shows up to a year out by artists he listens to whose public
  on-sale is still ahead (no 30-day window), each with any presale before it and the public
  on-sale time, soonest first. On-sale times come from Ticketmaster.
- 2026-10-09 v10: Matt asked for more shows in both lists. Ticketmaster is read a year ahead
  (was 180 days), "Not on sale yet" holds up to 50 shows, and "Shows you might like" up to
  50 over the next 180 days.
- 2026-10-09 v11: Matt asked that Shuffle re-draw every card but the pins: revisits come from the
  due pools in random order and the wildcard is drawn again, and `exclude` (every card shown
  since Top picks) keeps revisits and wildcards from repeating too.
- 2026-10-09 v12: Matt added the Billboard 200 to widen toward popular music people his age
  would know. `scripts/convert_billboard.py` turns the weekly charts (1963-2026) into
  `billboard_200`: the top 2,500 albums by weeks charted since 2005 + 0.3 x earlier weeks + 2 x
  top-10 weeks, greatest hits and compilations left out (and any entry MusicBrainz knows as a
  compilation rejected after resolving, `lists reject-compilations`). Goal `breadth`, weight 0.8,
  ranked; Essential to #250, Recommended to #1,000. A `popular` profile (migration 0017) draws
  from it alone; `default` counts it too.
- 2026-10-10 v13: Matt added Rate Your Music's all-time top 5,000 (his download, `rym_clean1.csv`;
  snapshot date not recorded). `scripts/convert_rym.py` writes `rym_top_5000` in the shared shape,
  with RYM's genres and descriptors carried over and the rating in the note. Goal `canon`, weight
  1.0, ranked, like BestEverAlbums; label "Rate Your Music". The music graph's rule that Rate Your
  Music review pages are dead (graph spec, P04) covers scraped review pages, not this chart.
- 2026-10-10 v14 (planned, Matt): the music graph will shape the queue. `docs/graph/COMPANION_SPEC.md`
  section 5 specifies connection lines on cards, a `graph_affinity` score factor (profile
  `graph_weight`), a `thread` slot that picks a profile candidate connected to a just-finished
  album, generated lists read only by profiles that name them, and the `following` and `walk-back`
  profiles. Phase 6 Block H builds them and writes their rules into sections 7, 8, 10, 12 and 13
  here as the next version. Until then this spec describes the queue as it runs.

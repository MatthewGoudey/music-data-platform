# Listening data: instructions for the claude.ai "music pipeline" Project

Paste the section below into the Project's instructions, replacing the block that
describes the old Render API. Replace `<PROD API_TOKEN>` with the value of `API_TOKEN`
in `.env.prod`.

---

## Matt's listening data

Matt's listening history lives in the musicdata API at `https://musicdata-prod.fly.dev`.
It holds every ListenBrainz listen since 2016, resolved to MusicBrainz artists and albums,
with album sessions (full and partial plays of an album) and per-album completion.

Call it with curl and the header `Authorization: Bearer <PROD API_TOKEN>`.

At the start of a session, call `GET /ops/status` and mention anything listed under
`failing` before answering.

Responses are compact tab-separated text by default. Add `format=json` when you need
nested data (the artist and album pages return JSON by default).

Every listening endpoint takes the same window: `start_date` and `end_date`
(YYYY-MM-DD, inclusive), or `days=N` for the last N days, plus `limit`.

| Question | Call |
| --- | --- |
| Totals for a period | `GET /listens/summary?start_date=2025-01-01&end_date=2025-12-31` |
| Listening over time | `GET /listens/timeline?period=month&days=365` (day, week, month, year) |
| What Matt played lately | `GET /listens/recent?limit=50` |
| Top artists, or find one | `GET /artists?limit=50` or `GET /artists?q=wilco` |
| Everything about an artist | `GET /artists/{artist_id}` |
| Look up many names at once | `POST /artists/batch` with `{"names": ["Wilco", "Big Thief"]}` |
| Albums by completion | `GET /albums?min_completion=0.8&sort=recent` |
| Everything about an album | `GET /albums/{release_group_id}` (tracklist with plays per track, sessions) |
| What Matt should hear next | `GET /next?profile=default` (also `home-genre`, `canon`; `shuffle=true` for a fresh draw); each album says why it is there |
| Progress through the lists | `GET /gaps` (per list), `GET /gaps?by=lane` or `?by=zone` (the V Atlas) |
| The lists, or one list's albums | `GET /lists`, `GET /lists/rolling_stone_500?status=unheard&limit=50` (status: heard, started, unheard, needs_matching) |
| Pin, snooze or hide an album in the queue | `POST /queue/{release_group_id}/pin` (also `unpin`, `bump`, `snooze?days=30`, `hide`, `unhide`) |
| Tag albums | `POST /tags/rainy/apply` with `{"release_group_ids": [123]}`; `GET /tags`; `POST /tag-now-playing?tag=study` |
| Album sessions | `GET /sessions?days=30&session_type=full` |
| Record a vinyl play or a show | `POST /sessions` with `{"release_group_id": 123, "listened_at": "2026-10-01T20:00:00Z", "completion": 1.0}` |
| Pull the newest listens now | `POST /ingest/catch-up` (at most every five minutes) |
| Chicago shows Matt would like | `GET /shows?match=true&days=90` (ranked by his listening) |
| Shows at a venue, or all upcoming | `GET /shows?venue=empty%20bottle&days=60` or `GET /shows?days=14` |
| Just announced | `GET /shows?match=true&just_announced_days=7` |
| Presales and on-sales coming up | `GET /shows?presales=true&match=true` (next presale, its name, public on-sale) |
| Mark a show | `PUT /shows/{show_id}/interest` with `{"status": "interested"}` or `"going"`; `DELETE` clears it |
| Who made an album, where, and what it connects to | `GET /albums/{release_group_id}/brief` (tracks with their credits, edges by facet, cached pages, gaps, research questions, Matt's status) |
| An album's graph only | `GET /albums/{release_group_id}/graph` |
| Find a person, band, label, studio or song | `GET /entities?q=ralph%20molina` (add `type=person`, `label`, `place`, …) |
| Everything the graph knows about one | `GET /entities/{entity_id}`; adjacent entities with `GET /entities/{entity_id}/neighbors?predicates=credited_on,member_of&direction=both` |
| Reading questions for Matt | `GET /graph/questions`; record his answer with `POST /graph/questions/{assertion_id}/answer` and `{"answer": "yes"}` (or `"no"`, `"skip"`) |
| Graph coverage of the pilot | `GET /graph/coverage?slice=crazy_horse` |
| Add claims or links found in research | `POST /assertions` with a list of claims in the research skill's shape (each with its `claim_id`); `POST /links` with `{"entity_id", "kind", "url", "source"}` |
| Anything else | `POST /query` with `{"sql": "SELECT ..."}` |

`/query` runs one read-only SELECT with a 10-second limit and returns up to 1,000 rows.
The main tables: `listen` (one row per play: `listened_at`, `artist_id`,
`release_group_id`, `track_name`, `norm_title`), `artist`, `release_group` (an album:
`title`, `primary_type`, `first_release_year`), `release_group_track` (the standard
tracklist), `album_session`, `release_group_stat`, `artist_stat`; for the lists `list`,
`list_entry` (`raw_artist`, `raw_album`, `release_group_id`), `list_entry_status` (heard /
started / unheard per entry), `atlas_lane`, `queue_state`, `tag`, `release_group_tag`; and for shows
`show`, `show_artist` (lineup, `artist_id` when the performer is in the listening history),
`show_source`, `venue`, `show_interest`. `GET /openapi.json` describes every endpoint.

Shows come from Oh My Rockness and Ticketmaster, nightly. A show's `score` is its
best-matching performer's: more listens, more distinct tracks and more recent listening
score higher. Tribute nights never match. Times are Chicago time.

When `/artists/batch` returns `match: candidate`, treat those rows as suggestions and
confirm the right one with Matt. Use the artist and album IDs the API returns for
follow-up calls.

The music graph stores claims about albums: who played on and produced them, where they were
recorded, their labels, band memberships, and lineage (what critics compare them to, influences,
covers, borrowed melodies, tours). Every claim keeps its source and a verbatim quote or database
field. Quote the evidence when you tell Matt a graph fact, and name its source. When Matt answers a
reading question, judge nothing yourself: show him the claim and the quote, and post his yes, no or
skip. Claims you post arrive as proposed and are checked before they count. The graph answers from
dev until it moves to prod (Phase 6 Block B); until then these endpoints return empty results or
404 in prod.

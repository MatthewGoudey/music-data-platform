# Companion spec — Phase 6: the graph in the queue, and the album companion

- Status: authoritative for everything the graph *does for Matt*: what reaches his queue, what his
  cards say, the album and hub pages, and the liner notes and deep dives written on request.
  `docs/graph/GRAPH_SPEC.md` stays authoritative for how claims are made and checked (Phase 5,
  through Block E); its Blocks F and G (API, prod copy, walk) moved here. `docs/QUEUE_SPEC.md` stays
  authoritative for the queue: Block H below writes this spec's queue rules into it.
- Version 1, 2026-10-10. Owner: Matt. Change a rule here only after Matt agrees, and record why at
  the bottom.

## 0. What Matt asked for (2026-10-10)

- The graph **shapes what reaches his queue** and **adds context to the cards**.
- **Album pages and building blocks exist by default**, generated from the graph when a page opens.
- **Prose is written on request only**, in two kinds: short **liner notes** and a several-page
  **deep dive**. He reads them while listening to the record. A deep dive may take time and AI work
  (Claude on his plan, Sonnet or Opus, and Firecrawl searches and pages) to write.

What happens first: finish P04 under Phase 5 (load, verify, read, reading questions). P05–P07
continue alongside this phase. Graph data stays in dev until Block B; the queue's code changes in
Block H, after the documents, because the queue's gate week is under way.

Always ask Matt before: promoting the graph to prod (Block B), the default scope's Firecrawl spend
(Block C), the worker's schedule (Block G), the queue tag (Block H), anything that costs money, and
changing a rule in this file, the graph spec or the queue spec.

## 1. The one idea

The graph already holds who made each record, where, for which label, who played with whom, and —
for the albums read so far — what critics compare it to and which songs it covers or borrows. This
phase turns that into four things, cheapest first:

| Layer | When it exists | Made by |
| --- | --- | --- |
| **Building blocks**: claims, page text, connections to what Matt has heard | by default, nightly | free databases, Firecrawl, SQL |
| **Pages**: an album page and hub pages (person, band, label, studio, song) | by default, rendered when opened | the API, from the graph alone |
| **Prose**: liner notes and deep dives | on request | Claude, with research and an independent fact-check |
| **Queue effects**: connection lines on cards, a small score factor, a thread slot, follow and walk profiles | by default, after Block H | SQL over the graph |

Every sentence Matt reads traces to a claim or a cached page, as in the graph itself.

**What the graph is good at.** In dev, about 3,900 of 5,387 accepted claims are credits; lineage
(comparisons, influences, covers, borrowings) is about 135 claims on 47 of 75 read albums. So the
queue features lean on **people** (producers, players, members, studios) first and **lineage**
second, and every page and card reads well for an album that has credits only.

## 2. Glossary

| Term | Meaning |
| --- | --- |
| heard album | a release group with at least one full session; 911 in prod on 2026-10-10 |
| artist parts | the artists of a credit, a joint credit split into its parts ("Neil Young & Crazy Horse" → Neil Young, Crazy Horse) |
| connecting node | a person, band, studio, lineage object or first recording that links a candidate to a heard album (section 4) |
| near-queue set | the top 300 scored candidates of each queue profile, recomputed nightly |
| generated list | a `list` a job writes (`source = 'generated:graph'`); only a profile that names it in `filters.lists` reads it |
| page | `GET /albums/{release_group_id}/page` (album) or `GET /entities/{entity_id}/page` (hub) |
| document | one piece of on-request prose (`album_document`): `liner_notes` or `deep_dive` |
| worker | the Claude session that writes requested documents (section 8) |

## 3. Building blocks (by default)

**3.1 The graph's home moves to prod (Block B).** On Matt's go, `graph copy --source dev` fills
prod's empty graph tables; from then on prod is the graph's home: reading batches, answers,
documents and nightly jobs write to prod (`--env prod`). Dev keeps its copy for tests, refreshed from
prod when a test needs it (`graph copy --source prod` into dev's emptied graph tables).

**3.2 Work by release group (Block B).** `graph import`, `graph fetch` and `graph facts` take
`--scope` as well as `--slice`: `default` (3.3), `opened` (3.6) or `rg:<id>[,<id>…]`. They select
release groups, create the album entity and its `graph_album` row when missing, and label claims for
an album without an atlas id with `rg<release_group_id>` in its place (`R123-rg4567-F001` from
import, `R124-rg4567-W001` from facts).

**3.3 Default scope (Block C).** The free baseline runs for, in this order:
1. albums whose page was opened without a baseline (3.6);
2. the **near-queue set**, so the albums Matt sees next are covered;
3. **heard albums** (911), so connections have something to reach;
4. the **V atlas** (2,838 resolved).
About 4,500 albums; the rest of the 15,735 list albums join as they enter the near-queue set or
their page opens. `graph fetch` (Wikipedia in `facts` mode, registered review pages) follows the same
order for groups 1–3; the atlas's pages follow at the pace section 9 allows.

**3.4 Verify everything pending (Block B).** `graph verify --pending` resolves the names of claims
posted without entities (section 8) and verifies every `proposed` or `unread` claim in any batch or
none, so imported claims reach `accepted` and the `edge` view.

**3.5 Per-track credits (Block A).** `graph import` stores `qualifiers.tracks` as the list of track
titles a recording-level credit covers (a count today), and an import updates `qualifiers` on a
claim it finds again (`ON CONFLICT (claim_key) DO UPDATE SET qualifiers = EXCLUDED.qualifiers`).
Re-run the pilot's import (free) so pages can show credits track by track.

**3.6 Opened pages.** Opening a page for an album without a baseline creates its album entity when
missing and adds a `graph_album` row with the slice `opened`; the next nightly import takes it
first, and the page says "Gathering credits and sources — check back tomorrow".

**3.7 The nightly graph workflow** (`.github/workflows/graph-nightly.yml`). It runs on **prod**
after `daily-sync` completes (`on: workflow_run`), on the latest `v*` tag; dev runs it by
`workflow_dispatch` only. Steps, each a `musicdata graph …` job: `near-queue` → `import --scope
default --limit 400` → `fetch --scope default --max-credits 500` → `facts --scope default` → `link`
→ `verify --pending` → `connections` (section 4, from Block D) → `documents-sweep` (8.3, from
Block F) → `dq`. `daily-sync` keeps its 15-minute check.

## 4. Connections (the engine behind cards, scores, threads and pages)

A connection links a candidate album *g* to a heard album *h* through one **connecting node**.
Always leave out every heard album that shares an artist part with *g*: the queue's artist affinity
already covers more by the same artists.

| Kind | Node links *g* and *h* when | Weight |
| --- | --- | --- |
| person | one person or band is `credited_on` or `member_of` both: as member, producer or songwriter on *g* | 1.0 |
| person | the same, as engineer, mixer or session player on *g* | 0.6 |
| person | the same, for mastering on *g* | 0.3 |
| lineage | *g* `sounds_like` or `influenced_by` an artist or album; *h* is by that artist or is that album | 1.0 |
| cover | *g* `covers` or `interpolates` a first recording; *h* is by its artist or holds it | 0.8 |
| studio | both `recorded_at` one studio | 0.3 |

Connections use exactly these kinds: graph spec v7 keeps touring apart from working together, and a
shared label or genre is too broad to connect two records.

```
C(g) = Σ over connecting nodes n of g:  weight(n) × confidence(n) × damp(n) × listen(n)

damp(n)   = 1 / (1 + ln(1 + albums(n) / 10))  for people and studios   (albums(n): album entities linked to n)
          = 1                                   for lineage and cover nodes
listen(n) = min(1, 0.5 + 0.1 × full sessions across the heard albums n reaches)
```

Each node counts once per candidate however many heard albums it reaches, and `damp` keeps a busy
mastering engineer or a big studio from outweighing a shared drummer. `confidence(n)` is the lower
of the two edges' confidences. Constants live in `src/musicdata/queue/config.py`.

`graph connections` (nightly) stores, for every resolved album on a list or a generated list,
`graph_connection (release_group_id, score, top, computed_at)`, `top` holding the three strongest nodes with their display text:

| Kind | Card line |
| --- | --- |
| person | `Alex Farrar (producer) · also on Boat Songs ✓` |
| person, member | `Ralph Molina (drums) · Crazy Horse ✓` |
| lineage | `Uncut compares it to Neil Young & Crazy Horse ✓` (the publication in `via`, else the source page's site) |
| cover | `Covers "Four Strong Winds" (Ian & Sylvia) ✓` |
| studio | `Recorded at Drop of Sun · also Boat Songs ✓` |

✓ marks a heard album or artist. A line names one heard album at most.

**Threads.** At the end of every `derive` (cheap SQL, recorded as its own `pipeline_run` row),
`graph threads` rewrites
`graph_thread (from_release_group_id, to_release_group_id, rank, connection, computed_at)`: for each
album with a full session in the last 14 days (the newest ten; queue spec v17), its 20 strongest connected unheard
albums, by the same node rules with that one album as the heard set.

## 5. Queue effects (Block H)

Block H writes these rules into `docs/QUEUE_SPEC.md` (sections 7, 8, 10, 12, 13 and its changes
log) as its next version.

**5.1 Cards.** `/next` items gain `connections` (up to two lines from `graph_connection.top`) and
`page_url` (with the page token). The card shows the lines under the *why* line, and the album title
opens the album page. The card's buttons stay as they are; Walk back, Follow and the document
requests live on the album page.

**5.2 Score factor.**

```
graph_affinity(g) = 1 + p.graph_weight × min(C(g) / C_CAP, 1)        C_CAP = 3
```

Profile `graph_weight`: `default` 0.3, `home-genre` 0.5, `canon` 0.0, `popular` 0.0, `following` and
`walk-back` 0.0. Lists stay the backbone: the factor only reorders candidates that lists supplied,
by at most ×(1 + graph_weight). An album without a `graph_connection` row gets 1.0.

**5.3 Thread slot.** Composition gains `thread` (1 in `default` and `home-genre`, 0 elsewhere). The
slot is filled after revisits and before the wildcard: from the newest finished album with
`graph_thread` rows, take the highest-scoring album among its `to` albums **that is a candidate under
the profile** (on the profile's lists, unheard, not hidden or snoozed); else try the next newest
finished album; else the slot goes to new. The card's why line reads
`Because you finished <album>: <connection>`.

**5.4 Generated lists.** `following` and `graph_walk` are generated lists. The queue engine,
`list_entry_status` consumers, the progress strip and `/gaps` read a generated list only when the
active profile's `filters.lists` names it, so `default` and the other list profiles score exactly as
they do today. Check L1 and `lists load` skip generated lists. Entries are complete `list_entry` rows
(graph spec section 11), each with its reason in `note`.

**5.5 Follow.** Matt follows a person, band, label or studio from its hub page.
`graph_follow (entity_id, followed_at)`; `POST /entities/{id}/follow` and `/unfollow` (page auth).
`graph follow-lists` rewrites `following` with every album a followed entity is credited on, a
followed band released, a followed label released or a followed studio recorded; an album the graph
knows by MBID only gets its release group the way `lists resolve` creates one. Profile `following`:
filters `{"lists": ["following"]}`, goal weights `{"depth": 1}`, n 10, revisit 0, wildcard 0, thread
0, affinity 0.1. Why line: `Following Ralph Molina · drums`.

**5.6 Walk back.** `POST /graph/walk?from=<id>` (page auth) rewrites `graph_walk` from the album's
lineage objects and its connecting people's earlier records (section 4's kinds and weights, two hops,
older albums only), each entry's note naming the step ("Ralph Molina drums on both"). Profile
`walk-back` as graph spec section 11 defines it, with `thread` 0. The button is on the album page.

## 6. Pages (Blocks E and later; rendered from the graph when opened)

Served by a new router (`api/routers/pages.py`) with the queue page's auth: move `page_or_bearer`
into `api/deps.py`, and carry the page token (`?t=`) in every link a page makes. One phone-width
column like `/queue`, plain HTML and a little script, generated from the graph alone. Each fact
carries a small source tag (MB, Discogs, Wikipedia, the review's site) linking to its source. Each
block adds its own controls: Request buttons arrive with Block F, Follow and Walk back with Block H.

**6.1 Album page** — `GET /albums/{release_group_id}/page`, built from the brief (section 11):
1. **Header**: artist — album (year · label · type); heard, started or unheard and sessions; Play;
   tabs **Album · Liner notes · Deep dive**, each document's state (none, requested, writing, ready,
   out of date) with its Request or Rewrite button.
2. **At a glance**: recorded at and when; producers; genres (Discogs styles); atlas lane and zone
   when it has them.
3. **Tracks**: the tracklist with each track's credits (3.5), writers, and covers or borrowings
   (original and first recording). While the page is visible it asks `GET /queue/now-playing` every
   30 seconds and, when the playing track's title key matches a track here, highlights it and
   scrolls to it.
4. **Who made it**: members, producers and engineers, guests; each name links to its hub page with
   "heard *n* of *m*".
5. **Connections**: where it comes from (lineage out, each with its quote and source), where it
   leads (covers of it, albums compared to it), and its strongest connections to heard albums.
6. **Reading**: registered links — Wikipedia, reviews, interviews, No Expectations posts — each with
   its site and a one-line quote when a claim cites it.
7. **What the graph lacks**: "no lineage read yet", "credits from the label only", with the Deep dive
   request as the way to fill it.

An album with no graph data still renders: header, tracks from `release_group_track`, links, and the
request buttons; opening it triggers 3.6.

**6.2 Hub pages** — `GET /entities/{entity_id}/page`:
- **Person or band**: roles and albums by year, heard ✓ or not, "heard *n* of *m*", bands and
  projects (`member_of`, `performs_as`, `renamed_from`), people worked with most; Follow.
- **Label or studio**: albums by year with heard marks; Follow.
- **Song (work)**: every recording in order of first release, the first marked, covers and
  interpolations, the albums that hold them; Play opens a Spotify search per version.

**6.3 Search** — `GET /pages/search?q=` (page auth): albums, people, bands, labels and songs by
name, so pages are reachable before the queue card links to them (Block H).

## 7. Documents (Blocks F and G; on request)

**7.1 Two kinds.**

| | Liner notes | Deep dive |
| --- | --- | --- |
| Length | 600–900 words (about 5 minutes) | 3,000–6,000 words (several pages) |
| Research | the brief, accepted claims and cached pages | also new pages: interviews, features, oral histories, reviews |
| Firecrawl | 0 | up to `DOCUMENT_CREDITS` (default 100) per document: page fetches through the API plus the worker's searches |
| Model | any | the strongest Claude model the worker has (Opus preferred) |
| Write-back | none | new pages cached; new claims posted to the graph |

**7.2 Structure.** Both follow the record as Matt hears it.
- *Liner notes*: what this record is and where it sits · how it was made (who, where, when) · three
  to five tracks worth noticing, from the sources · where it leads.
- *Deep dive*: **Before you press play** (two paragraphs) · **The band and the moment** · **Making
  the record** · **Track by track** (one section per track, anchored `#track-<n>` so the
  now-playing highlight works here too: credits, writing, what sources say about it, covers and
  borrowings) · **Reception, then and now** · **Where it comes from** · **Where it leads** · **The
  people** · **Listen next** (five to ten connected albums with reasons, each with Pin) · **Sources**.

**7.3 Writing rules** (they go into the worker's skill).
- Always cite: every factual sentence ends with a marker, `[c:<assertion_id>]` for a claim or
  `[p:<fetch_id>]` for a cached page. Markers are the only citation format.
- Always take descriptions of how a track or record *sounds* from a source (a review, an interview,
  liner notes) and attribute them ("Pitchfork hears…"). Where no source describes a track, give its
  credits, writing and history, and leave its sound to Matt's ears.
- Always quote briefly: at most two sentences from any one review or article; paraphrase the rest.
  Documents stay private behind the page token.
- Always follow the graph's rules: covers point at the first recording; comparisons belong to the
  critic who made them; sources are English, plus the artist's own language where graph spec v8
  allows it.

**7.4 The fact-check.** Before a document goes `ready`, a separate agent that did not write it reads
each cited sentence against its claim or page and marks it supported, partial or unsupported. The
writer rewrites or removes every partial and unsupported sentence. The document stores the tally in
`checks`: `{"sentences", "supported", "rewritten", "removed", "unsupported_remaining"}`.

**7.5 Rendering.** The API renders `body_md` to HTML with raw HTML escaped, and turns each marker
into a small source tag linking to the claim's source URL or the page's URL.

**7.6 Out of date.** A document is out of date when its album has claims accepted after its
`finished_at` that come from outside its own batch (`D<document_id>`), or when a claim it cites is
later rejected or superseded. Its tab then shows "out of date · Rewrite"; a rewrite is a new version.

## 8. The worker

**8.1 Requests.** `POST /albums/{id}/documents {"kind": "liner_notes" | "deep_dive"}` (page auth)
creates a row with `version` = 1 + the highest version for that album and kind, status `requested`.
One open request (requested or writing) per album and kind. The tab shows "Requested · usually ready
within a few hours".

**8.2 Who writes.** Default — **Claude on Matt's plan, no new cost**:
- A **scheduled task** in claude.ai, created by Claude in the Project on Matt's go (Block G), runs at
  09:00, 13:00, 17:00 and 21:00 Chicago time. Each run asks for requested documents and ends at once
  when there are none.
- Matt can also say "write my requested documents" to Claude in the Project or in Claude Code for an
  immediate run.
- The worker clones the public repo and follows `.claude/skills/album-companion/SKILL.md` (Block F
  writes it from sections 7 and 8); it uses the Firecrawl connector for searches and runs the reader
  and the fact-checker as separate agents.
- The task needs: automatic approval (Matt switches it on in the task's settings, so every run
  finishes without waiting at a prompt), the API bearer token, network access to the API and to Firecrawl, and agents.
  Block G proves all four with one manual run in a cloud session before the schedule starts.

Upgrade, only on Matt's go because it costs money: a request dispatches a GitHub Actions job that
calls the Anthropic API (Sonnet for liner notes, Opus for deep dives), ready in minutes, billed per
use. Price it from the current price list before asking.

**8.3 The worker's API** (bearer token):

| Endpoint | Does |
| --- | --- |
| `GET /documents?status=requested` | requested documents, oldest first, after a sweep of lapsed leases |
| `POST /documents/{id}/start` | `requested` → `writing`; returns a `lease_token` and the lease end (3 hours); adds 1 to `attempts`; creates the graph batch `D<id>` (slice `document`) |
| `POST /documents/{id}/renew` | `{lease_token}` → 3 more hours |
| `GET /albums/{id}/brief` | the input: identity, tracks, claims (each with `assertion_id`, evidence, source URL), `pages` (each cached fetch's `fetch_id`, URL, title, site) |
| `GET /fetches/{fetch_id}` | a cached page's text |
| `GET /assertions?ids=` | claims by id, for the fact-checker |
| `POST /fetches` | `{url, mode, document_id}`: **the API fetches the page through Firecrawl**, caches it in `source_fetch` under batch `D<id>`, counts it against `DOCUMENT_CREDITS`, and returns `fetch_id` and text |
| `POST /assertions` | new claims with worker-assigned labels `D<id>-rg<release_group_id>-L<n>`, batch `D<id>`, names as written; stored `proposed`; returns label → id |
| `POST /graph/batches/{label}/reader-verdicts` | the worker's reader verdicts, by claim label |
| `PUT /documents/{id}` | `{lease_token, status: ready \| failed, body_md, checks, model, firecrawl_credits, error}`; `firecrawl_credits` counts the worker's searches only, since page fetches are already in `source_fetch` |

On `PUT … ready` the API checks the lease token, parses the markers, requires each to resolve (a claim
that is accepted or in batch `D<id>`, or a cached fetch), stores the map in `citations` with
`claims_through`, and refuses `ready` with the list of unresolved markers otherwise. On `failed`, a
document with fewer than two attempts returns to `requested`; at two it stays `failed` with its
error. A **sweep** handles lapsed leases the same way, counting the lapse as a failed attempt: it
runs before `GET /documents` answers and as the nightly `graph documents-sweep` job.

Pages the worker fetches go through the API so their text is Firecrawl's, not the writer's: the Fly
app gets `FIRECRAWL_API_KEY` for this (amending graph spec section 9). Names in posted claims are
resolved by the nightly `graph verify --pending` (MusicBrainz lookups run in Actions); the posted
reader verdicts settle the text claims. A document cites a new claim of its own batch or the page
behind it.

## 9. Budget

| Resource | Default use | Limit |
| --- | --- | --- |
| Firecrawl | Block C's default scope: about 2,100 albums not yet fetched × 5–7 credits ≈ 10,000–15,000 once, then ~1,000–3,000 a month; deep dives up to 100 each | Matt's go on Block C approves up to 15,000 for the default scope; 500 per nightly run; check G8 counts `source_fetch` credits (documents' page fetches included) plus documents' search credits against `GRAPH_MONTHLY_CREDITS` |
| MusicBrainz | ~8 requests per album: ~4,500 default albums over ~11 nights at 400 a night | 1 request/second |
| Neon | prod is 279 MB; the default scope adds ~150,000 claims and their entities, about 150 MB (measure after the first 1,000 albums) | G7 under 800 MB; stop the default import and report at 650 MB |
| Claude | worker runs (an idle run is one API call); deep dives are the big users | Matt's plan |
| GitHub Actions | the nightly graph workflow, prod only | free on a public repo |
| Money | none | the API upgrade in 8.2 only on Matt's go |

## 10. Schema (one concern per migration, after Phase 5's 0020)

- **0021 (Block D)** — connections and threads (one concern: the computed connection tables):

```sql
CREATE TABLE graph_connection (
    release_group_id INTEGER PRIMARY KEY REFERENCES release_group (release_group_id) ON DELETE CASCADE,
    score            NUMERIC NOT NULL,          -- C(g)
    top              JSONB   NOT NULL,          -- up to three: kind, node entity_id, text, heard release_group_id, weight, assertion ids
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE graph_thread (
    from_release_group_id INTEGER NOT NULL REFERENCES release_group (release_group_id) ON DELETE CASCADE,
    to_release_group_id   INTEGER NOT NULL REFERENCES release_group (release_group_id) ON DELETE CASCADE,
    rank                  INTEGER NOT NULL,
    connection            JSONB   NOT NULL,
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_release_group_id, to_release_group_id)
);
```

- **0022 (Block F)** — documents:

```sql
CREATE TABLE album_document (
    document_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_group_id  INTEGER NOT NULL REFERENCES release_group (release_group_id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('liner_notes','deep_dive')),
    version           INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'requested'
                      CHECK (status IN ('requested','writing','ready','failed','superseded')),
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts          INTEGER NOT NULL DEFAULT 0,
    lease_token       TEXT,
    lease_until       TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    model             TEXT,
    body_md           TEXT,
    citations         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- marker → assertion_id or fetch_id
    checks            JSONB NOT NULL DEFAULT '{}'::jsonb,   -- the fact-check tally (7.4)
    claims_through    BIGINT,                               -- highest accepted assertion_id seen
    firecrawl_credits INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    UNIQUE (release_group_id, kind, version)
);
CREATE UNIQUE INDEX album_document_open_uq ON album_document (release_group_id, kind)
    WHERE status IN ('requested','writing');
```

- **0023 (Block H)** — profile settings: `queue_profile.graph_weight NUMERIC NOT NULL DEFAULT 0`,
  set per 5.2, and `composition.thread` per 5.3 on the existing profiles.
- **0024 (Block H)** — follows: `graph_follow (entity_id BIGINT PRIMARY KEY REFERENCES entity
  (entity_id) ON DELETE CASCADE, followed_at TIMESTAMPTZ NOT NULL DEFAULT now())`.
- **0025 (Block H)** — generated lists and their profiles: the lists `following` and `graph_walk`
  (goal `depth`, weight 1.0, ranked, `source = 'generated:graph'`, `file_rows` NULL) and the
  `following` and `walk-back` profiles.

## 11. API (bearer token unless page auth is named)

| Endpoint | Block | Returns |
| --- | --- | --- |
| `GET /entities?q=&type=`, `GET /entities/{id}`, `GET /entities/{id}/neighbors` | A | graph spec section 10 |
| `GET /albums/{id}/graph` | A | graph spec section 10 |
| `GET /albums/{id}/brief` | A (pages list), D (connections), F (documents) | graph spec section 10, plus per-track credits, `pages`, and later `connections` and the album's documents |
| `POST /assertions`, `POST /links`, `GET /assertions?ids=` | A | graph spec section 10; `POST /assertions` stores names as written for `verify --pending` |
| `GET /graph/questions`, `POST /graph/questions/{id}/answer`, `GET /graph/coverage` | A | graph spec section 10 |
| `GET /albums/{id}/page`, `GET /entities/{id}/page`, `GET /pages/search` | E | the pages (page auth) |
| `GET /queue/now-playing` | E | ListenBrainz playing-now as reported (artist, track, release), cached 20 seconds (page auth) |
| `POST /albums/{id}/documents`, `GET /albums/{id}/documents` | F | 8.1 (page auth) |
| the worker's endpoints | F | 8.3 |
| `POST /entities/{id}/follow`, `/unfollow`, `POST /graph/walk?from=` | H | 5.5, 5.6 (page auth) |

Add the brief, neighbors, questions, pages and document request endpoints to
`docs/claude-project-instructions.md` as each block lands, so Claude in the Project can open pages,
answer reading questions and request documents.

## 12. Checks and tests

| Check | Passes when |
| --- | --- |
| C1 | every ready document's markers all appear in its `citations` map, each resolved when published |
| C2 | every ready document has `checks.unsupported_remaining = 0` |
| C3 | the latest `graph_connections` run finished in the last 36 hours, and the latest `graph_threads` run after the latest `derive` run (`pipeline_run`) |
| C4 | after the nightly `documents-sweep`, every `writing` document's lease ends in the future |
| C5 | every `graph_connection.top` entry joins albums whose artist parts are disjoint |

Tests: connections leave out shared-artist albums (including joint credits), count each node once,
and damp busy nodes; `graph_affinity` stays within ×(1 + graph_weight); `default` scores exactly as
before when generated lists exist; the thread slot takes a profile candidate from the newest finished
album and falls back to new; follow and walk entries reach `/next` under their profiles only; pages
render for an album with no graph data; `PUT … ready` refuses unresolved markers and a stale lease;
a failed document retries once; the now-playing match uses the page's own tracklist.

## 13. Build order (mirrored in `CLAUDE.md`; report to Matt after each block)

Prod runs only `v*` tags (the Fly prod app and the prod nightly workflow), so every block that
changes code ends with a version tag on Matt's go; the queue changes only with Block H's tag.

- **Block A — API.** Section 11's Block A endpoints (the old graph spec Block F); the brief with
  `pages` and per-track credits; the import's qualifier upsert and the pilot's re-import (3.5).
- **Block B — Promotion.** `graph import --scope` and `rg` labels (3.2), `graph verify --pending`
  (3.4), the nightly workflow on prod (3.7) with import, link and verify; then, on Matt's go,
  `graph copy --source dev` into prod, and prod becomes the graph's home (3.1). Later batches
  (P05–P07) run against prod.
- **Block C — Default building blocks.** The near-queue set and the default scope (3.3) in the
  nightly workflow; on Matt's go for the spend. Report coverage, credits and database size after three
  nights.
- **Block D — Connections and threads.** Migration 0021; `graph connections` (nightly) and `graph
  threads` (end of `derive`); the brief gains `connections`.
- **Block E — Pages.** The pages router and `page_or_bearer` in `deps.py`; album, hub and search
  pages; now-playing; opened-page baselines (3.6). Matt tries them on his phone, reaching them by
  search or from Claude in the Project; adjust to his notes.
- **Block F — Documents**, in two sessions:
  - **F1, the platform.** Migration 0022; the request and worker endpoints with leases, the sweep and
    server-side fetches; marker checks and rendering; the document tabs with their Request buttons.
  - **F2, the writing.** `.claude/skills/album-companion/SKILL.md` from sections 7 and 8; one liner
    notes and one deep dive written in a Claude Code session through those endpoints for an album
    Matt picks, fact-checked by a separate agent.
- **Block G — The worker.** One manual run in a claude.ai cloud session proves the task's needs
  (8.2); then, on Matt's go, Claude in the Project creates the scheduled task.
- **Block H — Queue.** Migrations 0023–0025; connection lines and the page link on cards, `graph_weight`,
  the thread slot, generated-list handling (5.4), follow and walk back with their page buttons; the
  rules written into `docs/QUEUE_SPEC.md`; tests; tag on Matt's go.
- **Block I — Sign-off.** C1–C5 and G1–G8 green in prod; Matt has used the cards, pages and one deep
  dive while listening for a week.

## 14. What Matt does

- Gives the go for the prod promotion (Block B), the default scope's spend (Block C), the worker's
  schedule and automatic approval (Block G) and the queue tag (Block H); picks the first deep-dive
  album (Block F).
- Tries the pages and documents on his phone while listening, and says what to change.
- Requests documents from album pages whenever he wants them.

## Changes to this spec

- 2026-10-10: version 1 (Matt, via Claude). Matt asked for the graph to shape the queue and the
  cards, for generated pages and building blocks by default, and for liner notes and several-page
  deep dives written on request, read while listening, with AI time and Firecrawl spent when he asks.
  The graph spec's Block F (API) became Block A here, its prod copy Block B, and its walk part of
  Block H. An independent review before hand-off fixed: generated lists leaking into list profiles,
  import by release group, verifying imported claims, the qualifier upsert, page auth, migration
  numbers, the document loop (server-side fetches, leases and their sweep, versions, one citation
  format, labels, search-only credit reports), hub nodes outweighing real connections, joint-credit
  artists, checks C1–C5, tags per block, and the block order (documents before the queue change).
- 2026-10-10 (Matt: "I want to test functionality asap, even if we dont have data fully"): the
  build order is now A, B, C, D, H, E, F, G, I. Block H comes right after D in two parts: H1, the
  queue effects Matt sees (5.1 card lines without the page link, 5.2 graph_affinity, 5.3 the
  thread slot), tested in dev on the data the graph has; H2 (5.4 generated lists, 5.5 Follow,
  5.6 Walk back, and the card's page link) lands with Block E, whose pages carry their buttons.
  Migrations renumber in build order: 0022 is H1's profile settings (was 0023); documents,
  follows and generated lists take the next numbers when their blocks land.
- 2026-10-10 (Matt: "drop liner notes, leave the album page as is"): liner notes are dropped. Side
  by side with the album page they told the same facts as prose, so the deep dive is the one
  document: the album page's tabs are Album · Deep dive, requests take `kind: deep_dive` only, and
  the `album-companion` skill writes deep dives. The album page stays as built in Block E (its
  track list carries no critic notes). Section 7's liner-notes column and 7.2's liner-notes
  outline no longer apply; `album_document.kind` keeps its check constraint, and the one test
  version written on dev (document 4, Boat Songs) stays unreachable.

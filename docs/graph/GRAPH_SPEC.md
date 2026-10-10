# Graph spec — Phase 5: the music graph

- Status: authoritative for building the music graph. `docs/graph/edge-vocabulary.md` is
  authoritative for *what* a claim is (entity types, predicates, sources, basis, precedence); this
  file is authoritative for *how it is built* (schema, jobs, checks, API, build order). Where
  `docs/REDESIGN_PLAN.md`, an ADR or an earlier commit differs, these two files win.
- Version 1, 2026-10-09. Owner: Matt. Change a rule here only after Matt agrees, and record why
  at the bottom.
- The working prototype is `scripts/graph/` (stdlib Python, file based). Run T1 — three albums,
  126 claims, 125 accepted, 1 reading question, 12 Firecrawl credits (the ledger's 17 include a
  5-credit fetch test) — lives in `data/graph/` on the laptop (gitignored): `runs/T1/`, `cache/`
  and `baseline/`. Port the prototype; keep its behaviour exactly unless this spec says otherwise.

## 0. Priorities and what happens first

**Matt said start now (2026-10-09).** The queue's Phase 4 gate (Matt's week on the page) runs in
parallel, exactly as it is:
- Always run graph jobs and keep graph data in **dev** until Block G. Migrations 0018–0019 may
  reach prod with any queue fix tagged meanwhile: empty tables change nothing there. Graph jobs,
  graph data and any graph step in `daily-sync` reach prod only in Block G, on Matt's go.
- Always keep the queue code (`src/musicdata/queue/`, `/next`, the `/queue` page) as it is until
  Block G.
- Shows, venues and verdict work still wait for the Phase 4 gate.
- The old pipeline and its Task Scheduler task keep running.

Read first, in this order: `docs/graph/edge-vocabulary.md` (sections 1–4 and 13), this file,
`scripts/graph/README.md`, and `data/graph/runs/T1/report.md`.

Always ask Matt before: spending more Firecrawl credits than section 9 allows, promoting anything to
prod, adding or changing a predicate or entity type, and anything that costs money.

## 1. The one idea

The graph stores **claims**, not facts: *subject — predicate — object*, each with a source, a
basis, verbatim evidence, a confidence and a status. A claim is accurate when its source says it
and the checks in section 8 pass; how much it matters is its confidence. **Matt's only job in
checking is reading:** he answers *reading questions* ("does this quote say X?") for the few
claims the checks cannot settle. Whether a claim is musically true is left to its sources.

Four workers, each doing what it is best at:

| Worker | Does | Where it runs |
| --- | --- | --- |
| MusicBrainz, Wikidata, Discogs (free APIs) | identity, links, labels, credits, studios, songwriters, memberships, areas, every recording of a song with its date | `musicdata graph import`, GitHub Actions |
| Firecrawl (paid, budgeted) | every web page fetch: page text plus facts JSON | `musicdata graph fetch`, GitHub Actions |
| Claude, extracting | reading cached pages for lineage, covers and relationships (never the atlas's prose: spec v5) | Claude Code sessions with the `music-graph-research` skill, on Matt's plan |
| Claude, reading | an independent agent judging each text claim against its quote alone | a separate agent in the same sessions |

Everything except the two Claude steps runs unattended. The Claude steps exchange files with the
database through `musicdata graph batch …` commands (section 7.4).

## 2. Glossary

| Term | Meaning |
| --- | --- |
| entity | one node: an album, recording, work, artist, person, label, place, area, genre, lane, map or list (`entity` table) |
| claim, assertion | one row in `assertion`: subject, predicate, object, source, basis, evidence, confidence, status |
| claim label | a claim's readable id, `<prefix>-<atlas_id>-<F or L><nnn>`: the prefix is the batch label for Claude's claims (`P01-A0029-L004`), `R<pipeline_run_id>` for job claims, `T1` for the fixtures (`T1-A2184-L027`); batch files and reader verdicts use it |
| origin | what counts as one independent source when claims agree: a database (`musicbrainz`, `discogs`, `wikidata`) or one page (`source_url` without its fragment); an atlas note whose `source_urls` include a page counts as that page |
| source | who says it: `musicbrainz`, `discogs`, `wikidata`, `wikipedia`, `map:v_atlas`, `web:<domain>`, `matt` |
| extractor | what turned the source into a claim: `musicbrainz`, `discogs`, `wikidata`, `firecrawl_json`, `claude`, `matt` |
| basis | `documented` (credit, database, the artist's own statement), `reported` (a publication says so), `inferred` (resemblance judged by a curator or critic) |
| text claim | a claim whose evidence is a quote from a page or the atlas (extractor `claude`) |
| reader | the independent agent that judges text claims; its verdict is `SUPPORTS`, `PARTIAL`, `WRONG_DIRECTION`, `DOES_NOT_SUPPORT` or `NOT_A_CLAIM` |
| reading question | a claim the reader found weaker than claimed, with no database to settle it; status `ask_matt` |
| slice | a named set of target albums (section 6) |
| batch | up to 25 albums handed to a Claude reading session (`graph_batch`) |
| fetch | one Firecrawl call, cached in `source_fetch` once per URL and schema version |

## 3. What already exists, and where it goes

| Prototype (`scripts/graph/`) | Port to | Notes |
| --- | --- | --- |
| `mb_baseline.py`, `mb_artist.py`, `mb_work.py`, `mb_resolve.py` | new methods on `clients/musicbrainz.py` (`MusicBrainzClient`) | reuse its throttle; list entries already carry release group MBIDs, so the baseline looks up by MBID and searches only for claim objects |
| `discogs.py` | `clients/discogs.py` | same shape as `clients/web.py`'s `PoliteFetcher`; 25 requests/minute, 60 with `DISCOGS_TOKEN` |
| Wikidata sitelink in `mb_baseline.py` | `clients/wikidata.py` | `wbgetentities` with `sitefilter=enwiki`; back off on 429 |
| `fetch.py`, `cache_split.py` | `clients/firecrawl.py` + `graph/fetch.py` | REST API (`POST /v2/scrape`) via httpx, no CLI in Actions; cache in `source_fetch` |
| `facts_v2.schema.json` | `src/musicdata/graph/schemas/facts_v2.json` | as is |
| `album_baseline.py`, `facts_to_claims.py` | `graph/baseline.py`, `graph/facts.py` | claims written to `assertion`; the prototype's `source: firecrawl_json` becomes source `wikipedia` with extractor `firecrawl_json`, so key every rule on `extractor` |
| `cues.py` | `graph/cues.py` | the same rules |
| `verify.py` | `graph/verify.py` | every check in section 8, against the database |
| `reader_input.py`, `report.py` | `graph/batch.py`, `graph/report.py` | |

Keep `scripts/graph/` until Block D's T1 golden test passes, then delete it in its own commit
(git history keeps it). Move the T1 run into test fixtures in Block D (section 12).

## 4. Schema (migrations 0018–0019)

Plain SQL in `op.execute`, one concern per revision, as usual.

**0018_graph_core**

```sql
CREATE TABLE predicate (
    name           TEXT PRIMARY KEY,
    facet          TEXT    NOT NULL,
    subject_types  TEXT[]  NOT NULL,
    object_types   TEXT[]  NOT NULL,   -- '{literal}' for literal objects
    symmetric      BOOLEAN NOT NULL DEFAULT false,
    lineage        BOOLEAN NOT NULL DEFAULT false,  -- influenced_by, sounds_like, covers, samples
    description    TEXT    NOT NULL
);

CREATE TABLE entity (
    entity_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- an MBID artist is 'person' when MusicBrainz types it Person, else 'artist'; claim files'
    -- types are hints, and the structure check treats person as a kind of artist
    type              TEXT NOT NULL CHECK (type IN ('album','recording','work','artist','person',
                          'label','place','area','scene','lane','map','path','list','tag','genre')),
    name              TEXT NOT NULL,
    norm_key          TEXT NOT NULL CHECK (norm_key <> ''),
    context_key       TEXT NOT NULL DEFAULT '',  -- artist norm_key for albums, recordings and works
    mbid              UUID,
    release_group_id  INTEGER REFERENCES release_group (release_group_id) ON DELETE SET NULL,
    artist_id         INTEGER REFERENCES artist (artist_id) ON DELETE SET NULL,
    local_key         TEXT,                       -- lane id, atlas id, list slug, tag name
    attrs             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- year, disambiguation, artist, album, candidates
    resolve_status    TEXT NOT NULL DEFAULT 'resolved'
                      CHECK (resolve_status IN ('resolved','ambiguous','unresolved','local')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX entity_mbid_uq ON entity (type, mbid)
    WHERE mbid IS NOT NULL AND type NOT IN ('artist','person');
CREATE UNIQUE INDEX entity_artist_mbid_uq ON entity (mbid)   -- one entity per MusicBrainz artist
    WHERE mbid IS NOT NULL AND type IN ('artist','person');
CREATE UNIQUE INDEX entity_rg_uq ON entity (release_group_id) WHERE release_group_id IS NOT NULL;
CREATE UNIQUE INDEX entity_local_uq ON entity (type, local_key) WHERE local_key IS NOT NULL;
CREATE UNIQUE INDEX entity_unmapped_uq ON entity (type, norm_key, context_key)
    WHERE mbid IS NULL AND local_key IS NULL;
CREATE INDEX entity_name_trgm ON entity USING gin (name gin_trgm_ops);  -- pg_trgm exists (0006)

CREATE TABLE assertion (
    assertion_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_key       TEXT NOT NULL UNIQUE,   -- sha1 of subject, predicate, object, source, source_url, evidence
    claim_label     TEXT NOT NULL UNIQUE,   -- e.g. T1-A2184-L027 (glossary)
    replaces        BIGINT REFERENCES assertion (assertion_id),  -- a corrected re-extraction
    subject_id      BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    predicate       TEXT   NOT NULL REFERENCES predicate (name),
    object_id       BIGINT REFERENCES entity (entity_id) ON DELETE CASCADE,
    object_value    TEXT,
    qualifiers      JSONB  NOT NULL DEFAULT '{}'::jsonb,
    source          TEXT   NOT NULL,
    extractor       TEXT   NOT NULL CHECK (extractor IN
                        ('musicbrainz','discogs','wikidata','firecrawl_json','claude','matt')),
    basis           TEXT   NOT NULL CHECK (basis IN ('documented','reported','inferred')),
    direction       TEXT   CHECK (direction IN ('subject_newer')),
    evidence        TEXT   NOT NULL,        -- text over 300 characters fails the Evidence check
    source_url      TEXT,
    fetch_id        BIGINT,                 -- FK added in 0019
    album_context   BIGINT REFERENCES entity (entity_id),  -- the album being researched
    batch_id        INTEGER,                -- FK added in 0019
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                        ('proposed','unread','accepted','rejected','ask_matt','superseded')),
    confidence      NUMERIC(3,2) NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    checks          JSONB  NOT NULL DEFAULT '{}'::jsonb,
    fails           TEXT[] NOT NULL DEFAULT '{}',
    support         TEXT[] NOT NULL DEFAULT '{}',   -- independent sources that agree
    reader_verdict  TEXT,
    reader_reason   TEXT,
    reader_first    TEXT,                   -- the reader's first verdict, kept for the M2 measure
    asserted_by     TEXT   NOT NULL,
    asserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    pipeline_run_id BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((object_id IS NULL) <> (object_value IS NULL))
);
CREATE INDEX assertion_subject_idx ON assertion (subject_id, predicate);
CREATE INDEX assertion_object_idx ON assertion (object_id, predicate);
CREATE INDEX assertion_album_idx ON assertion (album_context);
CREATE INDEX assertion_open_idx ON assertion (status) WHERE status IN ('proposed','unread','ask_matt');

CREATE TABLE entity_link (
    entity_id    BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    kind         TEXT   NOT NULL CHECK (kind IN ('wikipedia','wikidata','musicbrainz','discogs',
                     'allmusic','bandcamp','official','label_page','interview','review',
                     'live_session','streaming','liner_notes','other')),
    url          TEXT   NOT NULL,
    language     TEXT,
    source       TEXT   NOT NULL,
    verified_at  TIMESTAMPTZ,
    status       TEXT   NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','dead','blocked')),
    PRIMARY KEY (entity_id, url)
);

CREATE TABLE map_membership (
    entity_id  BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
    map        TEXT   NOT NULL,        -- 'v_atlas'
    coords     JSONB  NOT NULL,        -- atlas_id, lane, zone, layer, priority, start_here
    PRIMARY KEY (entity_id, map)
);

CREATE TABLE graph_album (                -- research progress per target album
    entity_id      BIGINT PRIMARY KEY REFERENCES entity (entity_id) ON DELETE CASCADE,
    slices         TEXT[] NOT NULL DEFAULT '{}',
    priority       TEXT,                  -- Essential, Recommended, Deep cut (from the atlas)
    baseline_at    TIMESTAMPTZ,
    fetched_at     TIMESTAMPTZ,
    facts_at       TIMESTAMPTZ,
    batch_id       INTEGER,
    verified_at    TIMESTAMPTZ,
    notes          JSONB NOT NULL DEFAULT '{}'::jsonb   -- gaps, no-match reasons
);
```

`edge` view — the current view of each edge for the API and the walk:

```sql
CREATE VIEW edge AS
SELECT subject_id, predicate, object_id, object_value,
       max(confidence)                         AS confidence,
       array_agg(DISTINCT source)              AS sources,
       (array_agg(assertion_id ORDER BY
           CASE WHEN extractor = 'matt'                                   THEN 1
                WHEN extractor IN ('musicbrainz','discogs','wikidata')    THEN 2
                WHEN basis IN ('documented','reported')
                     AND source NOT LIKE 'map:%'                          THEN 3  -- text, Firecrawl JSON, page fields
                WHEN basis IN ('documented','reported')                   THEN 4  -- the atlas's own statements
                ELSE 5 END,                                                       -- inferred, by a map or an extractor
           confidence DESC))[1]                AS best_assertion_id
FROM assertion WHERE status = 'accepted'
GROUP BY subject_id, predicate, object_id, object_value;
```

The ranks follow edge-vocabulary section 3 (migration 0020 since spec v4: the atlas is AI-written). Symmetric predicates (`associated_with`) are stored
once with the lower `entity_id` as subject.

**0019_graph_fetch**

```sql
CREATE TABLE source_fetch (
    fetch_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url             TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('facts','plain','bandcamp')),
    schema_version  TEXT NOT NULL,          -- facts-v2, plain, bandcamp-v1
    entity_id       BIGINT REFERENCES entity (entity_id) ON DELETE SET NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    credits         INTEGER NOT NULL,
    ok              BOOLEAN NOT NULL,
    error           TEXT,                   -- redacted: fc-… keys replaced by fc-***
    title           TEXT,
    body            TEXT,                   -- page markdown; TOAST compresses it
    facts           JSONB,
    pipeline_run_id BIGINT
);
CREATE UNIQUE INDEX source_fetch_ok_uq ON source_fetch (url, schema_version) WHERE ok;
CREATE INDEX source_fetch_month_idx ON source_fetch (fetched_at);

CREATE TABLE graph_batch (
    batch_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label         TEXT NOT NULL UNIQUE,     -- P01, P02, …
    slice         TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'exported' CHECK (status IN
                      ('exported','claims_loaded','reader_exported','reader_loaded','verified')),
    counts        JSONB NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE assertion ADD FOREIGN KEY (fetch_id) REFERENCES source_fetch (fetch_id) ON DELETE SET NULL;
ALTER TABLE assertion ADD FOREIGN KEY (batch_id) REFERENCES graph_batch (batch_id) ON DELETE SET NULL;
ALTER TABLE graph_album ADD FOREIGN KEY (batch_id) REFERENCES graph_batch (batch_id) ON DELETE SET NULL;
```

The Firecrawl ledger is `source_fetch` itself: credits per row, summed per month for the ceiling.
A failed fetch is a row with `ok = false` and the error: that is the gaps log.

## 5. Seeds

- `seeds/graph/predicates.csv`: the 21 predicates of edge-vocabulary section 4 (17, plus 4 in v7), with facet,
  subject and object types, `symmetric` (`associated_with`) and `lineage` (`influenced_by`,
  `sounds_like`, `covers`, `samples`). `person` counts as an `artist` subtype everywhere a
  predicate allows `artist`.
- `musicdata graph seed` loads predicates, one `map` entity (`v_atlas`), the atlas lanes as `lane`
  entities (`local_key` = lane id) with `lane_parent` claims (extractor `claude`, basis `reported`,
  confidence 0.7: the atlas is AI-written), the lists as `list` entities, and
  `map_membership` for every resolved `v_atlas` list entry (coords from `list_entry`: atlas_id
  from `facets`, lane, zone, layer, priority, start_here). Idempotent.

## 6. Slices and targets

A slice is a named query over resolved `v_atlas` list entries; the target album entity is the one
whose `release_group_id` matches.

| Slice | Albums |
| --- | --- |
| `crazy_horse` (the pilot) | lane `C1`, `V1` or `V20`, plus every `atlas_path` step of *MJ Lenderman to Neil Young: the Crazy Horse line* — 181 albums (`docs/graph/pilot_crazy_horse_albums.csv`) |
| `v_atlas` | every resolved `v_atlas` entry (after the pilot gate) |
| `album:<release_group_id>` | one album, for single-album research |

Order within a slice: Essential, then `start_here` / on a path, then Recommended, then Deep cut;
within a tier, by atlas id. Unresolved entries are listed in the coverage report and skipped.

## 7. Jobs (`musicdata graph …`, a Typer sub-app like `lists`)

Every job body is wrapped in `musicdata.jobs.runs.record`, takes `--slice` and `--limit` (the
environment comes from the global `--env`, as for every job), resumes where it stopped, and records counts and credits in `pipeline_run.notes`. Run them
in dev through the backfill workflow:
`gh workflow run backfill -f env=dev -f job="graph import" -f args="--slice crazy_horse"`.

### 7.1 `graph import` — the free baseline (port of `album_baseline.py` + the database half of `facts_to_claims.py`)

Per target album, with no Firecrawl:
1. Release group by MBID: type, first release date, artist credit, URL relations → `entity_link`.
2. Wikidata sitelink to English Wikipedia when MusicBrainz holds no Wikipedia link.
3. Canonical release by the edge rules (official, earliest, fewest tracks; reuse
   `resolve/canonical.py`), looked up with `labels, recordings, artist-rels, place-rels,
   recording-level-rels, work-rels, work-level-rels, artist-credits`.
4. Album artists: type, area and begin area, `member of band` relations (both directions).
5. Discogs: the master's main release (or the release link): credits, labels, styles.
6. Claims (extractor = source, basis `documented`, confidence 0.9, evidence = the field, e.g.
   `MusicBrainz release credits: Jack Nitzsche — co-producer, piano`):
   `credited_on` (per person per source, up to two claims: one for performing and production
   roles with roles and track count in qualifiers, one for songwriting with role `songwriter` and
   the tracks listed), `recorded_at` (places, with the MusicBrainz role), `released_by`,
   `member_of`, `based_in`, `has_genre` (Discogs styles, qualifier `vocabulary: discogs_style`).
7. Atlas `source_urls` from `album_notes` → `entity_link` (kind by domain).
8. `graph_album.baseline_at = now()`.

`graph link`: set `entity.release_group_id` and `artist_id` wherever an MBID matches. It runs by
hand until Block G, which adds it to `daily-sync` after `derive`.

### 7.2 `graph fetch` — Firecrawl (port of `fetch.py`)

Per target album, registered links only (batch jobs run no web searches):

| Link | Mode | Credits |
| --- | --- | --- |
| English Wikipedia album page | `facts`: one call, `formats` markdown + JSON with `facts_v2.json`, `onlyMainContent` | 5 |
| Bandcamp album page | `bandcamp`: `includeTags` `#name-section, .tralbum-about, .tralbum-credits, .tralbum-tags, #band-name-location` | 1 |
| any other registered link the depth table allows | `plain`, `onlyMainContent` | 1 |

Depth by atlas priority: Essential, `start_here` or on a path → Wikipedia + up to 2 other
registered links; Recommended → Wikipedia + 1; Deep cut → 1 page (Wikipedia, else Bandcamp).
Reviews and interviews found by search come only from interactive skill sessions.

Rules: reuse a cached `(url, schema_version)` row; stop at `--max-credits` (default 500) before a
call that would cross it; check the month's spend against `GRAPH_MONTHLY_CREDITS` (default 100000, the plan's limit)
before the first call; on a 403, 429, 5xx or timeout, always write the failed row (1 credit) and
move to the next page, leaving the retry to a later run; record the `creditsUsed` the API returns,
or the mode's cost when it returns none; read `FIRECRAWL_API_KEY` from settings; redact `fc-…`
from every log line and error. Verify the
v2 request shape against docs.firecrawl.dev and test one page (5 credits) before the slice.

### 7.3 `graph facts` (port of the Firecrawl half of `facts_to_claims.py`)

From each `facts` fetch: `credited_on` per person (roles merged from producers, engineers and
musicians), `recorded_at` per location. Extractor `firecrawl_json`, basis `documented`, source
`wikipedia`, `fetch_id` set. Each claim then gets the name-on-page check (section 8). Set
`graph_album.facts_at` for every album the step has seen, including albums with no page.

### 7.4 Reading batches — the Claude step

| Command | Does |
| --- | --- |
| `graph batch export --slice crazy_horse --size 25` | picks the next albums whose facts step has run (`facts_at` is set even when an album had no page to fetch) and that have no batch, creates `graph_batch` (label `P01`…), writes `data/graph/batches/<label>/`: `albums.csv`, `pages/<fetch_id>.md` (full text with the header), `cues/<fetch_id>.md` (lineage paragraphs only, from `graph/cues.py`), `atlas/<atlas_id>.json` (description, lineage, source_urls), `known.json` (entities already linked to each album, for name matching) |
| `graph batch load-claims <label> <file.jsonl>…` | loads Claude's lineage claims (shape in the skill; names resolved by section 7.5), status `proposed`. A line with `"replaces": "<claim label>"` is a corrected re-extraction: the old row becomes `superseded` and its `reader_first` carries over to the new row |
| `graph batch reader-input <label>` | writes `reader_input.txt` (port of `reader_input.py`) |
| `graph batch load-reader <label> <reader_output.jsonl>` | stores verdicts by claim label; the first verdict a claim (or the claim it replaces) ever gets is kept in `reader_first` |
| `graph verify --batch <label>` | section 8; then `graph report --batch <label>` writes `report.md` into the folder |

A Claude Code session runs a batch with the `music-graph-research` skill: export → read and write
claims → load → verify → reader input → a separate reader agent → load reader → fix flagged
extraction errors and re-read just those → verify → report → reading questions to Matt.

### 7.5 Name resolution in `load-claims`

For each subject and object, in order:
1. An entity already linked to the album (credits, members, label, places), by `norm_key`.
2. For `covers`: the work named in `qualifiers.work` on the album's canonical release; its
   recordings (MusicBrainz browse by work, with artist credits and first release dates) give the
   subject and object recordings, each an entity with its MBID and year.
3. MusicBrainz search: artists by exact name or alias; albums by title and artist. One exact hit →
   resolved. Several → `ambiguous`, candidates in `attrs`. None → `unresolved`.
4. A joint credit ("Neil Young & Crazy Horse") stays one entity named as written, with
   `attrs.parts` resolved from its parts; its date is the earliest part's.

A claim with an unresolved or ambiguous entity still goes through every check; the report lists
those entities for the next batch.

### 7.6 `graph report`, `graph copy`

- `graph report [--slice | --batch]`: counts by status, predicate and album; coverage per facet
  (albums with ≥ 1 accepted claim in credits, recording, label, people, lineage); first-pass reader
  SUPPORTS rate per predicate; reading questions; gaps (failed fetches, no-match albums,
  unresolved entities); credits this run and this month.
- `graph copy --source dev` (Block G): copies `predicate`, `entity`, `entity_link`,
  `map_membership`, `graph_album`, `graph_batch`, `source_fetch` and `assertion` into the empty
  target tables with their ids (`INSERT … OVERRIDING SYSTEM VALUE`, then reset each identity
  sequence), remapping `release_group_id` and `artist_id` by MBID in the target (NULL where absent,
  filled by `graph link`). Ask Matt before the first copy.

## 8. Verification (port of `verify.py`; keep every rule)

| Check | Passes when | On failure |
| --- | --- | --- |
| Structure | the predicate exists; subject and object types fit it; `sounds_like` is `inferred`; `influenced_by` is `reported` or `documented`; every lineage claim from `claude` has `direction = 'subject_newer'` | `rejected` |
| Evidence | text claims: the quote, split on " … ", appears verbatim in the normalised fetch body or atlas field (normaliser: strip markdown links with titles and nested parentheses, citation markers, emphasis; unify quotes; collapse whitespace; lower-case). `field:` evidence: the value appears on the page. `firecrawl_json`: the person or place name appears on its page, quote marks aside; a place also passes without a trailing "Studio(s)" word when 5 or more characters remain ("Wally Heider" for "Wally Heider Studios") | `rejected` |
| Dates | lineage: the object's year (album first release, recording first release, artist begin; a joint credit's earliest part) is not later than the subject's; passes when either year is unknown | `rejected` |
| First recording | `covers`: no MusicBrainz recording of the work predates the object's | `rejected` |
| Databases | MusicBrainz or Discogs records the same credit, membership, label, studio, or cover order (for credits on other albums, fetch that album's baseline) | adds to `support`; a contradiction rejects |
| Independence | support counts distinct origins (glossary). Two claims are the same edge when predicate matches and both names match: equal normalised keys, or the shorter key (≥ 5 characters) inside the longer ("Wally Heider" and "Wally Heider Studios") | — |
| Reader | `SUPPORTS` | `DOES_NOT_SUPPORT`, `WRONG_DIRECTION`, `NOT_A_CLAIM` → `rejected`; `PARTIAL` → `accepted` when a database records the same edge, else `ask_matt` |

**Confidence** = base + 0.1 per independent agreeing source, capped at 0.95; `matt` claims are 1.0.

| Source and basis | Base |
| --- | --- |
| MusicBrainz, Discogs, Wikidata (documented) | 0.9 |
| Firecrawl JSON from a Wikipedia personnel list | 0.75 |
| a page's own statement (`documented`, e.g. a Bandcamp field) | 0.8 |
| `reported` text; `map:*` `reported` | 0.7 |
| `map:*` `inferred` | 0.5 |
| a critic's comparison (`inferred` text) | 0.5 |

For measures, a reader `PARTIAL` counts as not `SUPPORTS`.

**Status:** `rejected` when any check fails; `accepted` when every check passes and the claim comes
from a database, from Firecrawl JSON, from a page field, or has a reader `SUPPORTS`; `ask_matt` as
above; `unread` while a text claim waits for the reader.

**Matt's answers** (`POST /graph/questions/{assertion_id}/answer`, `yes | no | skip`): yes and no
each write a `matt` claim (source `matt`, extractor `matt`, the same quote as evidence, status
`accepted` or `rejected`) and mark the question `superseded`; skip leaves it open.

## 9. Budget and limits

| Resource | Limit | Pilot estimate |
| --- | --- | --- |
| Firecrawl | 500 credits per run; `GRAPH_MONTHLY_CREDITS` 100,000 (the plan's limit; the ledger counts graph fetches only); ask Matt above 1,500 for a slice | ~1,000–1,200 for 181 albums (5 per Wikipedia page, 1 per Bandcamp page, extra pages for Essentials) |
| MusicBrainz | 1 request/second (existing throttle); 503 → back off | ~6–10 requests per album: ~30 minutes for the slice |
| Discogs | 25 requests/minute without a token | 2 per album |
| Neon | keep the database under 800 MB (check G7) | ~7,000 claims and ~300 page bodies: a few MB |
| GitHub Actions | public repo: free | graph jobs run by hand, not nightly, until the pilot gate |
| Claude | Matt's plan | ~8 batches of 25 albums, one reader agent per ~30 text claims |

Secrets: `FIRECRAWL_API_KEY` (and `DISCOGS_TOKEN` if Matt makes one) in the `dev` and `prod` GitHub
Environments and the backfill workflow's `env` block. The Fly API fetches nothing, so Fly keeps its
current secrets. Add both to
`src/musicdata/config.py` as optional `SecretStr` settings.

## 10. API (Block F; every endpoint needs `API_TOKEN`, like the existing routers)

| Endpoint | Returns |
| --- | --- |
| `GET /entities?q=&type=` | entity search (trigram on name) |
| `GET /entities/{id}` | the entity, its links, map memberships and edges grouped by facet |
| `GET /entities/{id}/neighbors?predicates=&direction=in\|out\|both&min_confidence=0.5&limit=` | adjacent entities via `edge` |
| `GET /albums/{release_group_id}/graph` | the album's edges grouped by facet, each with sources and confidence |
| `GET /albums/{release_group_id}/brief` | the brief (edge-vocabulary section 8): identity and tracklist, where it sits in the atlas, edges by facet, registered links, gaps, research questions from section 7 of the vocabulary, Matt's status and sessions, budget hint |
| `POST /assertions` | proposed claims from a Claude session (the skill's JSONL shape); runs `load-claims` resolution; returns ids |
| `POST /links` | new links `{entity_id, kind, url, source}` |
| `GET /graph/questions` | open reading questions: quote, claim, source URL, reader reason |
| `POST /graph/questions/{assertion_id}/answer` | `{"answer": "yes" \| "no" \| "skip"}` |
| `GET /graph/coverage?slice=` | the coverage report as JSON |
| `POST /graph/walk?from={release_group_id}&depth=2` | Block G |

Add `GET /albums/{id}/brief`, `GET /entities/{id}/neighbors` and the two question endpoints to
`docs/claude-project-instructions.md`.

## 11. Queue integration — "walk back from here" (Block G)

- `POST /graph/walk?from={release_group_id}&depth=2` builds a list from the album's lineage:
  ancestors through `sounds_like`, `influenced_by` and `covers` objects, the records of artists
  linked by `member_of`, `associated_with` and `credited_on` (never `toured_with`: spec v7; never the
  atlas's path steps: spec v5)
  before it — weighted by edge confidence, two hops at most.
- It replaces the entries of one list, slug `graph_walk`, ranked by walk score. Each entry is a
  complete `list_entry`: `raw_artist` and `raw_album` from the release group, `artist_key` and
  `album_key` from the same identity functions `lists load` uses, `release_group_id` set,
  `resolve_status = 'resolved'`, `review_status = 'accepted'`, `added_by = 'graph:<release_group_id>'`.
  Only albums that resolve to a release group become entries, so `list_entry_status` and `/next`
  read them exactly as they read any list.
- Migration 0021 seeds the `graph_walk` list (goal `depth`, weight 1.0, ranked, `source =
  'generated:graph'`, `file_rows` NULL) and the `walk-back` profile (filters
  `{"lists": ["graph_walk"]}`, goal weights `{"depth": 1}`, composition n 10, revisit 0,
  wildcard 0, affinity 0.1). Confirm that check L1 and `lists load` skip generated lists (no row
  in `_lists.csv`), and make them do so if they do not.
- `/next?profile=walk-back` then serves the walk through the existing queue engine.
- The `/queue` page gets one control per card, "Walk back", that calls the walk (with the same
  auth the page's Pin action uses) and switches to `walk-back`; the profile switcher shows "Walk back from <album>". The rest of the page stays as it is.

## 12. Acceptance checks and tests

`musicdata dq` gains graph checks (they run once graph tables exist):

| Check | Passes when |
| --- | --- |
| G1 | every assertion's predicate exists and its subject and object types fit it |
| G2 | every accepted text claim's evidence passes the Evidence check again (verbatim quotes, and `field:` values on the page) |
| G3 | no accepted lineage claim has an object newer than its subject |
| G4 | every accepted `claude` claim with quoted evidence has reader `SUPPORTS`, or `PARTIAL` with database support (`field:` claims are exempt) |
| G5 | every claim in a `verified` batch is `accepted`, `rejected`, `ask_matt` or `superseded` |
| G6 | no `source_fetch.error`, `assertion.evidence` or `pipeline_run.notes` matches a Firecrawl key, `(?<![0-9a-f])fc-[0-9a-f]{32}(?![0-9a-f])` (32 hex characters, so MBIDs such as `…9cfc-07e36eee65b9` stay clear) |
| G7 | the database is under 800 MB |
| G8 | this month's Firecrawl credits ≤ `GRAPH_MONTHLY_CREDITS` |

Tests:
- **T1 golden (unit, Block D):** fixtures in `tests/unit/fixtures/graph/T1/`, copied from the
  laptop's `data/graph/`: `cache/28930b8e187d.*` (Boat Songs), `cache/82f76ce5b11b.*` (Crazy Horse),
  `cache/4e5c5c6f315b.md` (Keeper's Bandcamp block) and `cache/index.jsonl` (CC BY-SA page text;
  keep the URL header); `baseline/` (MusicBrainz and Discogs JSON, trimmed to the fields used);
  `runs/T1/claims/T1_lineage.jsonl` and `T1_skipped.jsonl`; `runs/T1/reader_output.jsonl` (final)
  and `runs/T1/reader_output_round1.jsonl` (first pass); `runs/T1/claims_verified.jsonl` as the
  expected result; plus the three albums' rows of `seeds/atlas/album_notes.csv`. Run the ported
  facts and verification over them with the T1 claim labels: 126 claims, 125 `accepted`, 1
  `ask_matt` (T1-A2184-L027), 0 `rejected`, each confidence equal to `claims_verified.jsonl`.
- **Negative cases (unit):** a cover pointed at Crazy Horse's "Gone Dead Train" instead of Randy
  Newman's → rejected by first recording; a lineage claim with subject and object swapped →
  rejected by dates; `sounds_like` with basis `reported` → rejected; a quote not on the page →
  rejected; a Firecrawl-JSON name missing from the page → rejected; the atlas note citing the same
  Wikipedia page → counted once.
- **Normaliser golden file:** `tests/unit/golden/graph_normalize.json` with the link, citation and
  title cases T1 hit.
- **Integration (Neon dev):** schema constraints, `claim_key` idempotency, `load-claims` name
  resolution with made-up MBIDs, the walk list and profile; clean up rows as usual.

## 13. Build order (mirrored in `CLAUDE.md`; report to Matt after each block)

- **Block 0 — setup.** ADR 0017 (the claims graph: storage in Postgres, page bodies in
  `source_fetch`, Claude reading via batches, dev-first). Settings and secrets (section 9);
  `FIRECRAWL_API_KEY` and `DISCOGS_TOKEN` added to the backfill workflow's `env`.
- **Block A — schema and seeds.** Migrations 0018–0019 (0020 is the spec v4 edge ranks; 0021 comes in Block G); `seeds/graph/predicates.csv`;
  `musicdata graph seed`; integration tests for the constraints; apply to dev.
- **Block B — the free baseline (milestone M1).** Client methods (MusicBrainz, Discogs,
  Wikidata); `graph import`, `graph link`; run `--slice crazy_horse` in dev; report coverage per
  facet (claims of any status: nothing is accepted before Block D) and the no-match albums. Done
  when the coverage report exists for the 181.
- **Block C — Firecrawl.** `clients/firecrawl.py`, `graph fetch`, `graph facts`; one-page live test
  (5 credits); then the slice in dev (report the estimate first; ask Matt above 1,500).
- **Block D — verification and batches.** `graph verify`, `graph batch …`, `graph report`; the T1
  golden and negative tests pass (build the fixtures from `data/graph/` before deleting
  anything); then delete `scripts/graph/`. Tell Matt the skill can switch to
  the `musicdata graph batch` commands (he updates it from the claude.ai Project).
- **Block E — the pilot run (milestone M2).** The 181 albums in batches of 25 with the skill;
  reading questions sent to Matt after each batch. Done when every predicate type with ≥ 10 text
  claims has ≥ 90% `SUPPORTS` in `reader_first` (`PARTIAL` counts against), checks G1–G8 are
  green in dev, and Matt has answered the open reading questions. When a predicate type falls
  short after two batches, report the failing claims and the rule change you propose, and wait for
  Matt. (`docs/graph/golden_set_crazy_horse.csv` is a record of the first extractor test; the
  first-pass reader measure replaces it.)
- **Block F — API.** Section 10; tests; the Project instructions rows.
- **Block G — the walk and the gate (milestone M3).** Section 11 with migration 0021; a version
  tag; on Matt's go: migrations to prod, `graph copy --source dev`, `graph link` added to
  `daily-sync` after `derive`, G1–G8 green in prod; Matt uses `walk-back` for a week.

## 14. What Matt does

- Answers reading questions (yes / no / skip) from the quote alone, in the report or through
  `GET /graph/questions`.
- Agrees or not to vocabulary proposals (open: a predicate for a band's earlier name, e.g. Crazy
  Horse as The Rockets).
- Gives the go for prod (Block G) and for any Firecrawl spend above section 9's limits.

## Changes to this spec

- 2026-10-09: version 1 (Matt, via Claude), from the edge vocabulary, the prototype in
  `scripts/graph/` and run T1. Matt chose to start in dev now, alongside the queue's gate week.
  An independent review before hand-off fixed: prod isolation during the gate week, one entity
  per MusicBrainz artist, claim labels and corrected re-extractions, the edge-view ranks, the walk
  list's entries, checks G2/G4–G6, and the T1 fixture list; the golden set became a record.
- 2026-10-09: v2 (Matt). The `firecrawl_json` name check ignores quote marks (`"Sneaky" Pete
  Kleinow`) and lets a place pass without a trailing "Studio(s)" word ("Wally Heider" on the page
  for "Wally Heider Studios"). The pilot slice's first verify rejected 28 such names. Also from
  that run: `graph fetch` reads an atlas-cited Wikipedia page in facts mode only when its title
  names the album (ten artist and discography pages had credited their people to the album;
  Matt approved deleting those 64 claims).
- 2026-10-09: v3 (Matt). `GRAPH_MONTHLY_CREDITS` is 100,000, the Firecrawl plan's real monthly
  limit (15,000 was a guess). October has about 80,000 left after other Firecrawl use, which the
  graph's ledger (`source_fetch`) does not see. The per-run cap of 500 and "ask Matt above 1,500
  for a slice" stay.
- 2026-10-09: v4 (Matt). The atlas is AI-written: it picks the pilot's albums, and its notes
  count as one ordinary source. The edge view ranks it below databases and documented or reported
  pages, and its inferred claims level with an extractor's (migration 0020); a `map:*` inferred
  claim's base confidence drops from 0.6 to 0.5; `lane_parent` claims become extractor `claude`,
  basis `reported`, confidence 0.7 (they were `matt`, 1.0); the reader sees "an AI-written atlas
  note". Where the atlas conflicts with another source, both claims stay and the evidence decides.
  The walk list's migration becomes 0021.
- 2026-10-09: v5 (Matt). The atlas is AI-written, so it sets scope only: which albums and
  artists the pilot covers, their priority, lanes as coordinates, and the pages it links to. It
  is never the source of a claim. `lane_parent` claims are no longer seeded; `load-claims`
  refuses a `map:*` source; batch exports carry no atlas prose; the walk does not follow atlas
  path steps. The 216 atlas claims in dev and Matt's 7 answers on atlas quotes were deleted (his
  go). Every claim quotes a real page or a database. Also: `credited_on` means work on the music
  (playing, singing, writing, producing, engineering, mixing, mastering, arranging); artwork,
  photography, design, liner notes and business roles are not credits (Matt: "an album cover
  isn't really working on an album"); imports skip them and the 332 such claims in dev were
  deleted. And a song written by someone else is not a cover unless the source names who
  recorded it first.
- 2026-10-10: v6 (Matt). A critic's "RIYL" (recommended if you like) or "for fans of" line about an
  album is a `sounds_like` claim for each artist it names; the skill's extraction rules and reader
  prompt say so. The three P01 RIYL claims the first reader marked NOT_A_CLAIM were sent back
  to a reader under the new prompt; their `reader_first` keeps the first verdict.
- 2026-10-10: v7 (Matt). Four predicates join the vocabulary (21 in all): `toured_with`,
  `performs_as`, `renamed_from`, `interpolates` (lineage). `associated_with` covers working, playing
  and recording together only; its `touring` claims were re-filed: separate acts on one tour or bill
  became `toured_with`, a player in another artist's band stayed `associated_with` (kind `backing`),
  and every re-filed claim went back to a reader. The walk (section 11) does not follow
  `toured_with`.
- 2026-10-10: v8 (Matt). English pages only: `graph fetch` skips non-English Wikipedias and
  country domains (`.de`, `.fr`, `.it` and others), extractors skip any non-English page, and the
  18 claims quoting German pages (laut.de, plattentests.de, rollingstone.de) were deleted with
  Matt's go; 35 links to non-English sites were marked dead. Also from the P04 run: Rate Your
  Music links are dead (their cached pages often held the wrong album), and the normaliser reads
  link titles with escaped quotes.

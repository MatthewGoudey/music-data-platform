# Music graph: edge vocabulary, sources and the Crazy Horse pilot

- Status: **Proposed** — Matt marks this up; the checks in section 13 test claims without needing
  Matt to know the music. The research procedure is the `music-graph-research` skill; the build
  (schema, jobs, checks, API) is `docs/graph/GRAPH_SPEC.md`.
- Date: 2026-10-09. Scope: the claims graph that can map any album, starting with the Crazy
  Horse line of the V atlas. Sequencing: the pilot starts after the Phase 4 queue gate; this
  document and the golden set can be worked on meanwhile.
- Always use positive, controlled vocabulary: a new predicate, entity type or tag category is a
  reviewed change to this file.

## 1. The model in one paragraph

The graph stores **claims**, not facts: *subject — predicate — object*, each with a source, a
basis, a confidence, evidence and a review status. Entities are anchored on MusicBrainz IDs
(linked onward to Wikidata and Discogs) wherever they exist; lanes, scenes, maps, paths, lists and
tags get local IDs. Every album gets an automatic **baseline** from open databases the first time
it is touched (listening, a list, a map, or a lookup); **maps** such as the V add curated depth on
top. Three layers stay separate: structure (the map), opinion (lists and curators, i.e. sources)
and evidence (Matt's listening).

## 2. Entity types

| Type | Anchor | Notes |
| --- | --- | --- |
| `album` | MusicBrainz release group; points at the existing `release_group` row | the main subject of most claims |
| `recording`, `work` | MusicBrainz recording / work | track level: covers, samples, songwriting |
| `artist` | MusicBrainz artist; points at the existing `artist` row | person or group |
| `person` | MusicBrainz artist of type person | the same table as `artist`; used in credits |
| `label` | MusicBrainz label | |
| `place` | MusicBrainz place (studios, venues) | |
| `area` | MusicBrainz area (city, region, country) | |
| `scene` | local | a place plus a period plus a circle of people; may link to an `area` |
| `lane` | local, per map | e.g. C1 Indie twang; has parents |
| `map` | local | e.g. The V; defines its own coordinates |
| `path` | local, per map | an ordered listening route |
| `list` | local | a source of candidates (already in `list`) |
| `tag` | local, with a `category` | categories: instrument, voice, guitar, writing, mood, production, function, context |
| `genre` | MusicBrainz genre | the cross-map genre backbone; lanes map onto genres |

## 3. Claims

Every claim carries:

| Field | Meaning |
| --- | --- |
| `subject`, `predicate`, `object` | the edge; `object` may be an entity or a literal value |
| `qualifiers` | JSON: role, position, year, instrument, step, votes — whatever the predicate defines |
| `source` | `matt`, `map:<slug>`, `musicbrainz`, `wikidata`, `discogs`, `wikipedia`, `web:<domain>`, `atlas_matcher`, `firecrawl_json`, `claude` |
| `basis` | `documented` (a credit, a database, the artist's own statement), `reported` (a critic or publication says so), `inferred` (resemblance judged by a curator or extractor) |
| `confidence` | 0.0–1.0 |
| `evidence` | the exact supporting sentence (≤ 300 characters) or the database field, plus the source URL |
| `status` | `proposed` (loaded, unchecked), `unread` (checked, waiting for the reader), `accepted`, `rejected`, `ask_matt` (a reading question), `superseded` |
| `asserted_by`, `asserted_at`, `run_id` | who or what made the claim, when, in which job run |

**Precedence** for the current view of an edge: `matt` > `map:*` > documented database
(`musicbrainz`, `wikidata`, `discogs`) > `reported` text sources > `inferred` from a map >
`inferred` from an extractor.

**Rules**
- Always point lineage from the newer subject to the older object (the descendant is the subject).
- Always store `sounds_like` with basis `inferred`; store `influenced_by` only with `documented`
  or `reported` evidence, and downgrade anything weaker to `sounds_like`.
- Always attach evidence to a claim extracted from text; reject text claims that arrive without it.
- Always store a symmetric predicate once, with the lower entity id as subject.
- Always keep claims that disagree side by side; the current view picks by precedence, and both
  stay visible with their sources.

## 4. Predicates (17, grouped by facet)

| Facet | Predicate | Subject → object | Qualifiers | Typical sources |
| --- | --- | --- | --- | --- |
| Genre & style | `in_lane` | album → lane | `primary` or `secondary` | map |
| | `lane_parent` | lane → lane | | map |
| | `has_genre` | album, artist → genre | `votes` | musicbrainz, wikidata |
| Place & period | `from_scene` | album, artist → scene | | map, matt |
| | `based_in` | artist → area | `from`, `to` | musicbrainz |
| | `recorded_at` | album → place, area | `dates` | wikipedia (firecrawl_json), musicbrainz |
| People | `member_of` | artist (person) → artist (group) | `from`, `to`, `instrument` | musicbrainz, wikipedia |
| | `credited_on` | person → album, recording | `role` (producer, engineer, mixer, pedal steel, …) | wikipedia (firecrawl_json), discogs, musicbrainz |
| Label | `released_by` | album → label | `year`, `catalog` | musicbrainz (authoritative), map |
| Lineage | `influenced_by` | album, artist → album, artist | | interviews, wikipedia, wikidata (P737) |
| | `sounds_like` | album → album, artist | | map, atlas_matcher, matt |
| | `covers` | recording, album → work, recording, album | | musicbrainz, wikipedia |
| | `samples` | recording → recording | | musicbrainz |
| | `associated_with` | artist ↔ artist (symmetric) | `kind`: collaborator, touring, bandmate, label-mate, scene | map, wikipedia |
| | `path_next` | album → album | `path`, `step` | map |
| Sound, mood, function, context | `has_tag` | album → tag | | map, matt, musicbrainz tags via mapping |
| Source | `on_list` | album → list | `position`, `priority` | lists (already modelled as `list_entry`) |

Map coordinates (layer, zone, priority, start here) are not edges: they live in
`map_membership` (entity, map, coordinates JSON), because they only mean something inside a map.

## 5. Link registry

`entity_link`: `entity_id`, `kind`, `url`, `language`, `source`, `verified_at`, `status`.
Kinds: `wikipedia`, `wikidata`, `musicbrainz`, `discogs`, `allmusic`, `bandcamp`, `official`,
`label_page`, `interview`, `review`, `live_session`, `streaming`, `liner_notes`.
MusicBrainz URL relations and Wikidata sitelinks fill most of it during the baseline import.
Always fetch from a registered link before searching: an unknown or guessed URL that returns an
error still costs a credit.

## 6. Source rulebook

| Source | Fetch with | Use it for | Use it as |
| --- | --- | --- | --- |
| MusicBrainz | its API, 1 request/second, real User-Agent | identity, credits, labels, areas, membership, covers, samples, URL links | facts (CC0) |
| Wikidata | SPARQL in batches | genre, influenced by (P737), country, movement | facts (CC0) |
| Discogs | its API (60 requests/minute) via IDs from MusicBrainz | styles, detailed credits | facts (dumps are CC0) |
| Wikipedia | Firecrawl: JSON extraction (5 credits/page) for credits and recording facts; plain scrape (1 credit/page) cached for Claude to read | producers, personnel and roles, studios, dates; documented influences | facts plus link; always summarise, quoting at most a short sentence (CC BY-SA) |
| Reviews, interviews, features | Firecrawl plain scrape (1 credit/page), cached; Claude extracts | documented influences, recording stories | ≤ 300-character evidence quote plus link (copyrighted) |
| Album of the Year | search snippets only | pointers | their terms forbid bots |
| The atlas | `seeds/atlas/` | lanes, scenes, tags, paths, lineage prose | map claims |

**What the 2026-10-09 Firecrawl test showed** (Manning Fireworks, Everybody Knows This Is
Nowhere, Zuma; 15 credits): credits, roles, producers, studios and dates came back correct for
all three. Lineage and label did not: the label field held the album title, a Rolling Stone 500
ranking came back as `influenced_by`, and a cover *of* the album came back as the album covering
someone. So:
- always take labels from MusicBrainz;
- always extract credits and recording facts with Firecrawl JSON extraction (it runs unattended);
- always extract lineage with Claude, reading the atlas prose and the cached page text, with an
  explicit `direction` for every claim (`this album → other` or `other → this album`) and lists or
  rankings routed to `on_list`; every extracted lineage claim goes through the checks in section 13.

**What the second test showed** (run T1, 2026-10-09: Boat Songs, Crazy Horse (1971), Keeper; 12
credits):
- One Firecrawl call with `formats: markdown,json` returns the page text and the facts JSON for 5
  credits, so a Wikipedia album page is fetched once, never as a plain scrape plus an extraction.
- Asked for "songs by other writers", Firecrawl JSON again returned later covers *of* the album's
  songs: covers stay with Claude, and the facts schema (`scripts/graph/facts_v2.schema.json`) has no
  covers field.
- A whole Bandcamp page is mostly fan names; `--include-tags` on the about, credits, tags and
  location blocks returns about 700 characters with everything useful.
- Discogs (free API, 25 requests/minute without a token) carries credits MusicBrainz lacks for new
  indie records: Boat Songs has 1 MusicBrainz credit and 16 Discogs credits.
- MusicBrainz works list every recording of a song with its first release date, which settles who
  covered whom: "Gone Dead Train" was first released by Randy Newman (1970), a year before Crazy
  Horse, so a later cover's object is Newman's recording.
- An atlas note that lists a Wikipedia page in `source_urls` repeats that page; the two count as one
  source.

**Who does what:** Firecrawl does all web search and fetching. Claude does the reading where
judgement matters (lineage, direction, whether a sentence is a claim at all), in batch Claude Code
sessions on Matt's plan; Matt has said plan usage is fine wherever Claude is the best tool. The rule
matcher stays as a cheap baseline and as an entity-name dictionary for linking.

**Budget:** an album costs about 5 credits with a Wikipedia page (text and facts in one call), 1
for a Bandcamp-only deep cut, and up to 35 for an Essential album that also gets reviews and
interviews. The pilot (181 albums, plus about 100 plain artist pages) comes to roughly 1,000–1,200
credits; the whole V (2,942 albums, 1,201 artists) to roughly 15,000–18,000, spread over waves.
MusicBrainz, Wikidata and Discogs cost nothing.

### 6a. Firecrawl budget policy for the music graph

Two different callers, two rule sets:

**The pipeline (code in `musicdata`, run by GitHub Actions or Fly):**
- Always read the key from the `FIRECRAWL_API_KEY` secret in each GitHub Environment (the Fly API
  fetches nothing, so it needs no key).
- Always scrape registered links only (section 5); batch jobs run no web searches.
- Always cache each result by `(url, schema_version)` in a `source_fetch` table (URL, schema
  version, fetched_at, credits_used, JSON result), and reuse it: the pipeline pays for a page once
  per schema version.
- Always stop a run at `--max-credits` (default 500) using the `creditsUsed` the API returns, and
  record credits spent in the run's `pipeline_run.notes`.
- Monthly ceiling for the music graph: a number Matt sets (suggested 15,000 of the 100,000 a
  month), checked before each run against the month's recorded spend.

**Interactive sessions (an "album companion" or research in a Claude chat):**
- A per-album cap: about 35 credits (up to 4 page extractions and 2 searches), set by the brief's
  budget hint.
- Write-back to the API instead of local research files, so findings land in the graph.
- These rules, the reading rules and the reader prompt live in the `music-graph-research` skill
  (drafted 2026-10-09). Until the pipeline jobs exist, Claude Code sessions run the same procedure
  with `scripts/graph/` and its `fetch.py` enforces the cache, the run cap and the no-retry rule.
  The general Firecrawl research skill stays as it is for other projects.

## 7. Research-question templates

One template per gap or weak edge; a brief turns them into concrete tasks.

| Trigger | Question | Preferred sources | Search pattern |
| --- | --- | --- | --- |
| `sounds_like` with no documented support | "Is there an interview or feature where {artist} talks about {object}?" | interview, feature | `"{artist}" interview "{object}"` |
| no `credited_on` producer | "Who produced, engineered and played on {album}?" | wikipedia, discogs, liner_notes | registered links first |
| no `recorded_at` | "Where and when was {album} recorded?" | wikipedia, label_page | registered links first |
| `from_scene` with no context | "What was happening in {scene} around {year}; who else recorded there?" | feature, label_page | `"{scene}" {year} scene` |
| `path_next` | "How does {album} lead to {next}: shared people, studio, label, or a stylistic thread?" | the graph first, then features | graph traversal |
| `covers` or `samples` missing on a known standard | "Which songs are covers, and of whom?" | musicbrainz, wikipedia | registered links |

## 8. The album brief

`GET /albums/{id}/brief` returns one packet for an AI to work from: identity (names, year, IDs,
tracklist with recording IDs and lengths); where it sits (map, layer, zone, lane and lane parents,
priority, start here); edges grouped by facet with source and basis; registered links; gaps;
research questions generated from section 7; Matt's context (heard or not, sessions, tags, why it
is in the queue); and a budget hint (pages, searches, credits).

## 9. The companion outline (a shape, not a document)

At a glance · Listen for (from sound tags) · Track notes (credits, covers, samples) · Who made it ·
Where it comes from (lineage, previous path step) · Where it leads (descendants, next path step,
related albums) · Scene and place · Further reading (links). Each section names the edges that feed
it and the research questions that fill it. An "album companion" skill in the Project calls the
brief, works its questions with Firecrawl inside the budget hint, writes back, and fills the
outline.

## 10. Write-back

Every research run posts what it found as `proposed` claims with evidence (`POST /assertions`)
and new links (`POST /links`). Fetched pages stay cached by URL, so a second companion for the same
album costs almost nothing.

## 11. Pipelines

| Job | Does | Writes |
| --- | --- | --- |
| `graph import` | MusicBrainz (and later Wikidata) baseline for albums in priority order: maps, then listening, then everything else | claims with `documented` basis, links |
| `graph fetch` | Firecrawl fetch of registered links into the `source_fetch` cache: Wikipedia album pages as text plus facts JSON in one call (5 credits), other pages plain (1 credit), unattended | cached page text and facts |
| `graph extract --source facts` | the cached facts JSON plus MusicBrainz and Discogs credits, studios, labels and styles (`scripts/graph/facts_to_claims.py` today) | `proposed` claims with evidence |
| `graph extract --source lineage` | a Claude Code batch session (20–25 albums per session) following the `music-graph-research` skill: reading atlas prose and cached page text against this vocabulary; output as JSONL, loaded with `graph load-claims` | `proposed` claims with evidence and direction |
| `graph verify` | the checks in section 13 plus the independent reader's verdicts (`scripts/graph/verify.py` today) | status and confidence per claim, reading questions |
| `graph extract --source atlas-rules` | the rule-based matcher: baseline scores and the entity-name dictionary | `proposed` claims with evidence |
| `graph review` | batch accept / reject, most-connected and lowest-confidence first | status changes |
| `graph derive` | inherited lanes, bridge albums, importance within a map, coverage against listening | derived views |

## 12. The pilot: the Crazy Horse line

- **Slice:** every album whose primary lane is C1 Indie twang, V1 Molina and the Magnolia circle,
  or V20 Neil Young and the Crazy Horse universe, plus every album on the path *MJ Lenderman to
  Neil Young: the Crazy Horse line* — 181 albums from 1966 to 2026, Core to V
  (`docs/graph/pilot_crazy_horse_albums.csv`).
- **Atlas matcher on the slice:** 223 candidate claims from 136 of the 181 albums. The prose leans
  on resemblance ("sounds indebted to"), so the matcher finds `sounds_like` and `associated_with`
  readily and found no `influenced_by`: documented influence has to come from interviews and
  Wikipedia.

| Step | What | Done when |
| --- | --- | --- |
| M0 | this vocabulary marked up by Matt; the skill and `scripts/graph/` tested on three albums (run T1) | Matt has read sections 3–4 and agreed or changed them |
| M1 | schema (`entity`, `predicate`, `assertion`, `map_membership`, `entity_link`); MusicBrainz and Discogs baseline and links for the 181 | a coverage report per facet |
| M2 | the skill run over the slice in batches: fetch, facts, Claude reading, checks, independent reader | every predicate type with ≥ 10 text claims at ≥ 90% SUPPORTS on the reader's first verdict; Matt has answered the reading questions |
| M3 | `/entities/{id}`, `/neighbors`, `/paths`, `/albums/{id}/brief`; a "walk back from here" profile in the queue (a generated list `graph_walk:<album>`) | Matt uses it for a week |

**Run T1** (2026-10-09; Boat Songs, Crazy Horse (1971), Keeper): 126 claims, 125 accepted and 1
reading question after one round of fixes; 12 Firecrawl credits. The first reader pass flagged 19 of
28 text claims, almost all for evidence clipped too short to name both ends, and one real error (a
Nazareth cover pointed at Crazy Horse's recording instead of Randy Newman's earlier one). Both led
to rules in the skill. Keeper, a deep cut with no Wikipedia page and no Bandcamp credits, yields
identity, area and atlas prose only: that is the expected depth for deep cuts.

## 13. Checking claims without knowing the music

Matt knows the home genre's shape, not every artist in it, so no step depends on him judging whether
a claim is musically true. A claim records what a source says; these checks decide whether the
source says it and whether anything contradicts it:

| Check | How |
| --- | --- |
| Structure | the predicate exists, the subject and object types fit it, the basis rules in section 3 hold |
| Verbatim evidence | the quote appears on the cached page or in the atlas field; Firecrawl-JSON names appear on their page |
| Dates | lineage points from newer to older, by MusicBrainz first-release and begin dates |
| First recording | a `covers` object is the earliest MusicBrainz recording of the song |
| Databases | MusicBrainz and Discogs record the same credit, membership, label, studio or cover order |
| Independence | an atlas note built from a page counts as that page, not as a second source |
| Independent reader | a separate agent, with no extraction context, judges each text claim against its quote alone |

Confidence starts from the source and basis and rises 0.1 per independent agreeing source (up to
0.95). A claim the reader finds weaker than claimed ("closer to soft rock" filed as soft rock), with
no database to settle it, becomes a **reading question** for Matt: the quote and the claim, answered
yes, no or skip from the quote alone.

### The golden set

`docs/graph/golden_set_crazy_horse.csv`: 50 claims — 38 from the atlas matcher (sounds like 12,
associated with 16, member of 4, released by 3, follows 2, covers 1) and 12 from Firecrawl's
Wikipedia extraction (credited on 5, recorded at 2, associated with 2, released by, influenced by,
covers). Some are wrong on purpose of the extractor, not of the sampling: they show where each
extractor fails.

It is now a record of the first extractor test (atlas matcher against Firecrawl JSON). The
first-pass reader measure in `docs/graph/GRAPH_SPEC.md` (Block E) replaces it as the quality bar.
The original judging instructions, kept for anyone who does know the music:
- `verdict`: `correct`, `wrong_predicate`, `wrong_object`, `wrong_direction`, `not_a_claim`, or `unsure`.
- `fix`: when it is wrong, the right predicate or object (e.g. `on_list`, `Neil Young member_of Buffalo Springfield`).
- `note`: anything else.
- Missed claims: add a row with `extractor` = `matt` for any claim the sentence makes that no row
  caught. These rows measure recall.

Judged that way, the set gives precision per extractor and per predicate, and recall on the
sentences judged.

## Changes to this document

- 2026-10-09: first draft (Matt, via Claude), with the brief, link registry, source rulebook and
  research templates from the "read while listening" discussion, and the Firecrawl test results.
- 2026-10-09: division of labour settled with Matt: Firecrawl for all web search and fetching,
  Claude (plan usage) for lineage reading in batch sessions, Firecrawl JSON only for credits and
  recording facts; budget policy added (section 6a).
- 2026-10-09: the `music-graph-research` skill and `scripts/graph/` tested on three albums (run T1);
  Discogs added as a free credits source; checks that need no music knowledge replace Matt judging
  the golden set (section 13); milestones M0 and M2 updated; budget re-estimated (section 6).
- 2026-10-09: statuses `unread` and `ask_matt` added (section 3), as used by the checks; the build
  moved to `docs/graph/GRAPH_SPEC.md`; the golden set became a record, replaced by the first-pass
  reader measure; Firecrawl secrets live in GitHub Environments only.
- Proposed, for Matt: a predicate for a band's earlier name or predecessor band (Crazy Horse
  recorded in 1968 as The Rockets), e.g. `continues` (artist → artist, `from` year). Until agreed,
  such sentences go to the skipped log.

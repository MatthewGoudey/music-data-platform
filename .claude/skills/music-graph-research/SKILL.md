---
name: "music-graph-research"
description: "Use when researching albums or artists for Matt's music graph in music-data-platform: claims (credits, covers, memberships, lineage) for the Crazy Horse pilot or any album batch, or one album with sources."
---

# Music graph research

This skill turns albums into **claims** for Matt's music graph: *subject — predicate — object*, each with a
source, a basis, verbatim evidence and a confidence. The vocabulary is `docs/graph/edge-vocabulary.md`
(sections 3–4) and the build is `docs/graph/GRAPH_SPEC.md` in the `music-data-platform` repo; read the
vocabulary before the first album of a session.

A claim is an attributed statement: "this source says X — predicate — Y". A claim is accurate when its
source says it and the checks in section 4 pass; how much it matters is its confidence. Matt never has to
know the music to approve a claim. He answers only **reading questions** ("does this quote say X?") for the
few claims the checks cannot settle.

**The atlas is AI-written** (graph spec v5). It sets scope only: which albums and artists a batch covers
and which pages to read. It is never the source of a claim: every claim quotes a real page, and
`load-claims` refuses a `map:*` source.

Division of labour:
- **MusicBrainz, Wikidata, Discogs** (free APIs): identity, links, labels, credits, studios, memberships,
  songwriters, every recording of a song with its date. `musicdata graph import` runs these first.
- **Firecrawl**: every web page fetch and every web search. Never fetch a page any other way.
- **Claude**: reading pages for lineage, covers and relationships, and writing the claims.
- **A separate reader agent**: checks every text claim against its quote, without the extraction context.

Tested on 2026-10-09 with Boat Songs, Crazy Horse (1971) and Keeper (run T1): 126 claims, 125 accepted, 1
reading question, 12 Firecrawl credits. The run is the golden test in `tests/unit/fixtures/graph/T1/`.

## 1. Setup

Work from the repo root. Every step is a `musicdata` command against prod, the graph's home since
2026-10-10: `uv run musicdata --env prod --plain-logs graph …`. Claims, pages and checks live in the
database; a reading batch's files live in `data/graph/batches/LABEL/` (gitignored):

| Path | Holds |
| --- | --- |
| `albums.csv` | the batch's albums: `atlas_id,artist,album,year,priority,entity_id` |
| `pages/FETCH_ID.md` | each fetched page, full text, with a header (url, title, atlas id) |
| `cues/FETCH_ID.md` | the paragraphs of each page that can carry lineage, numbered `[¶n]` |
| `atlas/ATLAS_ID.json` | the pages the atlas links to (`source_urls`); scope only, never evidence |
| `known.json` | entities already linked to each album (credits, members, label, places), for names |
| `claims/` | the claim files written in step E, and the skipped file |
| `reader_input.txt`, `reader_output.jsonl`, `report.md` | the reader's input and verdicts, the batch report |

Firecrawl:
- The key is `FIRECRAWL_API_KEY` (the repo's `.env`, and the GitHub Environment secrets). Never print it,
  paste it, or write it into a file; `musicdata` redacts any `fc-…` token in errors and logs.
- Budgets (graph spec section 9): 500 credits per run (`graph fetch --max-credits`), a monthly ceiling of
  `GRAPH_MONTHLY_CREDITS` (100,000, the plan's limit; the ledger counts graph fetches only), about 35 per
  album in a single-album session, and ask Matt before a slice above 1,500 credits.
- In a chat without the repo, use the Firecrawl connector (`firecrawl_scrape`, `onlyMainContent: true`, with
  `formats: ["markdown", "json"]` and the schema in `src/musicdata/graph/schemas/facts_v2.json` for a
  Wikipedia album page), save the result to a file at once, and keep to single albums.

Free APIs: the clients send a real User-Agent, keep MusicBrainz at 1 request/second and Discogs under its
limit, follow MusicBrainz redirects (merged IDs), and back off on 503 and 429.

## 2. Per slice, then per batch

Steps A–D run once per slice as jobs (laptop or the `backfill` workflow); they are resumable and skip
albums already done.

**A. Baseline (free).** `graph import --slice SLICE`, then `graph link`. Albums with no MusicBrainz match
appear under "No match" in the report.

**B–C. Fetch (Firecrawl).** `graph fetch --slice SLICE --max-credits 500` picks pages by atlas priority from
registered links only:

| Atlas priority | Pages |
| --- | --- |
| Essential, or `start_here` / on a path | English Wikipedia (`facts`) + up to 2 other registered links |
| Recommended | Wikipedia + 1 |
| Deep cut | 1: Wikipedia, else Bandcamp |

The album's own Wikipedia page (from MusicBrainz or Wikidata) comes first; a page an atlas note cites feeds
facts mode only when its title names the album. Modes: `facts` (text plus facts JSON, 5 credits), `bandcamp`
(the about, credits, tags and location blocks, 1 credit), `plain` (1 credit). A cached page costs nothing; a
failed page is logged and never retried in the run. Batch jobs run no web searches.

**D. Facts to claims (free).** `graph facts --slice SLICE` writes the Firecrawl-JSON claims (credits and
studios); the baseline already wrote MusicBrainz and Discogs claims. `graph verify --slice SLICE` checks
them all.

**E. Export a batch and read (Claude).** `graph batch export --slice SLICE --size 25` creates the next batch
(`P01`, `P02`…). For each album, read its `cues/` paragraphs (the full `pages/` text when a cue needs
context), then write claims by section 3 into `claims/LABEL_lineage.jsonl`, and
everything read but not claimed into `claims/LABEL_skipped.jsonl` (one `{"album", "text", "reason"}` object
per line). Check every quote first, until it reports 0 problems:
`graph batch check-quotes data/graph/batches/LABEL data/graph/batches/LABEL/claims/FILE.jsonl` (no database).
Then load them: `graph batch load-claims LABEL data/graph/batches/LABEL/claims/LABEL_lineage*.jsonl`
(names resolve to entities: those in `known.json` first, then MusicBrainz).

## 3. Reading rules

Write one JSON object per line:

```json
{"claim_id": "P01-A2184-L001", "run_id": "P01", "album": "A2184",
 "subject": {"type": "recording", "name": "Rod Stewart – I Don't Want to Talk About It", "artist": "Rod Stewart", "year": 1975, "album": "Atlantic Crossing"},
 "predicate": "covers",
 "object": {"type": "recording", "name": "Crazy Horse – I Don't Want to Talk About It", "artist": "Crazy Horse", "year": 1971, "album": "Crazy Horse"},
 "qualifiers": {"work": "I Don't Want to Talk About It", "writer": "Danny Whitten"},
 "source": "wikipedia", "basis": "reported",
 "evidence": "Whitten's ballad \" I Don't Want to Talk About It\" would be covered by a variety of artists, including Rita Coolidge; Everything but the Girl on their 1988 album Idlewild; and Rod Stewart, who had a chart-topping hit with the song in the United Kingdom, taken from his 1975 album Atlantic Crossing.",
 "source_url": "https://en.wikipedia.org/wiki/Crazy_Horse_(album)", "direction": "subject_newer",
 "extractor": "claude", "status": "proposed", "asserted_by": "music-graph-research", "asserted_at": "2026-10-09"}
```

Entity types: `album`, `recording`, `work`, `artist` (a group, or a joint credit as written), `person`,
`label`, `place`, `area`, `genre`. Give `year` and `artist` wherever the source states them; give the
batch's album its `atlas_id` (`{"type": "album", "name": "ARTIST – ALBUM", "atlas_id": "A0015"}`). Sources:
`wikipedia` (any page: a non-Wikipedia URL is stored as `web:DOMAIN`) or `web:DOMAIN`. Never the atlas. Use the page's URL from its header.
Put the publication or critic a source cites in `qualifiers.via`. A corrected claim carries
`"replaces": "OLD_CLAIM_ID"` and a new claim id.

**Evidence**
- English pages are always accepted (spec v8). Read another language only for artists tied to it: for
  English-language artists, skip any non-English page and log it as a skip. (A slice of non-English
  music reads English plus its own language, and its non-English reading questions carry an English
  translation for Matt.)
- Always copy evidence verbatim from the cached page, at most 300 characters.
- Always quote the clause that names both ends of the claim. When they sit in two sentences of one source,
  join the passages with " … ". On a Wikipedia album article, "the album" and "the band" may stand for the
  album and its artist; anything else must be named in the quote.
- For a structured page field, write `field: FIELD NAME = VALUE` (for example
  `field: Bandcamp artist location = Ohio`).

**Predicates**
- `sounds_like` (basis `inferred`): a curator or critic compares the album to, says it resembles, is
  indebted to, or builds on another artist or album. One claim per object. A critic's "RIYL" (recommended
  if you like) or "for fans of" line about the album counts: one claim per artist named (Matt, spec v6).
- `influenced_by` (basis `reported` or `documented`): only when the source says influence or inspiration in
  so many words, or quotes the artist saying it. A critic's comparison is `sounds_like`.
- `covers` (basis `reported`): the subject is the later recording, the object is the **first** recording of
  the song. Never assume the album's own recording came first: when the source names an earlier performer,
  that performer is the object (Nazareth's "Gone Dead Train" covers Randy Newman's 1970 recording, not Crazy
  Horse's). Covers *of* this album's songs by later artists are claims too, with the later recording as subject.
  A song written by someone else is not a cover by itself: claim `covers` only when the source names who
  recorded or performed it first; a writer alone is a skip.
- `credited_on`: a person or band played, sang, wrote, produced, engineered, mixed, mastered or arranged a
  named album; use `role`. Artwork, photography, design, layout, liner notes and business roles
  (management, A&R) are not credits: skip them (Matt: "an album cover isn't really working on an album").
- `member_of`: the source says the person was in, joined, or was a member of the group.
- `associated_with`: two artists worked, played or recorded together, including one playing in the other's
  band; give `kind` (collaborator, bandmate, backing). Touring or sharing a bill is not working together.
- `toured_with`: two acts toured together or shared a bill as separate acts (opened for, supported,
  co-headlined); give `role` (opener, support, co-headliner) when stated.
- `performs_as`: a person records or performs under a stage name or solo project (Katie Crutchfield →
  Waxahatchee; Zack James → Dari Bay).
- `renamed_from`: a band or project was earlier known by another name; the newer name is the subject (Crazy
  Horse → The Rockets; Dinosaur Jr. → Dinosaur).
- `interpolates` (lineage, `direction: subject_newer`): a song borrows the melody or lyric of an older song
  ("Borrowed Tune" → "Lady Jane"); the subject is the newer recording (or the album, with `track`).
- `tribute_to`: the album or song pays tribute or homage to an artist, writer or work (a tribute album, a
  song written for or about someone, "a tip of the hat to"); give `kind` (tribute album, song, homage).
- `named_after`: the album, song or band takes its name from an older song, work, album or artist (Didn't
  It Rain → Mahalia Jackson's song; a title parodying a slogan names the slogan as a `work`).
- `references`: a lyric or album names or quotes an older song, album or artist without borrowing its
  melody ("Hippie Dream" names "Wooden Ships"); a borrowed melody or lyric is `interpolates`.
- `based_on`: a song or album adapts a poem, story or film, or sets another writer's words to music (a
  song drawn from a James Wright poem; New Multitudes setting Woody Guthrie's lyrics); the object is a
  `work` with `kind` (poem, story, lyrics).
- `appears_in`: a song or album is used in a film, TV episode, advert or game; the object is a `work`
  named as the source names it, with `kind` (film, tv, advert, game) and `year`.
- `arrangement_from`: a recording follows another performer's arrangement of a song someone else recorded
  first ("Oh Susannah" after Tim Rose and the Thorns); the first recording itself is `covers`.
- `compiles`: an album collects earlier releases (two EPs issued as one LP).
- `companion_to`: an album accompanies a film, book or show of its own (Year of the Horse and its film);
  the object is a `work` with `kind`.
- `renamed_from` also covers a studio or label's earlier name.
- `relative_of`: two people are family; give `relation` (brother, great-grandfather, …).
- `influenced_by` may name a genre as its object when the source says the album or artist drew on it
  ("informed by krautrock"); a channel or medium (MTV) is a skip.
- `has_genre` and `from_scene`: only when the source says the album *is* that style or *came out of* that
  scene; "closer to", "nods to" and "hints of" are skipped.
- A ranking or list placement routes to `on_list`, never to lineage.

**Direction:** the descendant is always the subject (`direction: "subject_newer"` on every lineage claim).

**Skip, and log the reason in the skipped file:** objects that are not one entity (a label's roster, a decade,
a mood); the same artist's earlier or later records (discography order comes from MusicBrainz); mentions with
no named album or artist; and relations the vocabulary lacks (log these as `vocabulary gap: …`, for example a
band's earlier name). Never bend a relation into the nearest predicate.

## 4. Checks and the independent reader

1. `graph verify --batch LABEL`
2. `graph batch reader-input LABEL` writes `reader_input.txt` (every text claim that passed the checks).
3. Launch a **separate** agent (never the one that extracted) with the reader prompt below on
   `reader_input.txt`; it writes `reader_output.jsonl`. About 30 claims per reader agent.
   Then `graph batch load-reader LABEL data/graph/batches/LABEL/reader_output.jsonl` (a claim's first verdict
   stays in `reader_first`, the M2 measure).
4. Fix what the reader flagged where the fault is in the extraction (a clipped quote, the wrong object): write
   the corrected claims with `replaces`, load them, run `graph batch reader-input LABEL` again, and send just
   the revised lines to the same reader; load its verdicts.
5. `graph verify --batch LABEL`, then `graph report --batch LABEL` (also written to `report.md`).

Reader prompt (send verbatim, filling RUN_DIR):

```
You are an independent reader checking extracted claims against their evidence quotes. You have not seen how
they were extracted. Read RUN_DIR/reader_input.txt. Each line has a claim ID, a CLAIM in plain words, an
EVIDENCE quote (" … " joins two passages from the same source), and the SOURCE the quote comes from.

Judge ONLY whether the quote, read on its source, states the claim. Use no outside knowledge of music,
artists or dates: a claim you believe is true but the quote does not state is not supported. Dates in the
CLAIM that the quote does not mention are context, not part of the judgement, unless the quote contradicts them.

References the source resolves count as stated: on a Wikipedia article about an album, "the album", "this
album", "the band" (the album's artist) and the album's own songs refer to that album and its artist.

What each relation means:
- "compared to, or said to sound like or build on": the quote says the album resembles, is compared with, is
  indebted to, or builds on the other artist's sound. A critic's "RIYL" (recommended if you like) or "for
  fans of" line about the album counts as this comparison. A bare co-mention is not enough.
- "influenced or inspired by": the quote says influence or inspiration, not just resemblance.
- "is a cover of X: a later recording of a song that X recorded first": the quote says the later artist
  covered, recorded or performed the song, and that X recorded or performed it earlier.
- "worked or played on": the quote says the person or band played, sang, wrote, produced, engineered,
  mixed, mastered, backed or arranged that album. Artwork, photography, design and business work do not count.
- "was a member of": the quote says the person was in, joined, or was a member of the group.
- "worked with (kind)": the quote says they worked, played, or recorded together (one playing in the
  other's band counts). Touring together or sharing a bill does not.
- "toured with or shared a bill with": the quote says they toured together, one opened for or supported
  the other, or they played the same bill.
- "records or performs under the name": the quote says the person performs as, records as, or is the
  project named.
- "was earlier known as": the quote says the group or project had the other name before.
- "borrows the melody or lyric of": the quote says the song borrows, adapts, interpolates or is set to the
  other song's melody or words.
- "pays tribute or homage to": the quote says the album or song is a tribute or homage to, written for,
  or tips its hat to the other.
- "is named after": the quote says the title or name comes from, is taken from, or refers to the other.
- "names or quotes": the quote says the lyric or album names, mentions or quotes the other.
- "is based on, adapts or sets the words of": the quote says the song or album is drawn from, adapted
  from, based on, or sets the words of the other work.
- "was used in": the quote says the song or album was used, featured or played in the film, show,
  advert or game.
- "is a relative of": the quote states the family relation.
- "follows the arrangement of": the quote says the recording takes, copies or follows the other
  performer's arrangement or version.
- "collects": the quote says the album combines, collects or compiles the other releases.
- "accompanies": the quote says the album is the soundtrack or companion of the film, book or show.
- "is described as" / "came out of": the quote says the album is that style or came from that scene, not
  merely near it.

Verdicts:
- SUPPORTS: the quote states the claim.
- PARTIAL: the quote states only part of it, or something weaker (implies, hints, "closer to"); say which.
- WRONG_DIRECTION: the quote states the relation the other way round.
- DOES_NOT_SUPPORT: the quote does not state it.
- NOT_A_CLAIM: the quote is a list, ranking, hedge, or otherwise not an assertion of this relation.

Write RUN_DIR/reader_output.jsonl, one JSON object per line:
{"claim_id": "...", "verdict": "...", "reason": "one short sentence"}.
Reply with only the counts per verdict and the IDs of every verdict other than SUPPORTS, each with its reason.
```

What `graph verify` checks (graph spec section 8), none of which needs music knowledge:

| Check | Passes when | On failure |
| --- | --- | --- |
| Structure | the predicate is in the vocabulary and the subject and object types fit it; `sounds_like` is `inferred`; `influenced_by` is `reported` or `documented`; lineage carries a direction | rejected |
| Evidence | the quote appears verbatim on the cached page; a `field:` value appears on the page; a Firecrawl-JSON name appears on its page (quote marks aside; a place may drop a trailing "Studio(s)") | rejected |
| Dates | for lineage, the object's first release (or the artist's start) is not later than the subject's | rejected |
| First recording | for `covers`, no MusicBrainz recording of the song predates the object's | rejected |
| Databases | MusicBrainz or Discogs records the same credit, membership, label, studio, or the cover order | adds support; a contradiction rejects |
| Independence | claims from one page count once | no double counting |
| Reader | SUPPORTS | DOES_NOT_SUPPORT, WRONG_DIRECTION and NOT_A_CLAIM reject; PARTIAL becomes a reading question unless a database records the same edge |

Confidence starts by source and basis — databases 0.9; Firecrawl JSON from a Wikipedia personnel list 0.75;
a page's own statement (`documented`) 0.8; `reported` text 0.7; a critic's comparison 0.5 — and gains 0.1
per independent source that agrees, up to 0.95. Where sources disagree, the current view of an edge follows
Matt, then the databases, then pages.

Statuses: `accepted` (the source says it and every check passed), `rejected` (a check failed),
`ask_matt` (a reading question), `unread` (the reader has not judged it yet).

## 5. Finish a batch

- `musicdata --env prod dq`: checks G1–G8 green (G4: every accepted Claude claim has a reader SUPPORTS, or a
  PARTIAL backed by a database; G8: the month's credits under the ceiling).
- Send Matt the report's reading questions as they appear in `report.md`: the quote, the claim, yes / no /
  skip. Record his answers as `matt` claims (`accepted` or `rejected`) with the quote as evidence; once the
  graph API exists, `POST /graph/questions/{assertion_id}/answer` does this.
- Add any `vocabulary gap` lines from the skipped log to the changes section of `edge-vocabulary.md` as
  proposals for Matt; the vocabulary changes only when he agrees.
- Close with the report's counts, the first-pass reader SUPPORTS rate per predicate (the bar is 90%), the
  credits spent, the reading questions, and the gaps.

## 6. Batches

- 20–25 albums per batch. For more than 10 albums, split the reading across extractor agents of 5 albums
  each, in waves of up to 5 agents; each writes its own `claims/LABEL_lineage_N.jsonl` and skipped file.
- The reader is always a different agent from every extractor, about 30 claims per reader.
- Re-reading a batch costs nothing for pages already fetched; after a rule change, re-run `graph verify`.

## 7. One album for listening

For "tell me about this album while I listen": run the slice steps for `--slice album:RELEASE_GROUP_ID`,
then steps E and section 4 for that album inside its 35-credit cap, then answer from the accepted claims and
the cached pages only, in the companion outline's order (at a glance; listen for; who made it; where it
comes from; where it leads; scene and place; further reading), citing each source URL. Every fact in the
answer traces to a claim or a cached page.

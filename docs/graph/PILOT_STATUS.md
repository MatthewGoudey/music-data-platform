# Music graph pilot: status (2026-10-10)

This file is the current state of the music graph (Phases 5 and 6) of `music-data-platform`, written for
Matt's claude.ai Project. Read it to answer questions about the graph pilot; read
`docs/graph/GRAPH_SPEC.md` (how the graph is built) and `docs/graph/edge-vocabulary.md` (what a
claim is) for the rules themselves. The graph lives in prod since 2026-10-10 (Phase 6 Block B);
dev keeps a copy for development.

## 1. What the graph is

A store of **claims** about the albums in Matt's listening world: who played on and produced each
record, where it was recorded, its label, band memberships, and lineage (influences, comparisons,
covers, borrowed melodies, tours). Every claim keeps:

- its **source** (MusicBrainz, Discogs, Wikidata, a Wikipedia page, a review, Bandcamp, or Matt);
- **verbatim evidence**: a quote of at most 300 characters from that page, or a database field;
- the results of automatic **checks**, a **confidence** (0–1), and a **status**: `accepted`,
  `rejected`, `ask_matt` (a reading question for Matt), or `superseded` (replaced by a correction).

Matt never judges whether music claims are true. He answers only **reading questions**: "does
this quote say X?", yes / no / skip, from the quote alone. His answers become claims under his
name (`matt`, confidence 1.0) that outrank every other source.

The point (Phase 6, `docs/graph/COMPANION_SPEC.md`): an API that answers "what is this record
connected to?"; album and hub pages; liner notes and deep dives written on request; and a queue
that the graph shapes — connection lines on cards, a small score factor, a thread slot after a
finished album, and follow and walk-back profiles.

## 2. The pilot

The pilot is the **Crazy Horse line**: 181 albums chosen from the V atlas (lanes C1, V1, V20 and
the path "MJ Lenderman to Neil Young"), from MJ Lenderman, Wednesday, Greg Freeman, Ratboys,
Florry and the Asheville/Burlington scenes back through Songs: Ohia, Magnolia Electric Co.,
Centro-matic and Will Johnson to Neil Young, Crazy Horse, Buffalo Springfield and CSNY.

- 169 of the 181 have a MusicBrainz match and are in the pilot; 12 small releases have none.
- Albums are read in **batches of 25** (P01–P07).

## 3. Rules Matt set during the pilot (graph spec v2–v12)

| Version | Rule |
| --- | --- |
| v2 | A name scraped from a Wikipedia page passes the name-on-page check with or without quote marks, and a studio passes without a trailing "Studio(s)". |
| v3 | The Firecrawl budget is 100,000 credits a month (the plan's real limit). Each run stops at 500 credits; a slice above 1,500 credits waits for Matt. |
| v4, v5 | **The atlas is AI-written, so it sets scope only**: which albums and artists, their priority, and pages to look at. It is never the source of a claim. Every claim quotes a real page or comes from a database. 224 atlas-sourced claims and Matt's 7 answers on atlas quotes were deleted. |
| v5 | **"Worked on" means work on the music**: playing, singing, writing, producing, engineering, mixing, mastering, arranging. Artwork, photography, design, liner notes and business roles are not credits (346 such claims were deleted, and imports skip them). |
| v5 | **Writing a song is not recording it first.** A cover claim needs the source to name who recorded the song earlier. |
| v6 | A critic's **"RIYL" or "for fans of"** line about an album counts as "sounds like" for each artist named. |
| v7 | **Touring is not working together.** `associated_with` means worked, played or recorded together (including playing in someone's band). Four predicates were added: `toured_with` (two acts on one tour or bill), `performs_as` (stage name or solo project), `renamed_from` (a band's earlier name), `interpolates` (a song borrowing an older song's melody or lyric). The walk will not follow `toured_with`. |
| v8 | **English sources are always accepted; another language only for artists tied to it.** The pilot is English-language, so it reads English pages only; 18 claims quoting German pages were deleted and 35 non-English links marked dead. A future non-English slice reads English plus its own language, with reading questions translated for Matt. |
| v9 | The graph's uses for Matt moved to `docs/graph/COMPANION_SPEC.md` (Phase 6): the API, the prod copy and the walk are its Blocks A, B and H. |
| v10 | **Duplicate entities merge only when safe**: an entity without an MBID merges into one with an MBID only when both share a normalised name and at least one album. Every merge is logged and `graph unmerge` undoes it; other name matches stay apart and are reported. |
| v11, v12 | **More vocabulary** (Matt: "add whatever vocab you need"): `tribute_to`, `named_after`, `references` (a lyric naming a song), `based_on` (a song from a poem), `appears_in` (a song in a film or advert), `relative_of`, `arrangement_from` (following another performer's arrangement), `compiles` (an album collecting EPs), `companion_to` (an album and its film); `influenced_by` may name a genre; `renamed_from` covers studios and labels. 30 predicates. |

Matt also confirmed Josh Terry's **No Expectations** (noexpectations.fyi) as a critic worth
reading: 92 of his posts are linked as review pages to 37 pilot albums.

WhoSampled was considered and left out: it has no public API, its robots.txt blocks AI crawlers
including Claude's, and it asks for reference use only. Samples come from MusicBrainz and Discogs.

Rate Your Music review pages are not read: their cached pages often held the wrong album, so all
67 links are marked dead. (Its all-time chart is a queue list, `rym_top_5000`; queue spec v13.)

## 4. Where the work stands

Phase 5 (building the graph) is done: all 169 baselined albums of the Crazy Horse slice are read
and verified in seven batches (P01–P07), and the graph moved to prod with Phase 6 Block A (the
graph API) and Block B (the prod copy and the nightly graph workflow), released as v0.4.0.

| Batch | Albums | First-pass agreement | Notes |
| --- | --- | --- | --- |
| P01–P04 | 100 | 96.2% | Neil Young, Crazy Horse and the Chicago and Asheville circle |
| P05 | 25 | 97% | Dialup Ghost's own list of 48 influences; Jason Molina's influence on 11 artists; Will Johnson's bands |
| P06 | 25 + 2 | 98% | Buffalo Springfield to *This Note's for You*; "The Loner" covered by Stephen Stills; *Trans* and Kraftwerk; Arbouretum's real Wikipedia page fetched after the atlas link proved dead |
| P07 | 19 | 99% | *Left for Dead* to *Oceanside Countryside*; Sonic Youth's influence on *Arc* and their tour; Dogwood Tales and the Asheville Bandcamp records |

## 5. What the graph holds (prod, 2026-10-10)

| | Count |
| --- | --- |
| Accepted claims | 6,315 |
| Rejected (a check failed, or Matt said no) | 29 |
| Superseded (replaced by a correction or an answer) | 53 |
| Entities (people, bands, albums, recordings, places, labels) | 5,112 |
| Pilot albums with accepted lineage or connection claims | 88 of 169 |
| Claims under Matt's name (his answers) | 13 |

Accepted claims by origin: pages (Wikipedia credit facts and claims read by Claude) 2,857;
MusicBrainz 1,742; Discogs 1,703; Matt 13.

Accepted claims by kind: credited on 4,376; member of 405; recorded at 400; genre 317; released by
309; worked with 144; sounds like 113; influenced by 90; based in 89; toured with 29; covers 27;
borrows from (interpolates) 4; relative of 4; performs as 4; renamed from 2; came out of a scene 2.

## 6. Quality

Each text claim is judged by an **independent reader agent** that sees only the claim and its
quote. The bar (Block E) is at least 90% `SUPPORTS` on the first reading for every kind of claim
with 10 or more read.

First-pass reader agreement, P01–P07 (1,525 claims read): **97.0%**.

| Kind | Read | First-pass agreement |
| --- | --- | --- |
| credited on | 665 | 98% |
| member of | 260 | 95% |
| worked with | 170 | 98% |
| sounds like | 118 | 91% |
| influenced by | 90 | 100% |
| recorded at | 60 | 97% |
| genre | 53 | 98% |
| covers | 30 | 83% |
| toured with | 29 | 100% |
| released by | 24 | 96% |
| based in | 12 | 100% |

**Covers is the one kind below the bar** (25 of 30). Its misses are the writer-versus-first-recording
mistake: a page names a song's writer or an earlier version without saying who recorded it first
(Ratboys' "Spiderweb", the Byrds' "See the Sky About to Rain", Jimmy Reed's "Bright Lights, Big
City"). The checks caught each one; none was accepted on a wrong reading.

All eight graph data checks (G1–G8) pass in prod. No reading questions are open.

## 7. Known problems and fixes

- **Rate Your Music pages held the wrong albums** (*Colorado* held Saint Etienne, *Homegrown* the
  Boo Radleys, *Barn* the Ramones); all its links are now dead and batch exports skip them.
- **Some registered links point at the wrong page.** *Silver & Gold*'s Wikipedia link is the
  concert video's article; its claims came from a PopMatters review instead.
- **MusicBrainz song histories are incomplete.** The first-recording check wrongly rejected two
  good covers (*Farmer John*, Pearl Jam's "Fuckin' Up"). Tolerable; watched.
- **Fixed in P04:** the quote normaliser now reads Wikipedia link titles with escaped quotes
  (`George \"Chocolate\" Perry`); `load-claims` retires a claim only once its correction is stored
  (an identical correction had briefly hidden the Cassandra Wilson cover).
- **Neil Young's own pages yield little lineage.** They mostly compare him to his own earlier
  records, which the rules skip. His albums gain covers, credits and collaborations instead.

## 8. Cost

- Firecrawl: 855 credits spent this month of the 100,000 limit (396 pages, including the 168
  No Expectations posts). Matt pre-approved spending up to the monthly limit; reading batches cost
  no Firecrawl credits.
- MusicBrainz, Discogs and Wikidata: free.
- Reading and checking run as Claude Code agents on Matt's plan.

## 9. How a batch runs

Commands run from the laptop against prod, the graph's home since 2026-10-10 (`uv run musicdata --env prod graph …`), following the
`music-graph-research` skill in `.claude/skills/music-graph-research/SKILL.md`:

1. `graph batch export --slice crazy_horse --size 25`: the next 25 albums, their pages, cue
   paragraphs and already-known entities, into `data/graph/batches/<label>/`.
2. Five extractor agents read the pages and write claims; each runs
   `graph batch check-quotes` until every quote is on its page.
3. `graph batch load-claims`: names resolve to entities (MusicBrainz, free).
4. `graph verify --batch`: structure, evidence, dates, first recording, database cross-checks.
5. `graph batch reader-input`, then separate reader agents (about 30 claims each), then
   `graph batch load-reader`.
6. Extraction faults are fixed as corrections (`replaces`) and re-read; the rest become reading
   questions.
7. `graph report --batch`, `musicdata dq`, then the questions go to Matt;
   `graph answer <label>=yes|no|skip` records his answers.

Other commands: `graph critic --site noexpectations` (refresh the critic's posts),
`graph import / fetch / facts / link` (baseline and pages), `graph report --slice crazy_horse`.

## 10. Next steps

1. Phase 6 Block C on Matt's go: the nightly workflow builds the near-queue albums (the default
   scope) — baseline, pages and facts — and reports coverage, credits and database size after three
   nights.
2. Then Blocks D–I of `docs/graph/COMPANION_SPEC.md`: connections and threads, album and hub pages,
   liner notes and deep dives, the worker, the queue changes.
3. Covers: tighten the extraction rule or the reader prompt until first-pass covers reach 90%.

## 11. Decisions waiting for Matt

- No reading questions are open (Matt answered the last three on 2026-10-10: *Arc* and
  *Metal Machine Music* no; Arbouretum alternative rock yes; *Trans* in Hawaii no, the quote is unclear).
- Gaps still left out as not music relations: a band-name lawsuit, MTV as an influence, a book
  mentioning an album, an album about a political figure.

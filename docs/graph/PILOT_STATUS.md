# Music graph pilot: status (2026-10-10)

This file is the current state of the music graph (Phases 5 and 6) of `music-data-platform`, written for
Matt's claude.ai Project. Read it to answer questions about the graph pilot; read
`docs/graph/GRAPH_SPEC.md` (how the graph is built) and `docs/graph/edge-vocabulary.md` (what a
claim is) for the rules themselves. All graph data lives in the dev environment; prod has none yet.

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

## 3. Rules Matt set during the pilot (graph spec v2–v9)

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

Matt also confirmed Josh Terry's **No Expectations** (noexpectations.fyi) as a critic worth
reading: 92 of his posts are linked as review pages to 37 pilot albums.

WhoSampled was considered and left out: it has no public API, its robots.txt blocks AI crawlers
including Claude's, and it asks for reference use only. Samples come from MusicBrainz and Discogs.

Rate Your Music review pages are not read: their cached pages often held the wrong album, so all
67 links are marked dead. (Its all-time chart is a queue list, `rym_top_5000`; queue spec v13.)

## 4. Where the work stands

Phase 5 (building the graph):

| Block | What | State |
| --- | --- | --- |
| 0, A | Setup, schema, predicates | Done |
| B | Free baseline from MusicBrainz, Discogs and Wikidata | Done for all 169 albums |
| C | Firecrawl page fetches and Wikipedia credit facts | Done (395 pages) |
| D | Verification, reading batches, report, data checks G1–G8 | Done |
| E | **Reading batches** | **P01–P04 verified (100 of 169 albums)**; P05–P07 to go (69 albums), alongside Phase 6 |

Phase 6 (`docs/graph/COMPANION_SPEC.md`, what the graph does for Matt) starts with Block A, the
graph API. Prod becomes the graph's home at its Block B, on Matt's go.

### P04 (done)

25 Neil Young albums, *Long May You Run* (1976) to *Talkin to the Trees* (2025): 217 claims,
**98% first-pass agreement**, three reading questions answered (Spooner Oldham and Ben Keith on
*Prairie Wind*: no; "Silver Eagle", a German quote: removed under v8). Highlights:

- *Harvest Moon*: eight covers (Cassandra Wilson, Lord Huron, Bill Frisell and others).
- *Mirror Ball*: Pearl Jam's credits, and Pearl Jam opening Young's 1993 tour (`toured_with`).
- "Four Strong Winds" covers Ian & Sylvia's 1963 recording; Nicolette Larson's "Lotta Love" covers
  Young's 1976 version.
- "Love Is a Rose" reworks "Dance, Dance, Dance"; the riff of "Psychedelic Pill" comes from "Sign
  of Love" (`interpolates`).
- Rejected by the checks, correctly: Linda Ronstadt's "Love Is a Rose" as a cover (she released it
  before Young did).

## 5. What the graph holds (dev, 2026-10-10)

| | Count |
| --- | --- |
| Accepted claims | 5,582 |
| Rejected (a check failed, or Matt said no) | 24 |
| Superseded (replaced by a correction or an answer) | 50 |
| Entities (people, bands, albums, recordings, places, labels) | 5,384 |
| Albums with accepted lineage claims (P01–P04) | 60 of 100 |
| Claims under Matt's name (his answers) | 21 |

Accepted claims by origin: MusicBrainz 1,731; Discogs 1,680; Wikipedia credit facts via Firecrawl
1,160; read from pages by Claude 1,000; Matt 11.

Accepted claims by kind: credited on 4,007; recorded at 359; member of 323; released by 286; genre
260; worked with 98; sounds like 94; based in 73; influenced by 28; toured with 25; covers 24;
borrows from (interpolates) 3; came out of a scene 2.

## 6. Quality

Each text claim is judged by an **independent reader agent** that sees only the claim and its
quote. The bar (Block E) is at least 90% `SUPPORTS` on the first reading for every kind of claim
with 10 or more read.

First-pass reader agreement, P01–P04 (916 claims read): **96.2%**.

| Kind | Read | First-pass agreement |
| --- | --- | --- |
| credited on | 342 | 97% |
| member of | 188 | 96% |
| worked with | 124 | 98% |
| sounds like | 97 | 92% |
| genre | 32 | 100% |
| recorded at | 28 | 100% |
| influenced by | 28 | 100% |
| covers | 25 | 84% |
| toured with | 25 | 100% |
| released by | 14 | 93% |
| based in | 10 | 100% |

Covers is the one kind below the bar. Its early misses were the writer-versus-first-recording
mistake (Ratboys' "Spiderweb", the Byrds' "See the Sky About to Rain"), which v5 now prevents; P04's
covers alone reached 90%.

All eight graph data checks (G1–G8) pass in dev. No reading questions are open.

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

- Firecrawl: 850 credits spent this month of the 100,000 limit (395 pages, including the 168
  No Expectations posts). Reading batches cost no Firecrawl credits.
- MusicBrainz, Discogs and Wikidata: free.
- Reading and checking run as Claude Code agents on Matt's plan.

## 9. How a batch runs

Commands run from the laptop against dev (`uv run musicdata --env dev graph …`), following the
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

1. Phase 6 Block A: the graph API (entities, neighbors, the album brief with pages and per-track
   credits, questions, coverage, posting claims and links), and the pilot's re-import for
   per-track credits.
2. P05–P07, the remaining 69 albums, alongside Phase 6.
3. Phase 6 Block B on Matt's go: prod becomes the graph's home, with a nightly graph workflow.
4. Then Blocks C–I of `docs/graph/COMPANION_SPEC.md`: default building blocks, connections and
   threads, album and hub pages, liner notes and deep dives, the worker, the queue changes.

## 11. Decisions waiting for Matt

- Optional vocabulary still unmodelled, logged by extractors: songs written for or about a person,
  awards, films and books as influences, songs used in films, reissues, family relations, song
  working titles, a lyric naming another song.

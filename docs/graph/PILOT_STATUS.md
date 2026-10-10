# Music graph pilot: status (2026-10-10)

This file is the current state of Phase 5 (the music graph) of `music-data-platform`, written for
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

The point: an API that answers "what is this record connected to?" (Block F) and a "walk back
from here" control on the `/queue` page that builds a listening list from an album's lineage
(Block G).

## 2. The pilot

The pilot is the **Crazy Horse line**: 181 albums chosen from the V atlas (lanes C1, V1, V20 and
the path "MJ Lenderman to Neil Young"), from MJ Lenderman, Wednesday, Greg Freeman, Ratboys,
Florry and the Asheville/Burlington scenes back through Songs: Ohia, Magnolia Electric Co.,
Centro-matic and Will Johnson to Neil Young, Crazy Horse, Buffalo Springfield and CSNY.

- 169 of the 181 have a MusicBrainz match and are in the pilot; 12 small releases have none.
- Albums are read in **batches of 25** (P01–P07).

## 3. Rules Matt set during the pilot (graph spec v2–v7)

| Version | Rule |
| --- | --- |
| v2 | A name scraped from a Wikipedia page passes the name-on-page check with or without quote marks, and a studio passes without a trailing "Studio(s)". |
| v3 | The Firecrawl budget is 100,000 credits a month (the plan's real limit). Each run stops at 500 credits; a slice above 1,500 credits waits for Matt. |
| v4, v5 | **The atlas is AI-written, so it sets scope only**: which albums and artists, their priority, and pages to look at. It is never the source of a claim. Every claim quotes a real page or comes from a database. 224 atlas-sourced claims and Matt's 7 answers on atlas quotes were deleted. |
| v5 | **"Worked on" means work on the music**: playing, singing, writing, producing, engineering, mixing, mastering, arranging. Artwork, photography, design, liner notes and business roles are not credits (346 such claims were deleted, and imports skip them). |
| v5 | **Writing a song is not recording it first.** A cover claim needs the source to name who recorded the song earlier. |
| v6 | A critic's **"RIYL" or "for fans of"** line about an album counts as "sounds like" for each artist named. |
| v7 | **Touring is not working together.** `associated_with` means worked, played or recorded together (including playing in someone's band). Four predicates were added: `toured_with` (two acts on one tour or bill), `performs_as` (stage name or solo project), `renamed_from` (a band's earlier name), `interpolates` (a song borrowing an older song's melody or lyric). The walk will not follow `toured_with`. |

Matt also confirmed Josh Terry's **No Expectations** (noexpectations.fyi) as a critic worth
reading: 92 of his posts are linked as review pages to 37 pilot albums.

WhoSampled was considered and left out: it has no public API, its robots.txt blocks AI crawlers
including Claude's, and it asks for reference use only. Samples come from MusicBrainz and Discogs.

## 4. Where the work stands

| Block | What | State |
| --- | --- | --- |
| 0, A | Setup, schema, predicates | Done |
| B | Free baseline from MusicBrainz, Discogs and Wikidata | Done for all 169 albums |
| C | Firecrawl page fetches and Wikipedia credit facts | Done (395 pages) |
| D | Verification, reading batches, report, data checks G1–G8 | Done |
| E | **Reading batches** | P01–P03 verified (75 albums); P04 extracted, not yet loaded; P05–P07 to go (69 albums) |
| F | Graph API, including answering reading questions from the Project | Not started |
| G | "Walk back from here" on the `/queue` page; copy the graph to prod | Not started; the prod copy waits for Matt's go |

### P04 (in progress)

25 Neil Young albums, *Long May You Run* (1976) to *Talkin to the Trees* (2025). Five extractor
agents wrote 217 claims; every quote passes the quote checker. Highlights:

- *Harvest Moon*: eight covers (Cassandra Wilson, Lord Huron, Bill Frisell and others).
- *Mirror Ball*: Pearl Jam's credits, and Pearl Jam opening Young's 1993 tour (`toured_with`).
- "Four Strong Winds" covers Ian & Sylvia's 1963 recording; Nicolette Larson's "Lotta Love" covers
  Young's 1976 version.
- "Love Is a Rose" reworks "Dance, Dance, Dance"; "Ocean Girl" reuses "War Song"; the riff of
  "Psychedelic Pill" comes from "Sign of Love" (`interpolates`).

Still to do for P04: load, verify, independent reader, then reading questions for Matt.

## 5. What the graph holds (dev, 2026-10-10)

| | Count |
| --- | --- |
| Accepted claims | 5,387 |
| Rejected (a check failed, or Matt said no) | 20 |
| Superseded (replaced by a correction or an answer) | 48 |
| Entities (people, bands, albums, recordings, places, labels) | 5,320 |
| Albums with accepted lineage claims (P01–P03) | 47 of 75 |
| Claims under Matt's name (his answers) | 19 |

Accepted claims by origin: MusicBrainz 1,731; Discogs 1,680; Wikipedia credit facts via Firecrawl
1,160; read from pages by Claude 805; Matt 11.

Accepted claims by kind: credited on 3,912; recorded at 351; member of 296; released by 286; genre
245; sounds like 94; based in 73; worked with 64; influenced by 27; toured with 23; covers 14; came
out of a scene 2.

## 6. Quality

Each text claim is judged by an **independent reader agent** that sees only the claim and its
quote. The bar (Block E) is at least 90% `SUPPORTS` on the first reading for every kind of claim
with 10 or more read.

First-pass reader agreement, P01–P03 (736 claims read): **95.7%**.

| Kind | Read | First-pass agreement |
| --- | --- | --- |
| credited on | 252 | 96% |
| member of | 161 | 95% |
| sounds like | 97 | 92% |
| worked with | 90 | 97% |
| influenced by | 27 | 100% |
| genre | 27 | 100% |
| toured with | 23 | 100% |
| recorded at | 20 | 100% |
| covers | 15 | 80% |
| released by | 14 | 93% |
| based in | 10 | 100% |

Covers is the one kind below the bar. Both real misses were the writer-versus-first-recording
mistake (Ratboys' "Spiderweb", the Byrds' "See the Sky About to Rain"), which the v5 rule now
prevents; the third miss is the corrected Byrds claim carrying the original's first verdict.

All eight graph data checks (G1–G8) pass in dev. No reading questions are open.

## 7. Known problems

- **Rate Your Music pages are unreliable.** At least six cached pages hold the wrong album
  (*Colorado* holds Saint Etienne, *Homegrown* the Boo Radleys, *Barn* the Ramones, *Josephine* Big
  Audio Dynamite, two P02 pages the Verve); several are empty. Extractors skip them. Proposed:
  mark Rate Your Music links dead so they are not read again (waiting for Matt).
- **Some registered links point at the wrong page.** *Silver & Gold*'s Wikipedia link is the
  concert video's article; its claims came from a PopMatters review instead.
- **MusicBrainz song histories are incomplete.** The first-recording check wrongly rejected two
  good covers (*Farmer John*, Pearl Jam's "Fuckin' Up"). Tolerable for now; watched.
- **Quote normaliser gap.** Wikipedia link titles with escaped quote marks
  (`George \"Chocolate\" Perry`) are not cleaned, so two real names failed the quote check and were
  dropped (both credits exist from MusicBrainz or Discogs). A small fix is planned before P04 loads.
- **Neil Young's own pages yield little lineage.** They mostly compare him to his own earlier
  records, which the rules skip (discography order comes from MusicBrainz). His albums gain
  covers, credits and collaborations rather than comparisons.

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

1. Fix the quote normaliser (escaped quotes in link titles), then finish P04: load, verify, read,
   reading questions.
2. P05–P07: the remaining 69 albums.
3. Block E sign-off: every kind of claim at 90% or better on first reading, G1–G8 green, every
   question answered.
4. Block F: the graph API (`/entities`, `/albums/{id}/graph`, `/albums/{id}/brief`,
   `/graph/questions` and answers), and its rows in the Project instructions.
5. Block G: "walk back from here" on the `/queue` page, following lineage claims only; then the
   copy to prod on Matt's go.

## 11. Decisions waiting for Matt

- Mark Rate Your Music links dead (recommended: yes).
- Optional vocabulary still unmodelled, logged by extractors: songs written for or about a person,
  awards, films and books as influences, songs used in films, reissues, family relations, song
  working titles, a lyric naming another song.

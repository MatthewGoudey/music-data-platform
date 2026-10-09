# ADR 0017: The claims graph

- Status: Accepted
- Date: 2026-10-09
- Decision: store the music graph as claims in Postgres, page bodies in `source_fetch`; Claude reads in exported batches checked by an independent reader; dev first, prod only on Matt's go

## Claims, not facts

The graph records what sources say: *subject — predicate — object*, each with a source, a
basis (`documented`, `reported`, `inferred`), verbatim evidence, a confidence and a status.
Claims that disagree stay side by side; the `edge` view picks the current one by the
precedence in `docs/graph/edge-vocabulary.md` section 3. Whether a claim is musically true is
left to its sources: checks that need no music knowledge (structure, verbatim evidence, dates,
first recording, database agreement, independence, an independent reader) decide whether the
source says it, and Matt answers only the reading questions those checks cannot settle.

## Storage

Everything lives in the existing Neon Postgres, beside the listening data: `entity` (anchored
on MusicBrainz IDs, linked to `release_group` and `artist` rows; local ids for lanes, maps,
lists and tags), `assertion` (one row per claim), `entity_link`, `map_membership`,
`graph_album`, `source_fetch` (every Firecrawl fetch: the page body, its facts JSON and its
credits, so the cache, the credit ledger and the gaps log are one table) and `graph_batch`.
A graph database was not needed: the walk is two hops over an indexed table, and keeping one
database keeps backups, the read-only `/query` role and the dev/prod split as they are.

## Who does what

Free APIs (MusicBrainz, Wikidata, Discogs) give the baseline; Firecrawl, paid and budgeted,
fetches registered pages only; Claude, on Matt's plan, reads pages and atlas prose for lineage
in exported batches of up to 25 albums, and a separate reader agent judges each text claim
against its quote alone. Jobs exchange files with the database through `musicdata graph
batch …`, so the paid and the judged steps leave a record.

## Dev first

Graph jobs and graph data stay in dev until the pilot is verified (GRAPH_SPEC Block G);
`graph copy --source dev` then moves them to prod on Matt's go. The schema migrations may ride
to prod earlier with a queue fix: empty tables change nothing. `FIRECRAWL_API_KEY` lives in the
dev and prod GitHub Environments and the gitignored `.env`; the Fly API fetches nothing and
keeps its secrets as they are.

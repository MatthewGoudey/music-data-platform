# scripts/graph — music graph research tools

Standard-library Python used by the `music-graph-research` skill until the `graph` jobs exist in
`src/musicdata/` (pilot milestones M1–M2 in `docs/graph/edge-vocabulary.md`). Run them from the repo
root with `uv run python scripts/graph/<tool>.py`. They write to `data/graph/` (gitignored).

| Tool | Does | Cost |
| --- | --- | --- |
| `album_baseline.py` | MusicBrainz identity, links, labels, credits, studios and songwriters; the Wikidata Wikipedia sitelink; album artists' memberships and areas; Discogs credits, labels and styles | free |
| `mb_baseline.py`, `mb_artist.py`, `mb_work.py`, `mb_resolve.py`, `discogs.py` | the single lookups `album_baseline.py` and `verify.py` call | free |
| `fetch.py` | one Firecrawl fetch into the shared cache, once per URL and mode; enforces the run cap; logs failures to `gaps.md` without retrying; records credits in `credits.jsonl` | 5 (`facts`), 1 (`plain`, `bandcamp`) |
| `cache_split.py` | stores a fetch as page text plus facts JSON with an index line (called by `fetch.py`) | — |
| `facts_v2.schema.json` | the Firecrawl JSON schema for credits and recording facts | — |
| `facts_to_claims.py` | MusicBrainz, Discogs and Firecrawl facts as claims, with cross-source agreement | free |
| `cues.py` | prints only the paragraphs of a cached page that can carry lineage, for Claude to read | free |
| `verify.py` | structure, verbatim evidence, date direction, first recording, database agreement, independence, reader verdicts; confidence and status | free |
| `reader_input.py` | the independent reader's input: each text claim as a sentence with its quote and source | free |
| `report.py` | the run report: counts, reading questions for Matt, rejections, skipped sentences, credits, gaps | free |

Rate limits: MusicBrainz 1 request/second (503 means slow down), Discogs 25 a minute without a token
(set `DISCOGS_TOKEN` for 60), Wikidata backs off on 429. Firecrawl reads `FIRECRAWL_API_KEY` from the
environment or `.env`.

Data layout:

```
data/graph/cache/            pages and facts, index.jsonl (shared)
data/graph/baseline/         MusicBrainz and Discogs JSON per album and artist (shared)
data/graph/runs/<run_id>/    albums.csv, claims/, reader_input.txt, reader_output.jsonl,
                             claims_verified.jsonl, report.md
data/graph/credits.jsonl     Firecrawl ledger
data/graph/gaps.md           pages that failed, never retried
```

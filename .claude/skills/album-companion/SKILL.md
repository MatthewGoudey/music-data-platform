---
name: album-companion
description: Use when writing Matt's requested album deep dives for music-data-platform, or when Matt says "write my requested documents". Works through the API's document endpoints; every sentence cited to a graph claim or a cached page and fact-checked by a separate agent.
---

# Album companion: deep dives

You write deep dives Matt reads while an album plays (liner notes were dropped on 2026-10-10: the album
page already gives the facts, so the deep dive is the one document). The spec is `docs/graph/COMPANION_SPEC.md`
sections 7 and 8; this skill is how to carry it out. The model for a good deep dive is the Boat Songs
document (dev, `/albums/96/documents/deep_dive`): Matt called it "an excellent write up".

Everything goes through the API. `API` is the base URL the run names: `https://musicdata-dev.fly.dev`
(dev) or `https://musicdata-prod.fly.dev` (prod). Authentication comes one of two ways:
- In a routine (the worker, `docs/graph/WORKER_ROUTINE.md`), the environment's network secret adds the
  bearer token to every call to the API host: call it with `curl` and no Authorization header.
- In a session on the laptop, send `Authorization: Bearer $API_TOKEN`, with the token from the
  repo's `.env.dev` or `.env.prod`. Never print it.

## 1. Pick up the work

1. `GET $API/documents?status=requested` — the requested documents, oldest first. None: say so and stop.
2. For each one, `POST $API/documents/{id}/start` → `lease_token` (3 hours) and `batch` (`D<id>`).
   Keep the token for every write. A deep dive that runs past two hours:
   `POST $API/documents/{id}/renew {"lease_token": …}`.
3. `GET $API/albums/{release_group_id}/brief?format=json` — identity, tracks (with per-track
   credits and borrowings), edges by facet (each with `assertion_id`, evidence, source URL),
   `pages` (each cached page's `fetch_id`, URL, title), links, gaps, Matt's context, and
   `connections` (albums the graph ties it to).
4. Read the cached pages: `GET $API/fetches/{fetch_id}` (text and URL).

## 2. Research

Find 4–8 more English sources: interviews with the artist about this record, features, reviews,
oral histories, liner notes. Search with the Firecrawl connector (`firecrawl_search`); then have the
API fetch each page you will use:

`POST $API/fetches {"url": …, "mode": "plain", "document_id": id}` → `fetch_id` and text.

The API caches it, counts it against the document's budget (100 credits by default) and refuses
non-English pages and Rate Your Music. Never cite a page you read some other way: a page is citable
only by its `fetch_id`. Count your own searches (about 2 credits each) for the hand-in.

## 3. Write

Markdown, following the record as Matt hears it.

3,000–6,000 words, in this order: `## Before you press play` (two paragraphs) · `## The band and the
moment` · `## Making the record` · `## Track by track` · `## Reception, then and now` · `## Where it
comes from` · `## Where it leads` · `## The people` · `## Listen next`.

Track by track: one `### <n>. <title>` per track in tracklist order, each followed by its anchor line
`<a id="track-<n>"></a>`: credits, writing, what sources say about it, covers and borrowings. Listen
next: five to ten albums from the brief's `connections` (different artists, different kinds of
link), each with its reason and the claim ids behind it.

Rules (always):
- **Cite every factual sentence**: end it with `[c:<assertion_id>]` for a claim or `[p:<fetch_id>]`
  for a cached page. Markers are the only citation format. A sentence you cannot cite does not go in.
- **Sound comes from sources**: take descriptions of how a track or the record sounds from a review,
  interview or liner note and attribute them ("Pitchfork hears…"). Where no source describes a
  track, give its credits, writing and history, and say plainly that its sound is left to Matt's ears.
- **Quote briefly**: at most two sentences from any one review or article; paraphrase the rest.
- **The graph's rules**: a cover points at the song's first recording; a comparison belongs to the
  critic who made it; English sources only.
- **No facts from memory**: if no claim or page says it, leave it out. Where sources disagree, say so
  and cite both.
- Write warmly and concretely, in plain sentences, with no hype words.
- Leave out any `## Sources` section: the page builds its own from the markers.

## 4. New claims

Facts you found on new pages that the graph lacks (a credit, where it was recorded, a comparison,
an influence, a cover) go back to the graph. Post them in the music-graph-research skill's claim
shape with labels `D<id>-rg<release_group_id>-L<n>`, `"batch": "D<id>"` and
`"release_group_id": <id>`:

`POST $API/assertions [ … ]` → label → `assertion_id`. Cite those ids in the text.

Then run a **separate reader agent** on them with the reader prompt from
`.claude/skills/music-graph-research/SKILL.md` section 4, and post its verdicts:
`POST $API/graph/batches/D<id>/reader-verdicts [{"claim_id", "verdict", "reason"}]`. Cite only
claims the reader marked SUPPORTS.

## 5. Fact-check

Launch a **separate agent that did not write the document**. Give it the draft, the claims
(`GET $API/assertions?ids=…` for every `c:` marker) and the pages (`GET $API/fetches/{id}`), and
this brief: for every sentence with a marker, judge supported / partial / unsupported against its
cited sources alone, with no outside knowledge; flag quotations longer than two sentences,
unattributed descriptions of sound, and factual sentences without a marker. Rewrite or remove every
partial and unsupported sentence, then have the same checker re-read the changed sentences until
none remain. Keep the tally.

## 6. Hand in

`PUT $API/documents/{id}`:

```json
{"lease_token": "…", "status": "ready", "body_md": "…", "model": "<your model id>",
 "firecrawl_credits": <your searches' credits>,
 "checks": {"sentences": n, "supported": n, "rewritten": n, "removed": n, "unsupported_remaining": 0}}
```

The API refuses `ready` and lists any marker that does not resolve (a claim must be accepted or in
`D<id>`; a page must be cached): fix those and send again. When the work cannot be finished, send
`{"lease_token": …, "status": "failed", "error": "<why>"}`; the document is retried once.

Then report to Matt: the album, the kind, the word count, the sources added, the credits, the
fact-check tally, and the page: `/albums/{release_group_id}/page` (the tab opens the document).

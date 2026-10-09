# ADR 0016: Show sources, scraping etiquette, and the match score

- Status: Accepted
- Date: 2026-10-08
- Decision: public pages only, an honest User-Agent, two seconds between requests; match score recency halves every two years

## Etiquette

The shows job reads other people's sites every night, so it behaves like a good guest:

- Public pages and official APIs only. A site's token-gated API (Oh My Rockness `/api/`)
  is not used, and no token is lifted from a site's JavaScript.
- The User-Agent names the project and links this repository
  (`musicdata/0.1; +https://github.com/MatthewGoudey/music-data-platform`). A site that
  refuses that User-Agent is not crawled; we do not pose as a browser (do312, ADR 0006).
- `robots.txt` is honoured, at least two seconds pass between requests to one host
  (Ticketmaster's API: four a second, under its five), and 429/5xx back off.
- Only new pages are fetched: stored shows are refreshed from the venue listing, not
  re-read. A nightly run is about 200 requests to Oh My Rockness and a dozen to
  Ticketmaster.
- API keys never reach logs: httpx request logging is off, and the Ticketmaster client
  redacts its key from any error (Actions logs are public).

## The match score

`GET /shows?match=true` ranks upcoming shows by the plan's score,
`ln(listens + 1) × min(distinct tracks / 5, 3) × recency`, where recency halves for every
two years since the artist was last played. A show scores as its best-matching performer,
headliner or support. Tribute nights and screenings (`non_artist`) never match, even when
a listing credits the real band.

## Consequences

- Coverage is Oh My Rockness's (indie and rock rooms) plus Ticketmaster's (large rooms,
  tours). Small rooms that sell through neither are missing until a source that allows
  polite crawling covers them.
- The score's half-life is a starting value; raise it if old favourites rank too low.

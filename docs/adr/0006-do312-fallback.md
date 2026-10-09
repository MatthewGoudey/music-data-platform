# ADR 0006: Chicago show source fallback

- Status: Superseded by the amendment below (2026-10-08)
- Date: 2026-10-07
- Decision: do312 from GitHub runners is the primary source; Ticketmaster is the supplement

do312 is behind Cloudflare and may block datacenter IPs. The `spike-scrapers` workflow probes it from a GitHub runner. If blocked: Ticketmaster plus venue-site scrapers, or run the scraper on the Fly machine. Decided by the spike result.

## Spike result (2026-10-08, run 37722339520)

- do312: `status=200 bytes=357101`, 25 `itemprop="startDate"` events. The GitHub runner's
  datacenter IP passes Cloudflare with a browser User-Agent, and the page carries schema.org
  event markup to parse.
- Ticketmaster: `status=401` because `TICKETMASTER_API_KEY` is not set yet; this measures
  nothing about reachability.

## Decision

Phase 3 scrapes do312 from the `shows` job on GitHub runners. Ticketmaster stays as a
supplement once a free API key is in the environment secrets. Re-run `spike-scrapers` before
Phase 3 starts; if do312 then returns a challenge page (non-200 or 0 events), switch to the
fallback above.

## Amendment (2026-10-08): Oh My Rockness and Ticketmaster; do312 is not crawled

- **do312 refuses an honest User-Agent.** The spike passed only with a browser
  User-Agent; a request that names this project gets 403 from Cloudflare. Posing as a
  browser to get past a bot block is not something this project does (ADR 0016), so
  do312 is not crawled. Its parser stays in `shows/parse.py`, tested, in case do312
  offers access.
- **Oh My Rockness is primary.** Venue pages list every upcoming show as schema.org
  JSON-LD and each show page embeds its record: bands already split and in order, start
  time with offset, venue with coordinates. Its token-gated `/api/` is not used.
- **Ticketmaster is secondary**, through the official Discovery API (key in the
  environment secrets): arenas, blues clubs and tours Oh My Rockness skips.
- Shows from both merge on venue, local date and headliner; the first run in dev stored
  902 upcoming shows, 133 of them listed by both.

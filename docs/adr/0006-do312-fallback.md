# ADR 0006: Chicago show source fallback

- Status: Proposed
- Date: 2026-10-07
- Decision: Decide after the spike

do312 is behind Cloudflare and may block datacenter IPs. The `spike-scrapers` workflow probes it from a GitHub runner. If blocked: Ticketmaster plus venue-site scrapers, or run the scraper on the Fly machine. Decided by the spike result.

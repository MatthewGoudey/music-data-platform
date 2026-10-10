"""`musicdata graph critic --site <name>`: a critic's posts as review pages for a slice's albums.

Matt named Josh Terry's No Expectations (noexpectations.fyi) as a critic worth reading for the
pilot's bands (2026-10-09). The job reads the site's sitemap, fetches each post once as a
plain page (1 credit, cached in `source_fetch` like every Firecrawl page, within `--max-credits`
and the monthly ceiling), and links a post to every slice album whose artist and title both
appear in its text (`entity_link`, kind `review`). The reading batches then read those posts
like any other page; a claim still has to quote the post.
"""

from __future__ import annotations

import re
from collections import defaultdict

from musicdata.clients.firecrawl import COST, SCHEMA_VERSION, FirecrawlClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph.fetch import MONTH_SPENT
from musicdata.graph.importer import slice_targets
from musicdata.graph.verify import norm
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

SITES = {
    "noexpectations": {
        "sitemap": "https://www.noexpectations.fyi/sitemap.xml",
        "post": re.compile(
            r"https://www\.noexpectations\.fyi/p/[a-z0-9-]+?(?=\d{4}-\d{2}-\d{2}|\s|$)"
        ),
        "source": "web:noexpectations.fyi",
    }
}
MIN_TITLE = 4  # shorter album titles ("III") match too much prose
NEAR = 150  # characters between the artist's name and the album title


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    spans, start = [], text.find(needle)
    while start >= 0:
        spans.append((start, start + len(needle)))
        start = text.find(needle, start + 1)
    return spans


def post_urls(sitemap: str, pattern: re.Pattern) -> list[str]:
    """Post URLs in sitemap order (the scraped sitemap runs URL and date together)."""
    return list(dict.fromkeys(pattern.findall(sitemap)))


def mentions(body: str, artist: str, album: str) -> bool:
    """The normalised post names the album title within NEAR characters of the artist's name,
    the title being a separate mention (a self-titled album needs "self-titled" nearby), so a
    common word ("Lucky", "Freedom") elsewhere in the post does not count."""
    a, t = norm(artist), norm(album)
    if len(t) < MIN_TITLE or not a:
        return False
    names = _spans(body, a)
    titles = [s for s in _spans(body, t) if not any(s[0] < e and n < s[1] for n, e in names)]
    if t == a:
        titles = _spans(body, "self-titled")
    return any(abs(ts - ns) <= NEAR for ns, _ in names for ts, _ in titles)


def graph_critic(*, site: str, slice_name: str, max_credits: int = 500) -> JobFn:
    if site not in SITES:
        raise ValueError(f"unknown site {site}; known: {', '.join(SITES)}")
    spec = SITES[site]

    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        if settings.firecrawl_api_key is None:
            raise RuntimeError("FIRECRAWL_API_KEY is not set")
        counts: dict[str, int] = defaultdict(int)
        async with connection(ctx.pool) as conn:
            month = int(await conn.fetchval(MONTH_SPENT))
            cached = {
                r["url"]: r["body"]
                for r in await conn.fetch(
                    "SELECT DISTINCT ON (url) url, body FROM source_fetch "
                    "WHERE ok AND schema_version = $1 AND url LIKE $2 ORDER BY url, fetched_at DESC",
                    SCHEMA_VERSION["plain"],
                    spec["sitemap"].split("/sitemap")[0] + "%",
                )
            }
            targets = [t for t in await slice_targets(conn, slice_name) if t["entity_id"]]
        spent = 0

        async def fetch(fc: FirecrawlClient, url: str) -> str | None:
            nonlocal spent
            if url in cached:
                counts["cached"] += 1
                return cached[url]
            cost = COST["plain"]
            if spent + cost > max_credits or month + spent + cost > settings.graph_monthly_credits:
                counts["stopped_at_cap"] = 1
                return None
            page = await fc.scrape(url, "plain")
            spent += page.credits
            counts["pages_ok" if page.ok else "pages_failed"] += 1
            async with connection(ctx.pool) as conn:
                await conn.execute(
                    """INSERT INTO source_fetch (url, mode, schema_version, credits, ok, error,
                                                 title, body, pipeline_run_id)
                       VALUES ($1, 'plain', $2, $3, $4, $5, $6, $7, $8)""",
                    url, SCHEMA_VERSION["plain"], page.credits, page.ok, page.error,
                    page.title, page.body, ctx.run_id,
                )  # fmt: skip
            if page.ok:
                cached[url] = page.body or ""
            return page.body if page.ok else None

        async with FirecrawlClient(settings.firecrawl_api_key.get_secret_value()) as fc:
            sitemap = await fetch(fc, spec["sitemap"])
            if sitemap is None:
                raise RuntimeError(f"could not read {spec['sitemap']}")
            posts = post_urls(sitemap, spec["post"])
            counts["posts"] = len(posts)
            for i, url in enumerate(posts, 1):
                await fetch(fc, url)
                if counts.get("stopped_at_cap"):
                    break
                if i % 25 == 0:
                    log.info("critic progress", extra={"done": i, "of": len(posts)})

        links = []
        bodies = {u: norm(b) for u, b in cached.items() if u in set(posts)}
        for t in targets:
            for url, body in bodies.items():
                if mentions(body, t["raw_artist"] or "", t["raw_album"] or ""):
                    links.append((t["entity_id"], url, spec["source"]))
        async with connection(ctx.pool) as conn, conn.transaction():
            # each run replaces this site's links to the slice's albums
            await conn.execute(
                "DELETE FROM entity_link WHERE source = $1 AND entity_id = ANY($2::bigint[])",
                spec["source"],
                [t["entity_id"] for t in targets],
            )
            await conn.executemany(
                """INSERT INTO entity_link (entity_id, kind, url, source)
                   VALUES ($1, 'review', $2, $3) ON CONFLICT (entity_id, url) DO NOTHING""",
                links,
            )
        counts["links"] = len(links)
        counts["albums_linked"] = len({e for e, _, _ in links})
        ctx.rows = counts["links"]
        ctx.notes.update(site=site, slice=slice_name, credits=spent, **counts)

    return _run

"""`musicdata graph fetch` (GRAPH_SPEC 7.2): Firecrawl pages for a slice's baselined albums.

Registered links only (no web searches in batch jobs). Depth by atlas priority:

| Album | Pages |
| --- | --- |
| Essential, start here or on a path | English Wikipedia (`facts`) + up to 2 other registered links |
| Recommended | Wikipedia + 1 |
| Deep cut | 1: Wikipedia, else Bandcamp |

A cached `(url, schema_version)` is reused at no cost. The run stops before a call that would
cross `--max-credits`; nothing runs once this month's credits reach `GRAPH_MONTHLY_CREDITS`. A
failed page is a `source_fetch` row with `ok = false` and 1 credit, left for a later run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import urlparse

import asyncpg

from musicdata.clients.firecrawl import COST, SCHEMA_VERSION, FirecrawlClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph.importer import order_key, slice_targets
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

SKIP_KINDS = {"wikidata", "musicbrainz", "discogs", "streaming", "allmusic", "wikipedia"}
OTHER_ORDER = {"bandcamp": 0, "liner_notes": 1, "interview": 2, "review": 3, "label_page": 4,
               "official": 5, "live_session": 6, "other": 7}  # fmt: skip

MONTH_SPENT = """SELECT coalesce(sum(credits), 0) FROM source_fetch
                  WHERE fetched_at >= date_trunc('month', now())"""


def is_english_wikipedia(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == "en.wikipedia.org"


def plan(links: list[asyncpg.Record], tier: int) -> list[tuple[str, str]]:
    """(url, mode) pages to fetch for one album; `tier` as in importer.order_key (0 Essential,
    1 start here / on a path, 2 Recommended, 3 Deep cut)."""
    # the album's own page (MusicBrainz, Wikidata) before pages an atlas note cites, which
    # can be the artist's
    wikis = sorted(
        (x for x in links if x["kind"] == "wikipedia" and is_english_wikipedia(x["url"])),
        key=lambda x: ((x.get("source") or "").startswith("map:"), x["url"]),
    )
    wiki = wikis[0]["url"] if wikis else None
    others = sorted(
        {x["url"]: x for x in links if x["kind"] not in SKIP_KINDS}.values(),
        key=lambda x: (OTHER_ORDER.get(x["kind"], 9), x["url"]),
    )
    pages = [(wiki, "facts")] if wiki else []
    extra = {0: 2, 1: 2, 2: 1}.get(tier)
    if extra is None:  # Deep cut: one page, Wikipedia else Bandcamp
        if not pages:
            band = next((x["url"] for x in others if x["kind"] == "bandcamp"), None)
            pages = [(band, "bandcamp")] if band else []
        return pages
    for x in others[:extra]:
        pages.append((x["url"], "bandcamp" if x["kind"] == "bandcamp" else "plain"))
    return pages


def graph_fetch(
    *, slice_name: str, limit: int | None = None, max_credits: int = 500, refresh: bool = False
) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        if settings.firecrawl_api_key is None:
            raise RuntimeError("FIRECRAWL_API_KEY is not set")
        counts: dict[str, int] = defaultdict(int)
        async with connection(ctx.pool) as conn:
            month = int(await conn.fetchval(MONTH_SPENT))
            if month >= settings.graph_monthly_credits:
                raise RuntimeError(
                    f"this month's Firecrawl credits ({month}) reached GRAPH_MONTHLY_CREDITS "
                    f"({settings.graph_monthly_credits})"
                )
            targets = await slice_targets(conn, slice_name)
            ready = {
                r["entity_id"]
                for r in await conn.fetch(
                    """SELECT entity_id FROM graph_album
                        WHERE baseline_at IS NOT NULL AND ($1 OR fetched_at IS NULL)""",
                    refresh,
                )
            }
        todo = [t for t in targets if t["entity_id"] in ready]
        if limit is not None:
            todo = todo[:limit]
        spent = 0
        stopped = False
        async with FirecrawlClient(settings.firecrawl_api_key.get_secret_value()) as fc:
            for t in todo:
                async with connection(ctx.pool) as conn:
                    links = await conn.fetch(
                        "SELECT kind, url, source FROM entity_link WHERE entity_id = $1 AND status = 'ok'",
                        t["entity_id"],
                    )
                for url, mode in plan(links, order_key(t)[0]):
                    async with connection(ctx.pool) as conn:
                        cached = await conn.fetchval(
                            "SELECT fetch_id FROM source_fetch WHERE url = $1 AND schema_version = $2 AND ok",
                            url,
                            SCHEMA_VERSION[mode],
                        )
                    if cached:
                        counts["cached"] += 1
                        continue
                    if (
                        spent + COST[mode] > max_credits
                        or month + spent + COST[mode] > settings.graph_monthly_credits
                    ):
                        stopped = True
                        break
                    page = await fc.scrape(url, mode)
                    spent += page.credits
                    counts["pages_ok" if page.ok else "pages_failed"] += 1
                    async with connection(ctx.pool) as conn:
                        await conn.execute(
                            """INSERT INTO source_fetch (url, mode, schema_version, entity_id, credits,
                                                         ok, error, title, body, facts, pipeline_run_id)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)""",
                            url,
                            mode,
                            SCHEMA_VERSION[mode],
                            t["entity_id"],
                            page.credits,
                            page.ok,
                            page.error,
                            page.title,
                            page.body,
                            json.dumps(page.facts) if page.facts is not None else None,
                            ctx.run_id,
                        )
                if stopped:
                    break
                async with connection(ctx.pool) as conn:
                    await conn.execute(
                        "UPDATE graph_album SET fetched_at = now() WHERE entity_id = $1",
                        t["entity_id"],
                    )
                counts["albums"] += 1
        ctx.rows = counts["albums"]
        ctx.notes.update(
            slice=slice_name,
            credits=spent,
            month_credits=month + spent,
            stopped_at_cap=stopped,
            **counts,
        )

    return _run

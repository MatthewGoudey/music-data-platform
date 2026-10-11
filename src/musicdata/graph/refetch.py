"""`musicdata graph refetch-missing`: fetch again the pages that claims quote but the database no
longer holds, and send those claims back to `proposed` so `graph verify --pending` checks them
against the page.

A claim whose page is missing fails the Evidence check whatever it quotes. This happened to the
deep dives written in dev (Déjà vu, Stardust, Harvest): `documents copy` carried their claims to
prod but not the pages they quote, and refilling dev from prod then removed the pages there too.
Only claims rejected for the Evidence check alone go back; their reader verdicts stay.
"""

from __future__ import annotations

from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

EVIDENCE_FAILS = (
    "evidence not found verbatim in the cached source",
    "field value not on the cached page",
)

MISSING = """
SELECT a.source_url AS url, min(a.album_context) AS entity_id
  FROM assertion a
 WHERE a.source_url LIKE 'http%' AND a.extractor = 'claude'
   AND a.status IN ('rejected', 'proposed', 'unread')
   AND NOT EXISTS (SELECT 1 FROM source_fetch f WHERE f.url = a.source_url AND f.ok)
 GROUP BY a.source_url ORDER BY a.source_url
"""

REOPEN = """
UPDATE assertion SET status = 'proposed', fails = '{}'
 WHERE source_url = ANY($1::text[]) AND extractor = 'claude' AND status = 'rejected'
   AND fails <@ $2::text[] AND cardinality(fails) > 0
"""


def graph_refetch_missing(*, max_credits: int = 100) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        from musicdata.clients.firecrawl import COST, SCHEMA_VERSION, FirecrawlClient
        from musicdata.worker import MONTH_SPENT

        settings = get_settings()
        if settings.firecrawl_api_key is None:
            raise RuntimeError("FIRECRAWL_API_KEY is not set")
        async with connection(ctx.pool) as conn:
            missing = await conn.fetch(MISSING)
            spent_month = int(await conn.fetchval(MONTH_SPENT))
        fetched, failed, credits = [], [], 0
        async with FirecrawlClient(settings.firecrawl_api_key.get_secret_value()) as fc:
            for m in missing:
                if credits + COST["plain"] > max_credits or (
                    spent_month + credits + COST["plain"] > settings.graph_monthly_credits
                ):
                    break
                page = await fc.scrape(m["url"], "plain")
                credits += page.credits
                async with connection(ctx.pool) as conn:
                    await conn.execute(
                        """INSERT INTO source_fetch (url, mode, schema_version, entity_id, credits,
                                                     ok, error, title, body)
                           VALUES ($1, 'plain', $2, $3, $4, $5, $6, $7, $8)""",
                        m["url"], SCHEMA_VERSION["plain"], m["entity_id"], page.credits, page.ok,
                        page.error, page.title, page.body,
                    )  # fmt: skip
                (fetched if page.ok else failed).append(m["url"])
        async with connection(ctx.pool) as conn:
            reopened = await conn.execute(REOPEN, fetched, list(EVIDENCE_FAILS))
        ctx.rows = len(fetched)
        ctx.notes.update(
            missing=len(missing), fetched=len(fetched), failed=failed, credits=credits,
            reopened=int(reopened.split()[-1]),
        )  # fmt: skip
        log.info("graph refetch-missing", extra=dict(ctx.notes))

    return _run

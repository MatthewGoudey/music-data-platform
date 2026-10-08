"""The ingest job: ListenBrainz → listen rows, identity assigned on the way in.

Incremental runs page back from now to three days below the newest stored listen, so
listens that reach ListenBrainz late (offline players, imports) still land; the natural
key makes the overlap free. `full` pages back through the rest of the history, starting
from the oldest stored listen, so a full load that stopped part-way resumes. Every run ends
by recording the ListenBrainz listen-count beside the database count for the dq check.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from musicdata.clients.listenbrainz import ListenBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.ingest.identity import IdentityIndex, PageResult
from musicdata.ingest.parse import parse_listen
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.log import get_logger

log = get_logger(__name__)

OVERLAP = timedelta(days=3)


ORPHANS = """
    DELETE FROM release_group rg
     WHERE rg.mbid IS NULL
       AND NOT EXISTS (SELECT 1 FROM listen l WHERE l.release_group_id = rg.release_group_id);
    DELETE FROM artist a
     WHERE a.mbid IS NULL
       AND NOT EXISTS (SELECT 1 FROM listen l WHERE l.artist_id = a.artist_id)
       AND NOT EXISTS (SELECT 1 FROM release_group rg WHERE rg.artist_id = a.artist_id);
"""


def ingest(*, full: bool = False, since: datetime | None = None, rekey: bool = False) -> JobFn:
    """`rekey` pages the whole history from now, moves stored listens onto the identity
    today's rules give them, then drops the unmapped artists and groups left empty."""

    async def _run(ctx: RunContext) -> None:
        settings = get_settings()
        async with connection(ctx.pool) as conn:
            watermark, oldest = await conn.fetchrow(
                "SELECT max(listened_at), min(listened_at) FROM listen"
            )
            index = await IdentityIndex.load(conn)

        start_before = None
        if rekey:
            stop_before = since
        elif since is not None:
            stop_before = since
        elif full or watermark is None:
            stop_before = None
            start_before = oldest
        else:
            stop_before = watermark - OVERLAP
        ctx.notes.update(
            mode="rekey" if rekey else "full" if stop_before is None else "incremental",
            watermark=watermark,
            start_before=start_before,
            stop_before=stop_before,
        )

        totals = PageResult()
        pages = parsed = skipped = 0
        async with ListenBrainzClient(
            settings.listenbrainz_user, user_agent=settings.musicbrainz_user_agent
        ) as lb:
            async for raw_page in lb.pages_back_to(stop_before, start_before=start_before):
                page = [p for p in (parse_listen(r) for r in raw_page) if p is not None]
                skipped += len(raw_page) - len(page)
                if stop_before is not None:
                    page = [p for p in page if p.listened_at >= stop_before]
                pages += 1
                parsed += len(page)
                if page:
                    async with connection(ctx.pool) as conn:
                        result = await index.write_page(conn, page, rekey=rekey)
                    for k, v in asdict(result).items():
                        setattr(totals, k, getattr(totals, k) + v)
                if pages % 25 == 0:
                    log.info("ingest progress", extra={"pages": pages, **asdict(totals)})
            lb_count = await lb.listen_count()

        async with connection(ctx.pool) as conn:
            if rekey:
                async with conn.transaction():
                    await conn.execute(ORPHANS)
            db_count = await conn.fetchval("SELECT count(*) FROM listen")
        ctx.rows = totals.listens_inserted
        ctx.notes.update(
            pages=pages,
            parsed=parsed,
            skipped=skipped,
            listenbrainz_count=lb_count,
            database_count=db_count,
            **asdict(totals),
        )

    return _run

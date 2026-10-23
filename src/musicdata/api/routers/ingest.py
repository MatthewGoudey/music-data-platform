"""Catch-up ingest: pull listens since the watermark, then rebuild their sessions.

Runs in the background inside the API process, at most once per five minutes: a run
of either job that started in the last five minutes, or one still running (a full load,
the nightly sync), means no new one starts. Each half is a normal job, so it leaves
pipeline_run rows with trigger 'catch-up'.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from musicdata.api.deps import Pool, require_token
from musicdata.db import connection
from musicdata.log import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])

MIN_INTERVAL = "5 minutes"
_running: set[asyncio.Task] = set()


async def _catch_up() -> None:
    from musicdata.derive.job import derive
    from musicdata.ingest.job import ingest
    from musicdata.jobs.runs import record

    if await record("ingest", ingest(), trigger="catch-up") == 0:
        if await record("derive", derive(catch_up=True), trigger="catch-up") == 0:
            from musicdata.graph.connections import graph_threads

            await record("graph_threads", graph_threads(), trigger="catch-up")


async def start_catch_up(pool) -> bool:
    """Start a catch-up unless one ran recently; True when one was started."""
    if _running:
        return False
    async with connection(pool) as conn:
        # A catch-up lives in this process and takes seconds; one still 'running' after 15
        # minutes died with its machine (out of memory, autostop) and blocks nothing.
        await conn.execute(
            """UPDATE pipeline_run SET status = 'failed', finished_at = now(),
                      error = 'interrupted: the API process ended before the job finished'
                WHERE trigger = 'catch-up' AND status = 'running'
                  AND started_at < now() - interval '15 minutes'"""
        )
        recent = await conn.fetchval(
            f"""SELECT 1 FROM pipeline_run
                 WHERE job IN ('ingest', 'derive')
                   AND (started_at > now() - interval '{MIN_INTERVAL}'
                        OR (status = 'running' AND started_at > now() - interval '6 hours'))
                 LIMIT 1"""
        )
    if recent:
        return False
    task = asyncio.create_task(_catch_up())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return True


@router.post("/catch-up", status_code=202, dependencies=[Depends(require_token)])
async def catch_up(pool: Pool):
    """Pull new listens now. Rate-limited to once per five minutes."""
    started = await start_catch_up(pool)
    return {"started": started, "min_interval": MIN_INTERVAL}

"""`musicdata graph near-queue` (companion spec 2 and 3.3): the near-queue set, the top 300
scored candidates of each queue profile, recomputed nightly.

Each album in the set gets its album entity (created when missing) and a `graph_album` row in the
`near_queue` slice with its best rank across profiles in `notes.near_queue_rank`; albums that left
the set lose the slice. The default scope reads the slice as its second group, so the albums Matt
sees next are covered first. Reads the queue; writes only graph tables.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg

from musicdata.db import connection
from musicdata.graph.importer import ensure_album
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.queue.engine import CHICAGO, load_profile, score_context

NEAR = 300


async def near_queue_ranks(
    conn: asyncpg.Connection, now: datetime | None = None, size: int = NEAR
) -> tuple[dict[int, int], int]:
    """Each release group in some profile's top `size`, with its best rank; and the profile count."""
    now = now or datetime.now(CHICAGO)
    sc = await score_context(conn)
    names = [r[0] for r in await conn.fetch("SELECT name FROM queue_profile ORDER BY name")]
    ranks: dict[int, int] = {}
    for name in names:
        for i, item in enumerate(sc.score(await load_profile(conn, name), now)[:size], 1):
            ranks[item.release_group_id] = min(ranks.get(item.release_group_id, i), i)
    return ranks, len(names)


async def store_near_queue(conn: asyncpg.Connection, ranks: dict[int, int]) -> dict[str, int]:
    async with conn.transaction():
        left = await conn.execute(
            """UPDATE graph_album SET slices = array_remove(slices, 'near_queue'),
                      notes = notes - 'near_queue_rank'
                WHERE 'near_queue' = ANY(slices)"""
        )
        created = 0
        for rg, rank in sorted(ranks.items(), key=lambda x: (x[1], x[0])):
            had = await conn.fetchval("SELECT 1 FROM entity WHERE release_group_id = $1", rg)
            t = await ensure_album(conn, rg)
            if t is None:
                continue
            created += not had
            await conn.execute(
                """INSERT INTO graph_album (entity_id, slices, notes)
                   VALUES ($1, ARRAY['near_queue'], jsonb_build_object('near_queue_rank', $2::int))
                   ON CONFLICT (entity_id) DO UPDATE
                      SET slices = CASE WHEN 'near_queue' = ANY(graph_album.slices)
                                        THEN graph_album.slices
                                        ELSE graph_album.slices || 'near_queue'::text END,
                          notes = graph_album.notes
                                  || jsonb_build_object('near_queue_rank', $2::int)""",
                t["entity_id"],
                rank,
            )
        baselined = await conn.fetchval(
            """SELECT count(*) FROM graph_album
                WHERE 'near_queue' = ANY(slices) AND baseline_at IS NOT NULL"""
        )
    return {
        "albums": len(ranks),
        "entities_created": created,
        "baselined": int(baselined),
        "previous": int(left.split()[-1]),
    }


def graph_near_queue(size: int = NEAR) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            ranks, profiles = await near_queue_ranks(conn, size=size)
            counts = await store_near_queue(conn, ranks)
        ctx.rows = counts["albums"]
        ctx.notes.update(profiles=profiles, size=size, **counts)

    return _run

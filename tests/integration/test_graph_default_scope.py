"""The default scope (companion spec 3.3) and the near-queue set, against Neon dev, rolled back;
made-up MBIDs and names."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.graph.importer import DEFAULT_ORDER, scope_targets
from musicdata.graph.near_queue import near_queue_ranks, store_near_queue

pytestmark = pytest.mark.integration
MBID = "00000000-0000-4000-f000-{:012d}"


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _album(conn, artist: int, n: int) -> int:
    return await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, primary_type, mbid)
           VALUES ($1, $2, $3, 'Album', $4) RETURNING release_group_id""",
        artist,
        f"Zz Scope Record {n}",
        f"zz scope record {n}",
        MBID.format(n),
    )


async def test_default_scope_order_and_steps(conn) -> None:
    # Clear the two slices the test sets, inside the rolled-back transaction.
    await conn.execute(
        """UPDATE graph_album SET slices = array_remove(array_remove(slices, 'opened'),
                                                        'near_queue')"""
    )
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Scope', 'zz scope') RETURNING artist_id"
    )
    opened, near_2, near_1 = [await _album(conn, artist, n) for n in (1, 2, 3)]
    await store_near_queue(conn, {near_1: 1, near_2: 2})
    t = (await scope_targets(conn, f"rg:{opened}"))[0]
    await conn.execute(
        "INSERT INTO graph_album (entity_id, slices) VALUES ($1, ARRAY['opened'])", t["entity_id"]
    )

    first = [r["rg"] for r in await conn.fetch(DEFAULT_ORDER, "baseline", 3)]
    assert first == [opened, near_1, near_2]  # opened pages, then the near-queue set by rank

    targets = await scope_targets(conn, "default", "baseline", 2)
    assert [x["release_group_id"] for x in targets] == [opened, near_1]

    # A baselined album leaves the baseline step and enters the fetch step.
    await conn.execute(
        "UPDATE graph_album SET baseline_at = now() WHERE entity_id = $1", t["entity_id"]
    )
    assert opened not in [r["rg"] for r in await conn.fetch(DEFAULT_ORDER, "baseline", 3)]
    assert [r["rg"] for r in await conn.fetch(DEFAULT_ORDER, "fetch", 1)] == [opened]

    # Recomputing the set drops albums that left it.
    await store_near_queue(conn, {near_2: 1})
    ranked = await conn.fetch(
        """SELECT e.release_group_id, (g.notes ->> 'near_queue_rank')::int AS rank
             FROM graph_album g JOIN entity e USING (entity_id)
            WHERE 'near_queue' = ANY(g.slices)"""
    )
    assert [(r["release_group_id"], r["rank"]) for r in ranked] == [(near_2, 1)]


async def test_near_queue_ranks_cover_each_profile(conn) -> None:
    ranks, profiles = await near_queue_ranks(conn, size=5)
    assert profiles >= 1
    assert len(ranks) <= 5 * profiles  # CI's database has no lists, so the set may be empty
    assert not ranks or min(ranks.values()) == 1

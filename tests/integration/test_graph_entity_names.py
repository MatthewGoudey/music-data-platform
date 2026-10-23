"""An import that renames an MBID entity keeps the old name (graph spec v13), against Neon dev,
rolled back; made-up MBIDs and names."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.graph.baseline import Ent
from musicdata.graph.importer import ensure_entity

pytestmark = pytest.mark.integration
MBID = "00000000-0000-4000-e000-{:012d}"


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


async def _names(conn, eid: int) -> tuple[str, list[str]]:
    r = await conn.fetchrow(
        "SELECT name, attrs -> 'names' AS names FROM entity WHERE entity_id = $1", eid
    )
    return r["name"], json.loads(r["names"]) if r["names"] else []


async def test_rename_keeps_each_old_name_once(conn) -> None:
    mbid = MBID.format(31001)
    eid = await ensure_entity(conn, Ent("artist", "Zzq Family Test", mbid), {})
    assert await _names(conn, eid) == ("Zzq Family Test", [])
    assert await ensure_entity(conn, Ent("person", "The Zzq Family Test", mbid), {}) == eid
    assert await _names(conn, eid) == ("The Zzq Family Test", ["Zzq Family Test"])
    for _ in range(2):  # back and forth: both names kept, neither twice
        await ensure_entity(conn, Ent("artist", "Zzq Family Test", mbid), {})
        await ensure_entity(conn, Ent("artist", "The Zzq Family Test", mbid), {})
    both = ["Zzq Family Test", "The Zzq Family Test"]
    assert await _names(conn, eid) == ("The Zzq Family Test", both)


async def test_rename_of_a_non_artist_keeps_the_old_name(conn) -> None:
    mbid = MBID.format(31002)
    eid = await ensure_entity(conn, Ent("place", "Zzq Sound Test", mbid), {})
    await ensure_entity(conn, Ent("place", "Zzq Sound Studios Test", mbid), {})
    assert await _names(conn, eid) == ("Zzq Sound Studios Test", ["Zzq Sound Test"])

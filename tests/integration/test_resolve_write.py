"""write_resolution / write_unresolved against a real database. Rolled back after each test."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from musicdata.resolve.canonical import resolve_group
from musicdata.resolve.job import write_resolution, write_unresolved

pytestmark = pytest.mark.integration

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "unit" / "fixtures" / "mb_agaetis_byrjun.json").read_text("utf-8")
)
RG_MBID = "00000000-0000-4000-8000-0000000000aa"  # not a real group: clear of loaded data


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


async def _group(conn) -> int:
    artist_id = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Test Sigur', 'zz test sigur') "
        "RETURNING artist_id"
    )
    return await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, mbid)
           VALUES ($1, 'Agaetis Byrjun (Remastered)', 'agaetis byrjun', $2)
           RETURNING release_group_id""",
        artist_id,
        RG_MBID,
    )


async def test_a_resolution_stores_metadata_and_the_tracklist(conn) -> None:
    rg_id = await _group(conn)
    res = resolve_group(FIXTURE["releases"])
    assert res is not None
    await write_resolution(conn, rg_id, res)
    await write_resolution(conn, rg_id, res)  # a second pass replaces, never duplicates
    rg = await conn.fetchrow(
        """SELECT rg.title, rg.primary_type, rg.first_release_year, a.mbid::text AS artist_mbid,
                  t.track_count, t.source
             FROM release_group rg JOIN artist a USING (artist_id)
             JOIN release_group_tracklist t USING (release_group_id)
            WHERE rg.release_group_id = $1""",
        rg_id,
    )
    assert dict(rg) == {
        "title": "Ágætis byrjun",
        "primary_type": "Album",
        "first_release_year": 1999,
        "artist_mbid": "f6f2326f-6b25-4170-b89d-e235b25508e8",
        "track_count": 10,
        "source": "musicbrainz",
    }
    n = await conn.fetchval(
        "SELECT count(*) FROM release_group_track WHERE release_group_id = $1", rg_id
    )
    assert n == 10


async def test_unresolved_never_overwrites_a_resolution(conn) -> None:
    rg_id = await _group(conn)
    await write_unresolved(conn, rg_id, "first try failed")
    res = resolve_group(FIXTURE["releases"])
    await write_resolution(conn, rg_id, res)
    await write_unresolved(conn, rg_id, "a later failure")
    source = await conn.fetchval(
        "SELECT source FROM release_group_tracklist WHERE release_group_id = $1", rg_id
    )
    assert source == "musicbrainz"

"""Resolving list entries against a real database, without MusicBrainz. Rolled back after
each test; made-up MBIDs and names."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.lists.match import Candidate
from musicdata.lists.resolve import Album, Known, ensure_release_group, settle
from musicdata.resolve.unmapped import merge_release_group

pytestmark = pytest.mark.integration


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


async def _group(conn, artist_id: int, title: str, key: str, year: int | None, mbid=None) -> int:
    return await conn.fetchval(
        """INSERT INTO release_group (mbid, artist_id, title, norm_key, first_release_year)
           VALUES ($1, $2, $3, $4, $5) RETURNING release_group_id""",
        mbid,
        artist_id,
        title,
        key,
        year,
    )


async def test_a_known_album_resolves_by_key_and_year(conn) -> None:
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Selftitled', 'zz selftitled') "
        "RETURNING artist_id"
    )
    blue = await _group(
        conn, artist, "Zz Selftitled", "zz selftitled", 1994, "00000000-0000-4000-8000-0000000000b1"
    )
    green = await _group(
        conn, artist, "Zz Selftitled", "zz selftitled", 2001, "00000000-0000-4000-8000-0000000000b2"
    )
    known = await Known.load(conn)
    album = Album("Zz Selftitled", "Zz Selftitled", "zz selftitled", "zz selftitled", 2001)
    assert known.find(album) == green
    album.year = 1993
    assert known.find(album) == blue
    album.year = None
    assert known.find(album) is None  # two candidates: MusicBrainz decides


async def test_a_new_group_is_created_and_a_merge_carries_its_entries(conn) -> None:
    c = Candidate(
        mbid="00000000-0000-4000-8000-0000000000c1",
        title="Zz Record (Deluxe Edition)",
        year=2010,
        primary_type="Album",
        secondary_types=(),
        artist_mbid="00000000-0000-4000-8000-0000000000a1",
        artist_name="Zz Newcomer",
    )
    rg_id, artist_id = await ensure_release_group(conn, c)
    assert await ensure_release_group(conn, c) == (rg_id, artist_id)
    assert (
        await conn.fetchval("SELECT norm_key FROM release_group WHERE release_group_id = $1", rg_id)
        == "zz record"
    )

    list_id = await conn.fetchval(
        "INSERT INTO list (slug, name, goal) VALUES ('zz_resolve', 'Zz', 'canon') RETURNING list_id"
    )
    entry = await conn.fetchval(
        """INSERT INTO list_entry (list_id, raw_artist, raw_album, artist_key, album_key)
           VALUES ($1, 'Zz Newcomer', 'Zz Record', 'zz newcomer', 'zz record')
           RETURNING entry_id""",
        list_id,
    )
    unmapped = await _group(conn, artist_id, "Zz Record", "zz record", None)
    await settle(conn, [entry], "resolved", unmapped, {"via": "known"})
    await merge_release_group(conn, unmapped, rg_id)
    assert (
        await conn.fetchval("SELECT release_group_id FROM list_entry WHERE entry_id = $1", entry)
        == rg_id
    )

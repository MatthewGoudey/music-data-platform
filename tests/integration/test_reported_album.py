"""Listens go home to the album the player reported (derive/reported.py). Rolled back after;
made-up MBIDs and names."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from musicdata.derive.reported import go_home

pytestmark = pytest.mark.integration

T0 = datetime(1999, 1, 1, 12, tzinfo=UTC)


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


async def _group(conn, artist: int, title: str, key: str, n: int) -> int:
    return await conn.fetchval(
        """INSERT INTO release_group (mbid, artist_id, title, norm_key, primary_type)
           VALUES ($1, $2, $3, $4, 'Album') RETURNING release_group_id""",
        f"00000000-0000-4000-b000-{n:012d}",
        artist,
        title,
        key,
    )


async def _listen(conn, artist: int, rg: int, minute: int, release_name: str) -> int:
    return await conn.fetchval(
        """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name, norm_title,
                               artist_name, release_name)
           VALUES ($1, $2, $3, $4, $4, 'Zz Home Artist', $5) RETURNING listen_id""",
        T0 + timedelta(minutes=minute),
        artist,
        rg,
        f"zz track {minute}",
        release_name,
    )


async def test_a_scattered_album_goes_home_and_a_compilation_play_stays(conn) -> None:
    artist = await conn.fetchval(
        """INSERT INTO artist (name, norm_key, mbid)
           VALUES ('Zz Home Artist', 'zz home artist', '00000000-0000-4000-b000-0000000000aa')
           RETURNING artist_id"""
    )
    home = await _group(conn, artist, "Zz Home Album", "zz home album", 1)
    hits = await _group(conn, artist, "Zz Greatest Hits", "zz greatest hits", 2)
    scattered = [
        await _listen(conn, artist, hits, m, "Zz Home Album (Legacy Edition)") for m in range(3)
    ]
    from_hits = await _listen(conn, artist, hits, 10, "Zz Greatest Hits")
    result = await go_home(conn)
    assert result["listens_sent_home"] >= 3
    rows = {
        r["listen_id"]: (r["release_group_id"], r["reported_key"])
        for r in await conn.fetch(
            "SELECT listen_id, release_group_id, reported_key FROM listen WHERE listen_id = ANY($1)",
            [*scattered, from_hits],
        )
    }
    assert all(rows[i] == (home, "zz home album") for i in scattered)
    assert rows[from_hits] == (hits, "zz greatest hits")


async def test_two_homes_with_one_key_leave_the_listen_alone(conn) -> None:
    artist = await conn.fetchval(
        """INSERT INTO artist (name, norm_key, mbid)
           VALUES ('Zz Home Artist', 'zz home artist', '00000000-0000-4000-b000-0000000000ab')
           RETURNING artist_id"""
    )
    await _group(conn, artist, "Zz Twin", "zz twin", 3)
    await _group(conn, artist, "Zz Twin", "zz twin", 4)
    other = await _group(conn, artist, "Zz Other", "zz other", 5)
    lid = await _listen(conn, artist, other, 20, "Zz Twin")
    await go_home(conn)
    assert (
        await conn.fetchval("SELECT release_group_id FROM listen WHERE listen_id = $1", lid)
        == other
    )


async def test_an_expanded_edition_and_an_unmapped_namesake_are_not_homes(conn) -> None:
    artist = await conn.fetchval(
        """INSERT INTO artist (name, norm_key, mbid)
           VALUES ('Zz Home Artist', 'zz home artist', '00000000-0000-4000-b000-0000000000ac')
           RETURNING artist_id"""
    )
    album = await _group(conn, artist, "Zz Play", "zz play", 6)
    await _group(
        conn, artist, "Zz Play & Zz Play: The B Sides", "zz play and zz play the b sides", 7
    )
    await conn.execute(
        """INSERT INTO release_group (artist_id, title, norm_key)
           VALUES ($1, 'Zz Remastered Namesake', 'zz remastered namesake')""",
        artist,
    )
    expanded = await _listen(conn, artist, album, 30, "Zz Play & Zz Play: The B Sides")
    namesake = await _listen(conn, artist, album, 31, "Zz Remastered Namesake")
    await go_home(conn)
    rows = await conn.fetch(
        "SELECT release_group_id FROM listen WHERE listen_id = ANY($1)", [expanded, namesake]
    )
    assert {r["release_group_id"] for r in rows} == {album}

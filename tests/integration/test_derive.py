"""Session rebuild for one release group against a real database. Rolled back afterwards."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import pytest

from musicdata.derive.job import REBUILD_STATS, _rebuild_chunk

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


async def _album(conn, primary_type: str = "Album") -> tuple[int, int]:
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Derive', 'zz derive') RETURNING artist_id"
    )
    rg = await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, primary_type)
           VALUES ($1, 'Zz Album', 'zz album', $2) RETURNING release_group_id""",
        artist,
        primary_type,
    )
    await conn.execute(
        """INSERT INTO release_group_tracklist (release_group_id, track_count, source)
           VALUES ($1, 5, 'musicbrainz')""",
        rg,
    )
    for p in range(1, 6):
        await conn.execute(
            """INSERT INTO release_group_track (release_group_id, position, title, norm_title)
               VALUES ($1, $2, $3, $3)""",
            rg,
            p,
            f"song {p}",
        )
    for n, p in enumerate([1, 2, 3, 4, 5, 1, 2, 3]):  # one full play, a later partial
        minutes = n * 4 if n < 5 else 300 + n * 4
        await conn.execute(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                                   norm_title, artist_name)
               VALUES (to_timestamp(946000000) + make_interval(mins => $1), $2, $3, $4, $4,
                       'Zz Derive')""",
            minutes,
            artist,
            rg,
            f"song {p}",
        )
    return artist, rg


async def test_rebuild_writes_sessions_and_is_repeatable(conn) -> None:
    _, rg = await _album(conn)
    assert await _rebuild_chunk(conn, [rg]) == 2
    assert await _rebuild_chunk(conn, [rg]) == 2
    rows = await conn.fetch(
        """SELECT session_type, tracks_played, completion FROM album_session
            WHERE release_group_id = $1 ORDER BY started_at""",
        rg,
    )
    assert [tuple(r) for r in rows] == [("full", 5, Decimal("1")), ("partial", 3, Decimal("0.6"))]


async def test_stats_count_a_respelled_recording_once_and_bonus_titles_apart(conn) -> None:
    artist, rg = await _album(conn)
    mbid = "00000000-0000-4000-8000-0000000000c1"
    await conn.execute(
        "UPDATE release_group_track SET recording_mbid = $2 WHERE release_group_id = $1 "
        "AND position = 1",
        rg,
        mbid,
    )
    for n, (title, rec) in enumerate([("song one alt take", mbid), ("bonus x", None)]):
        await conn.execute(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                                   norm_title, artist_name, recording_mbid)
               VALUES (to_timestamp(947000000 + $1), $2, $3, $4, $4, 'Zz Derive', $5)""",
            n,
            artist,
            rg,
            title,
            rec,
        )
    await conn.execute(REBUILD_STATS)
    row = await conn.fetchrow(
        "SELECT distinct_tracks, tracks_heard, track_count FROM release_group_stat "
        "WHERE release_group_id = $1",
        rg,
    )
    assert dict(row) == {"distinct_tracks": 6, "tracks_heard": 5, "track_count": 5}


async def test_singles_get_no_sessions_and_lose_old_ones(conn) -> None:
    _, rg = await _album(conn)
    await _rebuild_chunk(conn, [rg])
    await conn.execute(
        "UPDATE release_group SET primary_type = 'Single' WHERE release_group_id = $1", rg
    )
    assert await _rebuild_chunk(conn, [rg]) == 0
    assert (
        await conn.fetchval("SELECT count(*) FROM album_session WHERE release_group_id = $1", rg)
        == 0
    )


async def test_manual_sessions_survive_a_rebuild(conn) -> None:
    _, rg = await _album(conn)
    await conn.execute(
        """INSERT INTO album_session (release_group_id, started_at, ended_at, tracks_played,
                                      track_count, completion, session_type, source)
           VALUES ($1, '2001-01-01', '2001-01-01', 5, 5, 1, 'full', 'manual')""",
        rg,
    )
    await _rebuild_chunk(conn, [rg])
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM album_session WHERE release_group_id = $1 AND source = 'manual'",
            rg,
        )
        == 1
    )

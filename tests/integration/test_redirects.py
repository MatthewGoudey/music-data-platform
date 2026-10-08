"""Stray-track redirects and album-artist grouping against a real database. Rolled back."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.derive.redirects import apply_redirects
from musicdata.ingest.identity import IdentityIndex
from musicdata.ingest.parse import parse_listen

pytestmark = pytest.mark.integration

REC = [f"00000000-0000-4000-8000-0000000002{i:02d}" for i in range(4)]
ALBUM = "00000000-0000-4000-8000-000000000301"
SINGLE = "00000000-0000-4000-8000-000000000302"


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


async def _group(conn, artist: int, mbid: str, kind: str, recs: list[str]) -> int:
    rg = await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, mbid, primary_type)
           VALUES ($1, 'Zz Redirect', 'zz redirect', $2, $3) RETURNING release_group_id""",
        artist,
        mbid,
        kind,
    )
    await conn.execute(
        """INSERT INTO release_group_tracklist (release_group_id, track_count, source)
           VALUES ($1, $2, 'musicbrainz')""",
        rg,
        len(recs),
    )
    for i, rec in enumerate(recs, 1):
        await conn.execute(
            """INSERT INTO release_group_track
                      (release_group_id, position, title, norm_title, recording_mbid)
               VALUES ($1, $2, $3, $3, $4)""",
            rg,
            i,
            f"zz track {i}",
            rec,
        )
    return rg


async def test_a_stray_track_moves_to_the_album_around_it(conn) -> None:
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Redir', 'zz redir') RETURNING artist_id"
    )
    album = await _group(conn, artist, ALBUM, "Album", REC[:3])
    single = await _group(conn, artist, SINGLE, "Single", [REC[1]])
    plays = [(album, REC[0], 1), (single, REC[1], 2), (album, REC[2], 3)]
    for rg, rec, n in plays:
        await conn.execute(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                                   norm_title, artist_name, recording_mbid)
               VALUES (to_timestamp(940000000) + make_interval(mins => 4 * $1), $2, $3,
                       $4, $4, 'Zz Redir', $5)""",
            n,
            artist,
            rg,
            f"zz track {n}",
            rec,
        )
    first = await apply_redirects(conn)
    assert first["redirects_learned"] >= 1 and first["listens_moved"] >= 1
    on_album = await conn.fetchval("SELECT count(*) FROM listen WHERE release_group_id = $1", album)
    assert on_album == 3
    again = await apply_redirects(conn)
    assert again == {"redirects_learned": 0, "listens_moved": 0}


async def test_a_soundtrack_credited_per_track_is_one_album(conn) -> None:
    index = await IdentityIndex.load(conn)

    def listen(ts: int, artist: str, track: str):
        return parse_listen(
            {
                "listened_at": ts,
                "track_metadata": {
                    "artist_name": artist,
                    "track_name": track,
                    "release_name": "Zz Spider Verse",
                    "additional_info": {"release_artist_name": "Zz Various Artists"},
                },
            }
        )

    await index.write_page(
        conn,
        [listen(940000100, "Zz Khalil, Zz Curry", "Scared"), listen(940000400, "Zz Post", "Sun")],
    )
    groups = await conn.fetchval(
        """SELECT count(DISTINCT release_group_id) FROM listen
            WHERE listened_at IN (to_timestamp(940000100), to_timestamp(940000400))"""
    )
    assert groups == 1

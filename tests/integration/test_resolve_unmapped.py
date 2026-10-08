"""The unmapped tail merges into a local namesake by overlap. Rolled back after each test.

Runs resolve_unmapped against a single-connection 'pool' bound to the test transaction,
with a MusicBrainz client that fails the test if it is called: local candidates need
no request.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest

from musicdata.resolve.unmapped import merge_release_group, resolve_unmapped

pytestmark = pytest.mark.integration

RG_A = "00000000-0000-4000-8000-0000000000a1"
RG_B = "00000000-0000-4000-8000-0000000000b2"


class _OnePool:
    """Quacks like asyncpg.Pool for musicdata.db.connection: always the same connection."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class _NoMusicBrainz:
    async def search_release_groups(self, *a, **k):
        raise AssertionError("local candidates must not need MusicBrainz")

    async def releases_of_group(self, *a, **k):
        raise AssertionError("local candidates must not need MusicBrainz")


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


async def _setup(conn) -> tuple[int, int, int, int]:
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Fall', 'zz fall') RETURNING artist_id"
    )
    groups = []
    for mbid, tracks in ((RG_A, ["industrial estate", "rebellious jukebox"]), (RG_B, ["intro"])):
        rg = await conn.fetchval(
            """INSERT INTO release_group (artist_id, title, norm_key, mbid)
               VALUES ($1, 'Zz Witch Trials', 'zz witch trials', $2) RETURNING release_group_id""",
            artist,
            mbid,
        )
        await conn.execute(
            """INSERT INTO release_group_tracklist (release_group_id, track_count, source)
               VALUES ($1, $2, 'musicbrainz')""",
            rg,
            len(tracks),
        )
        for i, t in enumerate(tracks, 1):
            await conn.execute(
                """INSERT INTO release_group_track (release_group_id, position, title, norm_title)
                   VALUES ($1, $2, $3, $3)""",
                rg,
                i,
                t,
            )
        groups.append(rg)
    unmapped = await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key)
           VALUES ($1, 'Zz Witch Trials', 'zz witch trials') RETURNING release_group_id""",
        artist,
    )
    for i, title in enumerate(["industrial estate", "rebellious jukebox", "industrial estate"]):
        await conn.execute(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                                   norm_title, artist_name)
               VALUES (to_timestamp($1), $2, $3, $4, $4, 'Zz Fall')""",
            946000000 + i,
            artist,
            unmapped,
            title,
        )
    return artist, groups[0], groups[1], unmapped


async def test_an_unmapped_group_merges_into_the_namesake_it_overlaps(conn) -> None:
    _, rg_a, _, unmapped = await _setup(conn)
    counts = await resolve_unmapped(
        _OnePool(conn), _NoMusicBrainz(), limit=10, min_listens=3, release_group_ids=[unmapped]
    )
    assert counts["merged_locally"] == 1  # the free local pass, before any search
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM release_group WHERE release_group_id = $1", unmapped
        )
        == 0
    )
    assert await conn.fetchval("SELECT count(*) FROM listen WHERE release_group_id = $1", rg_a) == 3


async def test_merge_moves_aliases(conn) -> None:
    artist, rg_a, _, unmapped = await _setup(conn)
    await conn.execute(
        """INSERT INTO release_group_alias (artist_id, raw_album, release_group_id, source)
           VALUES ($1, 'Zz Witch Trials (Live)', $2, 'listenbrainz')""",
        artist,
        unmapped,
    )
    await merge_release_group(conn, unmapped, rg_a)
    assert (
        await conn.fetchval(
            "SELECT release_group_id FROM release_group_alias WHERE raw_album = 'Zz Witch Trials (Live)'"
        )
        == rg_a
    )

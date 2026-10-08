"""The identity and listen constraints that make audit findings impossible by construction.

Each test runs in a transaction that is rolled back, so it leaves nothing behind in the
database it points at (Neon dev on the laptop, a service container in CI).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

pytestmark = pytest.mark.integration

MBID_A = "f6f2326f-6b25-4170-b89d-e235b25508e8"
MBID_B = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
MBID_C = "9c691c15-dba1-42c8-a99b-0aead1ec0bd4"


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


async def _artist(c: asyncpg.Connection, name: str, key: str, mbid: str | None = None) -> int:
    return await c.fetchval(
        "INSERT INTO artist (name, norm_key, mbid) VALUES ($1, $2, $3) RETURNING artist_id",
        name,
        key,
        mbid,
    )


async def test_unmapped_artists_cannot_share_a_norm_key(conn) -> None:
    await _artist(conn, "The Beatles", "zz test beatles")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _artist(conn, "Beatles", "zz test beatles")


async def test_mapped_artists_with_one_name_stay_distinct(conn) -> None:
    await _artist(conn, "Nirvana", "zz test nirvana", MBID_A)
    await _artist(conn, "Nirvana", "zz test nirvana", MBID_B)
    n = await conn.fetchval("SELECT count(*) FROM artist WHERE norm_key = 'zz test nirvana'")
    assert n == 2


async def test_artist_key_is_never_empty(conn) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _artist(conn, "  ", "")


async def test_unmapped_release_groups_are_unique_per_artist(conn) -> None:
    a = await _artist(conn, "Weezer", "zz test weezer")
    sql = "INSERT INTO release_group (artist_id, title, norm_key, mbid) VALUES ($1, $2, $3, $4)"
    await conn.execute(sql, a, "Weezer", "weezer", MBID_A)
    await conn.execute(sql, a, "Weezer", "weezer", MBID_B)  # Blue and Green albums
    await conn.execute(sql, a, "Weezer (Deluxe)", "weezer", None)
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(sql, a, "Weezer [Remastered]", "weezer", None)


async def test_listen_needs_an_artist(conn) -> None:
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute(
            """INSERT INTO listen (listened_at, artist_id, track_name, norm_title, artist_name)
               VALUES (now(), NULL, 'Song', 'song', 'Nobody')"""
        )


async def test_relistening_the_same_listen_is_idempotent(conn) -> None:
    a = await _artist(conn, "Sigur Rós", "zz test sigur ros", MBID_C)
    sql = """INSERT INTO listen (listened_at, artist_id, track_name, norm_title, artist_name)
             VALUES ('2026-10-01T12:00:00Z', $1, $2, $3, 'Sigur Rós')
             ON CONFLICT (listened_at, artist_id, norm_title) DO NOTHING"""
    first = await conn.execute(sql, a, "Starálfur", "staralfur")
    again = await conn.execute(sql, a, "Staralfur", "staralfur")
    assert (first, again) == ("INSERT 0 1", "INSERT 0 0")

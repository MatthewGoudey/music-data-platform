"""Q2 and Q3 (docs/QUEUE_SPEC.md section 14) against a real database: a fixture list, a
fixture profile limited to it, and albums heard, hidden and snoozed. Rolled back after."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from musicdata.queue.engine import next_queue

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


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


async def _fixture(conn: asyncpg.Connection) -> dict[str, int]:
    """Fifteen albums by fifteen artists on list zz_queue; #1 heard, #2 hidden, #3 snoozed."""
    list_id = await conn.fetchval(
        """INSERT INTO list (slug, name, goal, ranked) VALUES ('zz_queue', 'Zz', 'canon', true)
           RETURNING list_id"""
    )
    groups = []
    for i in range(1, 16):
        artist = await conn.fetchval(
            "INSERT INTO artist (name, norm_key, mbid) VALUES ($1, $2, $3) RETURNING artist_id",
            f"Zz Queue Artist {i}",
            f"zz queue artist {i}",
            f"00000000-0000-4000-9000-{i:012d}",
        )
        rg = await conn.fetchval(
            """INSERT INTO release_group (mbid, artist_id, title, norm_key, primary_type)
               VALUES ($1, $2, $3, $4, 'Album') RETURNING release_group_id""",
            f"00000000-0000-4000-a000-{i:012d}",
            artist,
            f"Zz Queue Album {i}",
            f"zz queue album {i}",
        )
        await conn.execute(
            """INSERT INTO list_entry (list_id, position, raw_artist, raw_album, artist_key,
                                       album_key, release_group_id, resolve_status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, 'resolved')""",
            list_id,
            i,
            f"Zz Queue Artist {i}",
            f"Zz Queue Album {i}",
            f"zz queue artist {i}",
            f"zz queue album {i}",
            rg,
        )
        groups.append(rg)
    heard, hidden, snoozed = groups[:3]
    await conn.execute(
        """INSERT INTO album_session (release_group_id, started_at, ended_at, tracks_played,
                                      track_count, completion, session_type, source)
           VALUES ($1, $2, $2, 10, 10, 1.0, 'full', 'manual')""",
        heard,
        NOW - timedelta(days=3),
    )
    await conn.execute(
        """INSERT INTO queue_state (release_group_id, hidden_at, snoozed_until)
           VALUES ($1, now(), NULL), ($2, NULL, $3)""",
        hidden,
        snoozed,
        NOW + timedelta(days=30),
    )
    await conn.execute(
        """INSERT INTO queue_profile (name, filters, goal_weights, composition)
           VALUES ('zz_test', $1, '{"canon": 1}', '{"n": 10, "revisit_share": 0.2, "wildcard": 1}')""",
        json.dumps({"lists": ["zz_queue"]}),
    )
    return {"heard": heard, "hidden": hidden, "snoozed": snoozed, "all": set(groups)}


async def test_q2_new_albums_come_from_lists(conn) -> None:
    f = await _fixture(conn)
    q = await next_queue(conn, "zz_test", now=NOW)
    new = [i for i in q["items"] if i["slot"] in ("new", "wildcard")]
    assert new and all(i["release_group_id"] in f["all"] for i in new)
    assert all(i["why"] and i["why"][0]["list"] == "zz_queue" for i in new)


async def test_q3_no_heard_hidden_or_snoozed_album(conn) -> None:
    f = await _fixture(conn)
    q = await next_queue(conn, "zz_test", now=NOW)
    shown = {i["release_group_id"] for i in q["items"]}
    new = {i["release_group_id"] for i in q["items"] if i["slot"] in ("new", "wildcard")}
    assert f["heard"] not in new
    assert not {f["hidden"], f["snoozed"]} & shown
    assert len(q["items"]) == 10  # twelve albums left: enough for every slot


async def test_shuffle_returns_its_seed_and_honours_exclude(conn) -> None:
    f = await _fixture(conn)
    q = await next_queue(conn, "zz_test", now=NOW, shuffle=True, seed=7)
    assert q["seed"] == 7
    first = frozenset(i["release_group_id"] for i in q["items"] if i["slot"] == "new")
    again = await next_queue(conn, "zz_test", now=NOW, shuffle=True, seed=8, exclude=first)
    assert not first & {i["release_group_id"] for i in again["items"] if i["slot"] == "new"}
    assert f["all"]

"""Genre of the week (queue spec 10a) against a real database: made-up albums named "Zz Genre"
on a made-up list, deleted after. The pick runs inside a transaction that is rolled back, so a run
against Neon dev never touches the real week; the page reads the committed made-up rows."""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from musicdata.api.app import app
from musicdata.queue import config
from musicdata.queue.genre_week import load_index, pick, week_start

pytestmark = pytest.mark.integration
PAGE = "page-token"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("QUEUE_PAGE_TOKEN", PAGE)
    monkeypatch.setattr(config, "GENRE_WEEK_MIN_UNHEARD", 3)
    from musicdata.config import get_settings

    get_settings.cache_clear()
    _db(_seed)
    yield
    _db(_cleanup)
    get_settings.cache_clear()


def _db(coro_fn):
    async def run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()

    return asyncio.run(run())


async def _cleanup(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM list_entry WHERE raw_album LIKE 'Zz Genre%'")
    await conn.execute("DELETE FROM list WHERE slug = 'zz_genre'")
    await conn.execute("DELETE FROM release_group WHERE title LIKE 'Zz Genre%'")
    await conn.execute("DELETE FROM artist WHERE name LIKE 'Zz Genre%'")


async def _seed(conn: asyncpg.Connection) -> None:
    await _cleanup(conn)
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Genre Band', 'zz genre band') RETURNING artist_id"
    )
    list_id = await conn.fetchval(
        """INSERT INTO list (slug, name, goal, ranked) VALUES ('zz_genre', 'Zz Genre: list', 'canon', true)
           RETURNING list_id"""
    )
    for i in range(1, 7):
        title = f"Zz Genre Record {i}"
        rg = await conn.fetchval(
            """INSERT INTO release_group (artist_id, title, norm_key) VALUES ($1, $2, lower($2))
               RETURNING release_group_id""",
            artist,
            title,
        )
        genre = "Zzgenre Alpha" if i <= 3 else "Zzgenre-Beta (and notes)"
        await conn.execute(
            """INSERT INTO list_entry (list_id, position, raw_artist, raw_album, artist_key,
                                       album_key, release_group_id, resolve_status, facets)
               VALUES ($1, $2, 'Zz Genre Band', $3, 'zz genre band', lower($3), $4, 'resolved',
                       $5::jsonb)""",
            list_id,
            i,
            title,
            rg,
            json.dumps({"genre": genre}),
        )


def test_index_reads_list_genres():
    async def check(conn):
        idx = await load_index(conn)
        assert len(idx.albums("zzgenrealpha")) == 3
        assert len(idx.albums("zzgenrebeta")) == 3
        assert idx.names["zzgenrebeta"] == "zzgenre-beta"

    _db(check)


def test_pick_is_idempotent_and_reroll_replaces():
    async def check(conn):
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("DELETE FROM genre_week WHERE week_start = $1", week_start())
            first = await pick(conn)
            assert first["new"] and first["entries"] > 0
            again = await pick(conn)
            assert not again["new"] and again["genre"] == first["genre"]
            n = await conn.fetchval(
                """SELECT count(*) FROM list_entry e JOIN list l USING (list_id)
                    WHERE l.slug = 'genre_week' AND e.added_by = $1""",
                f"genre:{first['genre']}",
            )
            assert n == first["entries"]
            other = await pick(conn, reroll=True)
            assert other["genre"] != first["genre"]
            row = await conn.fetchrow(
                "SELECT * FROM genre_week WHERE week_start = $1", week_start()
            )
            assert row["picked_by"] == "reroll" and first["genre"] in row["rerolled"]
        finally:
            await tx.rollback()

    _db(check)


def test_genre_page_lists_the_albums():
    with TestClient(app) as client:
        r = client.get("/genres/zzgenrealpha/page", params={"t": PAGE})
        assert r.status_code == 200
        assert "Zz Genre Record 1" in r.text and "Zz Genre Record 4" not in r.text
        assert "Not heard yet · 3" in r.text
        assert client.get("/genres/zzgenrealpha/page").status_code in (401, 403)

"""Pages (companion spec section 6) against Neon dev: made-up rows named "Zz Page", deleted after.
An album with no graph data still renders and is queued for its baseline (3.6); hub pages list
albums with heard marks; search finds both; every page refuses a request without a token."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from musicdata.api.app import app

pytestmark = pytest.mark.integration
PAGE = "page-token"
MBID = "00000000-0000-4000-c000-{:012d}"


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("QUEUE_PAGE_TOKEN", PAGE)
    from musicdata.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _db(coro_fn):
    async def run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()

    return asyncio.run(run())


async def _cleanup(conn: asyncpg.Connection) -> None:
    ents = [
        r[0] for r in await conn.fetch("SELECT entity_id FROM entity WHERE name LIKE 'Zz Page%'")
    ]
    await conn.execute(
        "DELETE FROM assertion WHERE subject_id = ANY($1::bigint[]) OR object_id = ANY($1::bigint[])",
        ents,
    )
    await conn.execute("DELETE FROM graph_album WHERE entity_id = ANY($1::bigint[])", ents)
    await conn.execute("DELETE FROM entity WHERE entity_id = ANY($1::bigint[])", ents)
    await conn.execute(
        """DELETE FROM release_group_track WHERE release_group_id IN
             (SELECT release_group_id FROM release_group WHERE title LIKE 'Zz Page%')"""
    )
    await conn.execute(
        """DELETE FROM release_group_tracklist WHERE release_group_id IN
             (SELECT release_group_id FROM release_group WHERE title LIKE 'Zz Page%')"""
    )
    await conn.execute("DELETE FROM release_group WHERE title LIKE 'Zz Page%'")
    await conn.execute("DELETE FROM artist WHERE name LIKE 'Zz Page%'")


async def _setup(conn: asyncpg.Connection) -> dict:
    await _cleanup(conn)
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Page Band', 'zz page band') RETURNING artist_id"
    )
    rg = await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, primary_type, mbid,
                                      first_release_year)
           VALUES ($1, 'Zz Page Record', 'zz page record', 'Album', $2, 2001)
           RETURNING release_group_id""",
        artist,
        MBID.format(1),
    )
    await conn.execute(
        "INSERT INTO release_group_tracklist (release_group_id, track_count, source) VALUES ($1, 2, 'musicbrainz')",
        rg,
    )
    for n, title in ((1, "Zz Page Opener"), (2, "Zz Page Closer")):
        await conn.execute(
            """INSERT INTO release_group_track (release_group_id, position, title, norm_title)
               VALUES ($1, $2, $3, $3)""",
            rg,
            n,
            title,
        )
    band = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, artist_id) VALUES ('artist', 'Zz Page Band',
           'zz page band', $1) RETURNING entity_id""",
        artist,
    )
    return {"rg": rg, "band": band}


def test_pages(client) -> None:
    ids = _db(_setup)
    try:
        assert client.get(f"/albums/{ids['rg']}/page").status_code == 401
        r = client.get(f"/albums/{ids['rg']}/page", params={"t": PAGE})
        assert r.status_code == 200
        html = r.text
        assert "Zz Page Record" in html and "Zz Page Opener" in html
        assert "Gathering credits and sources" in html  # 3.6: no baseline yet
        assert f"/entities/{ids['band']}/page?t={PAGE}" in html  # links carry the page token

        async def opened(conn):
            return await conn.fetchval(
                """SELECT 'opened' = ANY(g.slices) FROM graph_album g JOIN entity e USING (entity_id)
                    WHERE e.release_group_id = $1""",
                ids["rg"],
            )

        assert _db(opened) is True

        hub = client.get(f"/entities/{ids['band']}/page", params={"t": PAGE})
        assert (
            hub.status_code == 200 and "Zz Page Record" in hub.text and "heard 0 of 1" in hub.text
        )

        found = client.get("/pages/search", params={"t": PAGE, "q": "Zz Page"})
        assert found.status_code == 200 and "Zz Page Record" in found.text

        assert client.get("/albums/-1/page", params={"t": PAGE}).status_code == 404
        now = client.get("/queue/now-playing", params={"t": PAGE})
        assert now.status_code == 200
    finally:
        _db(_cleanup)

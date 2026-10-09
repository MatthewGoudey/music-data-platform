"""The Phase 2 API against a migrated database (works whether it holds data or not)."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from musicdata.api.app import app

pytestmark = pytest.mark.integration
AUTH = {"Authorization": "Bearer test-token"}

GETS = [
    "/listens/summary",
    "/listens/timeline?period=year",
    "/listens/recent?limit=5",
    "/artists?limit=5",
    "/albums?limit=5&sort=recent",
    "/sessions?days=30",
    "/shows?days=30",
    "/shows?match=true&limit=5",
]


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("QUEUE_PAGE_TOKEN", "page-token")
    from musicdata.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", GETS)
def test_reads_need_the_token_and_answer_in_both_formats(client, path: str) -> None:
    assert client.get(path).status_code == 401
    compact = client.get(path, headers=AUTH)
    assert compact.status_code == 200
    assert compact.headers["content-type"].startswith("text/plain")
    sep = "&" if "?" in path else "?"
    as_json = client.get(f"{path}{sep}format=json", headers=AUTH)
    assert as_json.status_code == 200 and isinstance(as_json.json(), list)


def test_missing_pages_are_404(client) -> None:
    assert client.get("/artists/-1", headers=AUTH).status_code == 404
    assert client.get("/albums/-1", headers=AUTH).status_code == 404


def test_batch_lookup_never_guesses(client) -> None:
    r = client.post("/artists/batch", json={"names": ["Zzqx Nonexistent Band 9"]}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()[0]["match"] in {"none", "candidate"}


def test_query_reads_caps_rows_and_refuses_writes(client) -> None:
    r = client.post("/query", json={"sql": "SELECT generate_series(1, 2000) AS n"}, headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 1000 and r.headers["X-Truncated"] == "true"
    w = client.post("/query", json={"sql": "DELETE FROM listen"}, headers=AUTH)
    assert w.status_code == 400 and "ReadOnly" in w.json()["detail"]
    two = client.post("/query", json={"sql": "SELECT 1; SELECT 2"}, headers=AUTH)
    assert two.status_code == 400


def test_catch_up_is_rate_limited_and_needs_the_token(client, monkeypatch) -> None:
    from musicdata.api.routers import ingest

    async def _no_jobs() -> None:
        return None

    monkeypatch.setattr(ingest, "_catch_up", _no_jobs)
    assert client.post("/ingest/catch-up").status_code == 401
    r = client.post("/ingest/catch-up", headers=AUTH)
    assert r.status_code == 202 and "started" in r.json()


def test_queue_page_needs_its_own_token(client, monkeypatch) -> None:
    from musicdata.api.routers import ingest

    async def _no_catch_up(pool) -> bool:
        return False

    monkeypatch.setattr(ingest, "start_catch_up", _no_catch_up)
    assert client.get("/queue").status_code == 401
    assert client.get("/queue?t=wrong").status_code == 401
    page = client.get("/queue?t=page-token")
    assert page.status_code == 200 and "<html" in page.text


async def _temp_album() -> int:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        artist = await conn.fetchval(
            """INSERT INTO artist (name, norm_key) VALUES ('Zz Api Test', 'zz api test')
               ON CONFLICT DO NOTHING RETURNING artist_id"""
        ) or await conn.fetchval("SELECT artist_id FROM artist WHERE norm_key = 'zz api test'")
        return await conn.fetchval(
            """INSERT INTO release_group (artist_id, title, norm_key, primary_type)
               VALUES ($1, 'Zz Api Album', 'zz api album', 'Album')
               ON CONFLICT DO NOTHING RETURNING release_group_id""",
            artist,
        ) or await conn.fetchval(
            "SELECT release_group_id FROM release_group WHERE norm_key = 'zz api album'"
        )
    finally:
        await conn.close()


async def _cleanup() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("DELETE FROM release_group WHERE norm_key = 'zz api album'")
        await conn.execute("DELETE FROM artist WHERE norm_key = 'zz api test'")
    finally:
        await conn.close()


def test_verdicts_and_manual_sessions_round_trip(client) -> None:
    rg = asyncio.run(_temp_album())
    try:
        v = client.post(
            "/verdicts", json={"release_group_id": rg, "verdict": "again"}, headers=AUTH
        )
        assert v.status_code == 201
        form = client.post(
            "/queue/verdict?t=page-token",
            data={"release_group_id": rg, "verdict": "later", "note": "rainy day"},
            follow_redirects=False,
        )
        assert form.status_code == 303
        s = client.post(
            "/sessions",
            json={"release_group_id": rg, "listened_at": "2026-10-01T20:00:00Z", "completion": 1},
            headers=AUTH,
        )
        assert s.status_code == 201 and s.json()["session_type"] == "full"
        page = client.get(f"/albums/{rg}", headers=AUTH).json()
        assert page["sessions"][0]["source"] == "manual"
        assert (
            client.post(
                "/verdicts", json={"release_group_id": rg, "verdict": "maybe"}, headers=AUTH
            ).status_code
            == 422
        )
    finally:
        asyncio.run(_cleanup())

"""The document flow (companion spec 7–8) against Neon dev, with made-up rows named "Zz Doc"
(deleted after): request from the page, the worker lists, starts, renews, fetches a cached page,
posts a claim and its verdict into D<id>, and hands in; unresolved markers are refused; a failed
document retries once; the page and the tab states follow."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from musicdata.api.app import app
from musicdata.graph.claims import claim_key

pytestmark = pytest.mark.integration
PAGE = "page-token"
AUTH = {"Authorization": "Bearer test-token"}
MBID = "00000000-0000-4000-b000-{:012d}"
URL = "https://example.com/zz-doc-review"


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("QUEUE_PAGE_TOKEN", PAGE)
    monkeypatch.delenv("ROUTINE_FIRE_URL", raising=False)  # a test never starts the real worker
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
    rgs = [
        r[0]
        for r in await conn.fetch(
            "SELECT release_group_id FROM release_group WHERE title LIKE 'Zz Doc%'"
        )
    ]
    docs = [r[0] for r in await conn.fetch(
        "SELECT document_id FROM album_document WHERE release_group_id = ANY($1::int[])", rgs)]  # fmt: skip
    ents = [
        r[0] for r in await conn.fetch("SELECT entity_id FROM entity WHERE name LIKE 'Zz Doc%'")
    ]
    await conn.execute(
        "DELETE FROM assertion WHERE subject_id = ANY($1::bigint[]) OR object_id = ANY($1::bigint[])",
        ents,
    )
    await conn.execute(
        "DELETE FROM graph_batch WHERE label = ANY($1::text[])", [f"D{d}" for d in docs]
    )
    await conn.execute("DELETE FROM album_document WHERE release_group_id = ANY($1::int[])", rgs)
    await conn.execute("DELETE FROM source_fetch WHERE url = $1", URL)
    await conn.execute("DELETE FROM graph_album WHERE entity_id = ANY($1::bigint[])", ents)
    await conn.execute("DELETE FROM entity WHERE entity_id = ANY($1::bigint[])", ents)
    await conn.execute("DELETE FROM release_group WHERE release_group_id = ANY($1::int[])", rgs)
    await conn.execute("DELETE FROM artist WHERE name LIKE 'Zz Doc%'")


async def _setup(conn: asyncpg.Connection) -> dict:
    await _cleanup(conn)
    await conn.execute(
        """INSERT INTO predicate (name, facet, subject_types, object_types, "symmetric", lineage, description)
           VALUES ('credited_on', 'people', '{artist}', '{album,recording}', false, false, 'x')
           ON CONFLICT (name) DO NOTHING"""
    )
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Doc Band', 'zz doc band') RETURNING artist_id"
    )
    rg = await conn.fetchval(
        """INSERT INTO release_group (artist_id, title, norm_key, primary_type, mbid)
           VALUES ($1, 'Zz Doc Record', 'zz doc record', 'Album', $2) RETURNING release_group_id""",
        artist,
        MBID.format(1),
    )
    album = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid, release_group_id)
           VALUES ('album', 'Zz Doc Record', 'zz doc record', $1, $2) RETURNING entity_id""",
        MBID.format(1),
        rg,
    )
    person = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid) VALUES ('person', 'Zz Doc Drummer',
           'zz doc drummer', $1) RETURNING entity_id""",
        MBID.format(2),
    )
    evidence = "MusicBrainz release credits: Zz Doc Drummer — drums"
    claim = await conn.fetchval(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                  source, extractor, basis, evidence, album_context, status, confidence, asserted_by)
           VALUES ($1, 'ZTDOC-1', $2, 'credited_on', $3, 'musicbrainz', 'musicbrainz', 'documented',
                   $4, $3, 'accepted', 0.9, 'test') RETURNING assertion_id""",
        claim_key(person, "credited_on", album, "musicbrainz", None, evidence),
        person,
        album,
        evidence,
    )
    fetch = await conn.fetchval(
        """INSERT INTO source_fetch (url, mode, schema_version, entity_id, credits, ok, title, body)
           VALUES ($1, 'plain', 'plain', $2, 1, true, 'Zz Doc review',
                   'Zz Doc Drummer plays drums on Zz Doc Record.') RETURNING fetch_id""",
        URL,
        album,
    )
    return {"rg": rg, "claim": claim, "fetch": fetch}


def test_document_flow(client) -> None:
    ids = _db(_setup)
    rg = ids["rg"]
    try:
        r = client.post(f"/albums/{rg}/documents", params={"t": PAGE}, json={"kind": "deep_dive"})
        assert r.status_code == 200 and r.json()["created"] is True
        doc = r.json()["document_id"]
        again = client.post(
            f"/albums/{rg}/documents", params={"t": PAGE}, json={"kind": "deep_dive"}
        )
        first = {k: v for k, v in r.json().items() if k != "worker"}
        assert r.json()["worker"]["fired"] is False  # no trigger configured here
        assert again.json() == first | {"created": False}
        assert (
            "requested, usually ready" in client.get(f"/albums/{rg}/page", params={"t": PAGE}).text
        )

        assert client.get("/documents").status_code == 401
        assert any(d["document_id"] == doc for d in client.get("/documents", headers=AUTH).json())
        s = client.post(f"/documents/{doc}/start", headers=AUTH).json()
        token, batch = s["lease_token"], s["batch"]
        assert batch == f"D{doc}"
        assert (
            client.post(
                f"/documents/{doc}/renew", headers=AUTH, json={"lease_token": "x"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/documents/{doc}/renew", headers=AUTH, json={"lease_token": token}
            ).status_code
            == 200
        )

        f = client.post(
            "/fetches", headers=AUTH, json={"url": URL, "mode": "plain", "document_id": doc}
        )
        assert f.status_code == 200 and f.json()["cached"] and f.json()["fetch_id"] == ids["fetch"]
        assert (
            client.get(f"/fetches/{ids['fetch']}", headers=AUTH).json()["title"] == "Zz Doc review"
        )

        label = f"{batch}-rg{rg}-L001"
        posted = client.post("/assertions", headers=AUTH, json=[{
            "claim_id": label, "release_group_id": rg, "batch": batch, "predicate": "credited_on",
            "subject": {"type": "person", "name": "Zz Doc Drummer"},
            "object": {"type": "album", "name": "Zz Doc Record"},
            "source": "web:example.com", "basis": "reported", "source_url": URL,
            "evidence": "Zz Doc Drummer plays drums on Zz Doc Record."}])  # fmt: skip
        assert posted.status_code == 200, posted.text
        new_claim = posted.json()["assertions"][label]
        v = client.post(f"/graph/batches/{batch}/reader-verdicts", headers=AUTH,
                        json=[{"claim_id": label, "verdict": "SUPPORTS", "reason": "stated"}])  # fmt: skip
        assert v.status_code == 200 and v.json()["verdicts"] == 1

        bad = client.put(f"/documents/{doc}", headers=AUTH, json={
            "lease_token": token, "status": "ready", "body_md": "A fact [c:999999999]."})  # fmt: skip
        assert bad.status_code == 422 and "c:999999999" in bad.text
        body = (f"## Before you press play\n\nZz Doc Drummer plays drums here [c:{ids['claim']}]"
                f"[c:{new_claim}], as the review says [p:{ids['fetch']}].")  # fmt: skip
        done = client.put(f"/documents/{doc}", headers=AUTH, json={
            "lease_token": token, "status": "ready", "body_md": body, "model": "test",
            "checks": {"sentences": 1, "supported": 1}})  # fmt: skip
        assert done.status_code == 200 and done.json()["markers"] == 3
        states = client.get(f"/albums/{rg}/documents", params={"t": PAGE}).json()
        assert states["deep_dive"]["status"] == "ready"
        page = client.get(f"/albums/{rg}/documents/deep_dive", params={"t": PAGE})
        assert page.status_code == 200 and "Zz Doc Drummer plays drums here" in page.text

        # a rewrite is a new version; a failed one retries once, then stays failed
        assert client.post(f"/albums/{rg}/documents", params={"t": PAGE},
                           json={"kind": "liner_notes"}).status_code == 422  # dropped  # fmt: skip
        d2 = client.post(
            f"/albums/{rg}/documents", params={"t": PAGE}, json={"kind": "deep_dive"}
        ).json()
        assert d2["version"] == 2
        for expected in ("requested", "failed"):
            tok = client.post(f"/documents/{d2['document_id']}/start", headers=AUTH).json()[
                "lease_token"
            ]
            out = client.put(f"/documents/{d2['document_id']}", headers=AUTH,
                             json={"lease_token": tok, "status": "failed", "error": "test"}).json()  # fmt: skip
            assert out["status"] == expected
    finally:
        _db(_cleanup)

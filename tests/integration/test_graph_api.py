"""The graph API (companion spec Block A) against Neon dev. The API uses its own pool, so the
test writes made-up rows (names starting "Zz Api", labels "ZTAPI-") and deletes them after."""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from musicdata.api.app import app
from musicdata.graph.claims import claim_key

pytestmark = pytest.mark.integration
AUTH = {"Authorization": "Bearer test-token"}
MBID = "00000000-0000-4000-d000-{:012d}"

GETS = [
    "/entities?q=Crazy%20Horse",
    "/entities/1",
    "/entities/1/neighbors",
    "/albums/1/graph",
    "/albums/1/brief",
    "/assertions?ids=1",
    "/graph/questions",
    "/graph/coverage?slice=crazy_horse",
]


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
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


async def _predicates(conn) -> None:
    """The predicate rows the claims need (CI's database starts empty)."""
    from musicdata.graph.seed import read_predicates

    await conn.executemany(
        """INSERT INTO predicate (name, facet, subject_types, object_types, "symmetric", lineage,
                                  description)
           VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (name) DO NOTHING""",
        [tuple(p.values()) for p in read_predicates()],
    )


async def _cleanup(conn: asyncpg.Connection) -> None:
    ids = [r[0] for r in await conn.fetch("SELECT entity_id FROM entity WHERE name LIKE 'Zz Api%'")]
    await conn.execute("DELETE FROM assertion WHERE claim_label LIKE 'ZTAPI-%'")
    await conn.execute(
        "DELETE FROM assertion WHERE subject_id = ANY($1::bigint[]) OR object_id = ANY($1::bigint[])",
        ids,
    )
    await conn.execute("DELETE FROM entity WHERE entity_id = ANY($1::bigint[])", ids)


async def _setup(conn: asyncpg.Connection) -> dict:
    await _cleanup(conn)
    await _predicates(conn)
    person = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid) VALUES ('person', 'Zz Api Drummer',
           'zz api drummer', $1) RETURNING entity_id""",
        MBID.format(1),
    )
    album = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid) VALUES ('album', 'Zz Api Record',
           'zz api record', $1) RETURNING entity_id""",
        MBID.format(2),
    )
    evidence = "MusicBrainz release credits: Zz Api Drummer — drums"
    credit = await conn.fetchval(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                  qualifiers, source, extractor, basis, evidence, album_context, status,
                  confidence, asserted_by)
           VALUES ($1, 'ZTAPI-1', $2, 'credited_on', $3, '{"role": ["drums"], "tracks": "album"}',
                   'musicbrainz', 'musicbrainz', 'documented', $4, $3, 'accepted', 0.9, 'test')
           RETURNING assertion_id""",
        claim_key(person, "credited_on", album, "musicbrainz", None, evidence),
        person,
        album,
        evidence,
    )
    q_evidence = "Zz Api Drummer played on every song."
    question = await conn.fetchval(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                  source, extractor, basis, evidence, source_url, album_context, status,
                  reader_verdict, reader_reason, asserted_by)
           VALUES ($1, 'ZTAPI-2', $2, 'credited_on', $3, 'web:example.com', 'claude', 'reported',
                   $4, 'https://example.com/r', $3, 'ask_matt', 'PARTIAL', 'only implied', 'test')
           RETURNING assertion_id""",
        claim_key(
            person, "credited_on", album, "web:example.com", "https://example.com/r", q_evidence
        ),
        person,
        album,
        q_evidence,
    )
    return {"person": person, "album": album, "credit": credit, "question": question}


@pytest.mark.parametrize("path", GETS)
def test_graph_reads_need_the_token(client, path: str) -> None:
    assert client.get(path).status_code == 401
    assert client.get(path, headers=AUTH).status_code in (200, 404)


def test_graph_api_end_to_end(client) -> None:
    ids = _db(_setup)
    try:
        found = client.get("/entities?q=Zz%20Api%20Drummer&format=json", headers=AUTH).json()
        assert any(r["entity_id"] == ids["person"] for r in found)

        detail = client.get(f"/entities/{ids['person']}", headers=AUTH).json()
        (edge,) = detail["edges"]["people"]
        assert edge["predicate"] == "credited_on" and edge["direction"] == "out"
        assert edge["other"]["entity_id"] == ids["album"] and edge["assertion_id"] == ids["credit"]

        def neighbors(qs: str) -> list:
            url = f"/entities/{ids['person']}/neighbors?format=json&{qs}"
            return client.get(url, headers=AUTH).json()

        assert [r["entity_id"] for r in neighbors("direction=out")] == [ids["album"]]
        assert neighbors("direction=in") == []
        assert neighbors("min_confidence=0.95") == []

        claims = client.get(f"/assertions?ids={ids['credit']}", headers=AUTH).json()
        assert claims[0]["claim_label"] == "ZTAPI-1"

        qs = client.get("/graph/questions", headers=AUTH).json()
        (q,) = (x for x in qs if x["assertion_id"] == ids["question"])
        assert q["claim"] == "Zz Api Drummer worked or played on Zz Api Record"
        assert q["quote"] == "Zz Api Drummer played on every song."
        r = client.post(f"/graph/questions/{ids['question']}/answer", headers=AUTH,
                        json={"answer": "yes"})  # fmt: skip
        assert r.status_code == 200 and r.json()["matt_claim"]
        again = client.post(f"/graph/questions/{ids['question']}/answer", headers=AUTH,
                            json={"answer": "no"})  # fmt: skip
        assert again.status_code == 404

        base = {"predicate": "sounds_like", "basis": "inferred", "direction": "subject_newer",
                "subject": {"type": "album", "name": "Zz Api Record", "mbid": MBID.format(2)},
                "object": {"type": "artist", "name": "Zz Api Elder Band"},
                "source": "wikipedia", "evidence": "It recalls Zz Api Elder Band.",
                "source_url": "https://example.com/review"}  # fmt: skip
        atlas = client.post("/assertions", headers=AUTH,
                            json=[base | {"claim_id": "ZTAPI-3", "source": "map:v_atlas"}])  # fmt: skip
        assert atlas.status_code == 422 and "atlas" in atlas.json()["detail"]
        german = client.post("/assertions", headers=AUTH,
                             json=[base | {"claim_id": "ZTAPI-4", "source_url": "https://www.laut.de/x"}])  # fmt: skip
        assert german.status_code == 422
        ok = client.post("/assertions", headers=AUTH, json=[base | {"claim_id": "ZTAPI-5"}])
        assert ok.status_code == 200
        new_id = ok.json()["assertions"]["ZTAPI-5"]

        async def check(conn):
            return await conn.fetchrow(
                """SELECT a.status, a.source, o.resolve_status, o.name FROM assertion a
                     JOIN entity o ON o.entity_id = a.object_id WHERE a.assertion_id = $1""",
                new_id,
            )

        row = _db(check)
        assert (row["status"], row["source"]) == ("proposed", "web:example.com")
        assert (row["resolve_status"], row["name"]) == ("unresolved", "Zz Api Elder Band")
        same = client.post("/assertions", headers=AUTH, json=[base | {"claim_id": "ZTAPI-5"}])
        assert same.json()["assertions"]["ZTAPI-5"] == new_id

        def link(url: str) -> int:
            body = {"entity_id": ids["album"], "kind": "review", "url": url, "source": "test"}
            return client.post("/links", headers=AUTH, json=body).status_code

        assert link("https://www.plattentests.de/rezi.php?show=1") == 422
        assert link("https://rateyourmusic.com/release/album/x/") == 422
        assert link("https://example.com/zz-api-review") == 200

        async def retire(conn):
            from musicdata.graph.queries import set_link_status

            n = await set_link_status(conn, "https://example.com/zz-api-review", "dead")
            return n, await conn.fetchval(
                "SELECT status FROM entity_link WHERE url = 'https://example.com/zz-api-review'"
            )

        assert _db(retire) == (1, "dead")

        assert client.get("/albums/-1/brief", headers=AUTH).status_code == 404
    finally:
        _db(_cleanup)


def test_a_brief_renders_for_an_album_with_no_graph_data(client) -> None:
    async def pick(conn):
        return await conn.fetchval(
            """SELECT rg.release_group_id FROM release_group rg
                 JOIN release_group_tracklist t USING (release_group_id)
                WHERE t.track_count > 0
                  AND NOT EXISTS (SELECT 1 FROM entity e WHERE e.release_group_id = rg.release_group_id)
                LIMIT 1"""
        )

    rg = _db(pick)
    if rg is None:
        pytest.skip("every album with a tracklist has graph data")
    brief = client.get(f"/albums/{rg}/brief", headers=AUTH).json()
    assert (
        brief["identity"]["release_group_id"] == rg and brief["identity"]["album_entity_id"] is None
    )
    assert brief["tracks"] and brief["edges"] == {} and brief["pages"] == []
    assert brief["gaps"] == ["credits", "recording", "label", "people", "lineage"]
    assert brief["research_questions"]

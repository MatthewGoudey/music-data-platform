"""The graph schema (migrations 0018-0019) and `graph seed` against a real database. Rolled
back after each test; made-up MBIDs."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.graph.claims import claim_key
from musicdata.graph.seed import seed

pytestmark = pytest.mark.integration

MBID = "00000000-0000-4000-c000-{:012d}"


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


async def _entity(conn, type_: str, name: str, mbid: str | None = None, **kw) -> int:
    return await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid, local_key, context_key)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING entity_id""",
        type_,
        name,
        name.lower(),
        mbid,
        kw.get("local_key"),
        kw.get("context_key", ""),
    )


async def _claim(conn, s: int, o: int, *, source: str, extractor: str, basis: str, conf: float,
                 label: str, predicate: str = "credited_on") -> int:  # fmt: skip
    evidence = f"{source} {label}"
    return await conn.fetchval(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                                  source, extractor, basis, evidence, status, confidence,
                                  asserted_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'accepted', $10, 'test')
           RETURNING assertion_id""",
        claim_key(s, predicate, o, source, None, evidence),
        label,
        s,
        predicate,
        o,
        source,
        extractor,
        basis,
        evidence,
        conf,
    )


async def _fails(conn, sql: str, *args) -> bool:
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError):
        return True
    return False


async def test_one_entity_per_musicbrainz_artist_across_artist_and_person(conn) -> None:
    await seed(conn, None)  # predicates
    await _entity(conn, "artist", "Zz Graph Band", MBID.format(1))
    assert await _fails(
        conn,
        "INSERT INTO entity (type, name, norm_key, mbid) VALUES ('person', 'Zz', 'zz', $1)",
        MBID.format(1),
    )
    await _entity(conn, "album", "Zz Graph Album", MBID.format(2))
    assert await _fails(
        conn,
        "INSERT INTO entity (type, name, norm_key, mbid) VALUES ('album', 'Zz2', 'zz2', $1)",
        MBID.format(2),
    )


async def test_claims_need_one_object_a_known_extractor_and_a_unique_key(conn) -> None:
    await seed(conn, None)
    person = await _entity(conn, "person", "Zz Producer", MBID.format(3))
    album = await _entity(conn, "album", "Zz Record", MBID.format(4))
    base = """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                                     object_value, source, extractor, basis, evidence, asserted_by)
              VALUES ($1, $2, $3, 'credited_on', $4, $5, 'musicbrainz', $6, 'documented', 'e', 't')"""
    assert await _fails(conn, base, "k1", "L1", person, album, "both", "musicbrainz")
    assert await _fails(conn, base, "k2", "L2", person, None, None, "musicbrainz")
    assert await _fails(conn, base, "k3", "L3", person, album, None, "a_guess")
    await conn.execute(base, "k4", "L4", person, album, None, "musicbrainz")
    assert await _fails(conn, base, "k4", "L5", person, album, None, "musicbrainz")


async def test_the_edge_view_picks_by_precedence(conn) -> None:
    await seed(conn, None)
    person = await _entity(conn, "person", "Zz Engineer", MBID.format(5))
    album = await _entity(conn, "album", "Zz Session", MBID.format(6))
    text = await _claim(conn, person, album, source="web:zz.example", extractor="claude",
                        basis="reported", conf=0.8, label="ZZ-1")  # fmt: skip
    db = await _claim(conn, person, album, source="musicbrainz", extractor="musicbrainz",
                      basis="documented", conf=0.9, label="ZZ-2")  # fmt: skip
    edge = await conn.fetchrow(
        "SELECT * FROM edge WHERE subject_id = $1 AND object_id = $2", person, album
    )
    assert edge["best_assertion_id"] == db and float(edge["confidence"]) == 0.9
    assert sorted(edge["sources"]) == ["musicbrainz", "web:zz.example"]
    matt = await _claim(conn, person, album, source="matt", extractor="matt",
                        basis="documented", conf=1.0, label="ZZ-3")  # fmt: skip
    edge = await conn.fetchrow(
        "SELECT best_assertion_id FROM edge WHERE subject_id = $1 AND object_id = $2",
        person,
        album,
    )
    assert edge["best_assertion_id"] == matt and text != matt


async def test_the_atlas_ranks_below_a_page(conn) -> None:
    """Spec v4: the atlas is AI-written; a page's reported claim beats the atlas's, even when
    the atlas claim carries the higher confidence."""
    await seed(conn, None)
    a = await _entity(conn, "album", "Zz Newer", MBID.format(7))
    b = await _entity(conn, "album", "Zz Older", MBID.format(8))
    await _claim(conn, a, b, source="map:v_atlas", extractor="claude", basis="reported",
                 conf=0.9, label="ZZ-4", predicate="influenced_by")  # fmt: skip
    page = await _claim(conn, a, b, source="wikipedia", extractor="claude", basis="reported",
                        conf=0.7, label="ZZ-5", predicate="influenced_by")  # fmt: skip
    best = await conn.fetchval(
        "SELECT best_assertion_id FROM edge WHERE subject_id = $1 AND object_id = $2", a, b
    )
    assert best == page


async def test_seeding_twice_adds_nothing(conn) -> None:
    first = await seed(conn, None)
    second = await seed(conn, None)
    assert first["predicates"] == 17 and first["maps"] == 1
    assert second["lane_parent_added"] == 0
    assert {k: v for k, v in first.items() if k != "lane_parent_added"} == {
        k: v for k, v in second.items() if k != "lane_parent_added"
    }
    names = {r[0] for r in await conn.fetch("SELECT name FROM predicate WHERE lineage")}
    assert names == {"influenced_by", "sounds_like", "covers", "samples"}
    assert await conn.fetchval(
        """SELECT "symmetric" FROM predicate WHERE name = 'associated_with'"""
    )

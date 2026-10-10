"""A reading batch end to end against a real database: load-claims with name resolution,
reader verdicts, a corrected re-extraction, and verification writing statuses. Rolled back;
made-up MBIDs; MusicBrainz is a stub."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.graph.answers import answer
from musicdata.graph.batch import load_batch_claims
from musicdata.graph.claims import claim_key
from musicdata.graph.seed import seed
from musicdata.graph.verify import verify
from musicdata.graph.verify_job import load_claims, lookups, write_results

pytestmark = pytest.mark.integration

MBID = "00000000-0000-4000-c000-{:012d}"
URL = "https://en.wikipedia.org/wiki/Zz_Graph_Album"
PAGE = (
    "Zz Graph Album is the debut by Zz Graph Band. Critics compared the record to "
    '[Zz Old Band](https://en.wikipedia.org/wiki/Zz_Old_Band "Zz Old Band").[3] '
    "Zz Person played guitar on every song."
)


class StubMB:
    """Exact-name artist search over a fixed table; nothing else is called."""

    requests = 0
    ARTISTS = {
        "Zz Old Band": {"id": MBID.format(901), "name": "Zz Old Band", "type": "Group",
                        "life-span": {"begin": "1960"}},
        "Zz Future Band": {"id": MBID.format(902), "name": "Zz Future Band", "type": "Group",
                           "life-span": {"begin": "2090"}},
    }  # fmt: skip

    async def search_artists(self, name: str, *, limit: int = 5) -> list[dict]:
        return [self.ARTISTS[name]] if name in self.ARTISTS else []

    async def search_release_groups(self, *a, **k) -> list[dict]:
        return []


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


async def _setup(conn: asyncpg.Connection) -> tuple[int, int]:
    await seed(conn, None)
    album = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid, attrs)
           VALUES ('album', 'Zz Graph Album', 'zz graph album', $1, '{"year": 2000}')
           RETURNING entity_id""",
        MBID.format(900),
    )
    await conn.execute(
        """INSERT INTO map_membership (entity_id, map, coords)
           VALUES ($1, 'v_atlas', '{"atlas_id": "Z9999"}')""",
        album,
    )
    batch = await conn.fetchval(
        "INSERT INTO graph_batch (label, slice) VALUES ('ZT1', 'test') RETURNING batch_id"
    )
    await conn.execute(
        "INSERT INTO graph_album (entity_id, slices, batch_id) VALUES ($1, '{test}', $2)",
        album,
        batch,
    )
    await conn.execute(
        """INSERT INTO source_fetch (url, mode, schema_version, entity_id, credits, ok, body)
           VALUES ($1, 'plain', 'plain', $2, 1, true, $3)""",
        URL,
        album,
        PAGE,
    )
    person = await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid)
           VALUES ('person', 'Zz Person', 'zz person', $1) RETURNING entity_id""",
        MBID.format(903),
    )
    evidence = "MusicBrainz: Zz Person — guitar"
    await conn.execute(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                  source, extractor, basis, evidence, album_context, status, asserted_by)
           VALUES ($1, 'ZT1-BASE', $2, 'credited_on', $3, 'musicbrainz', 'musicbrainz',
                   'documented', $4, $3, 'proposed', 'test')""",
        claim_key(person, "credited_on", album, "musicbrainz", None, evidence),
        person,
        album,
        evidence,
    )
    return album, person


ALBUM = {"type": "album", "name": "Zz Graph Band – Zz Graph Album", "atlas_id": "Z9999"}


def _claim(label: str, predicate: str, subject: dict, obj: dict, basis: str, evidence: str,
           **kw) -> dict:  # fmt: skip
    return {"claim_id": label, "album": "Z9999", "predicate": predicate, "subject": subject,
            "object": obj, "qualifiers": {}, "source": "wikipedia", "basis": basis,
            "evidence": evidence, "source_url": URL, "direction": "subject_newer",
            "extractor": "claude", **kw}  # fmt: skip


CLAIMS = [
    _claim("ZT1-L001", "sounds_like", ALBUM, {"type": "artist", "name": "Zz Old Band"},
           "inferred", "Critics compared the record to Zz Old Band."),
    _claim("ZT1-L002", "credited_on", {"type": "artist", "name": "Zz Person"}, ALBUM,
           "reported", "Zz Person played guitar on every song."),
    _claim("ZT1-L003", "influenced_by", ALBUM, {"type": "artist", "name": "Zz Future Band"},
           "reported", "Critics compared the record to"),
    _claim("ZT1-L004", "sounds_like", ALBUM, {"type": "artist", "name": "Zz Old Band"},
           "inferred", "a sentence that is nowhere on the page"),
]  # fmt: skip


async def _verify(conn: asyncpg.Connection, batch_id: int) -> dict[str, tuple]:
    claims = await load_claims(conn, batch_id=batch_id)
    verify(claims, await lookups(conn, claims, StubMB(), None))
    await write_results(conn, claims)
    return {
        r["claim_label"]: (r["status"], float(r["confidence"]), list(r["fails"]))
        for r in await conn.fetch(
            "SELECT claim_label, status, confidence, fails FROM assertion WHERE album_context = $1",
            claims[0]["album_context"],
        )
    }


async def test_batch_load_resolve_read_and_verify(conn) -> None:
    album, person = await _setup(conn)
    counts, named = await load_batch_claims(conn, StubMB(), "ZT1", CLAIMS, None)
    assert counts["loaded"] == 4
    assert named["linked"] == 1 and named["resolved"] == 2  # Zz Person via the baseline
    again, _ = await load_batch_claims(conn, StubMB(), "ZT1", CLAIMS, None)
    assert again["already_loaded"] == 4 and not again.get("loaded")

    rows = {
        r["claim_label"]: r
        for r in await conn.fetch(
            """SELECT a.claim_label, a.subject_id, a.object_id, a.fetch_id, o.mbid::text AS mbid,
                      o.attrs::text AS attrs
                 FROM assertion a JOIN entity o ON o.entity_id = a.object_id
                WHERE a.batch_id = (SELECT batch_id FROM graph_batch WHERE label = 'ZT1')"""
        )
    }
    assert rows["ZT1-L001"]["subject_id"] == album  # by atlas id
    assert rows["ZT1-L001"]["mbid"] == MBID.format(901)
    assert json.loads(rows["ZT1-L001"]["attrs"])["year"] == 1960
    assert rows["ZT1-L002"]["subject_id"] == person
    assert rows["ZT1-L001"]["fetch_id"] is not None

    batch_id = await conn.fetchval("SELECT batch_id FROM graph_batch WHERE label = 'ZT1'")
    await conn.executemany(
        """UPDATE assertion SET reader_verdict = $2, reader_first = coalesce(reader_first, $2)
            WHERE claim_label = $1""",
        [("ZT1-L001", "SUPPORTS"), ("ZT1-L002", "PARTIAL"), ("ZT1-L003", "SUPPORTS")],
    )
    got = await _verify(conn, batch_id)
    assert got["ZT1-BASE"][0] == "accepted"
    assert got["ZT1-L001"][:2] == ("accepted", 0.5)
    assert got["ZT1-L002"][0] == "ask_matt"  # PARTIAL and no database cross-check
    assert got["ZT1-L003"][0] == "rejected" and got["ZT1-L003"][2][0].startswith("direction")
    assert got["ZT1-L004"][2] == ["evidence not found verbatim in the cached source"]

    fixed = _claim("ZT1-L005", "sounds_like", ALBUM, {"type": "artist", "name": "Zz Old Band"},
                   "inferred", "Critics compared … Zz Old Band", replaces="ZT1-L001")  # fmt: skip
    await load_batch_claims(conn, StubMB(), "ZT1", [fixed], None)
    old, new = await conn.fetch(
        """SELECT status, reader_first, replaces FROM assertion
            WHERE claim_label IN ('ZT1-L001', 'ZT1-L005') ORDER BY claim_label"""
    )
    assert old["status"] == "superseded" and new["reader_first"] == "SUPPORTS"
    got = await _verify(conn, batch_id)
    assert "ZT1-L001" not in got or got["ZT1-L001"][0] == "superseded"
    assert got["ZT1-L005"][0] == "unread"  # waits for the reader's verdict on the new quote

    # Matt answers the reading question: a matt claim replaces it, and verify leaves it alone
    new = await answer(conn, "ZT1-L002", "yes", {"region": "UK"})
    row = await conn.fetchrow(
        "SELECT status, extractor, confidence, qualifiers::text AS q FROM assertion WHERE assertion_id = $1",
        new,
    )
    assert (row["status"], row["extractor"], float(row["confidence"])) == ("accepted", "matt", 1.0)
    assert json.loads(row["q"])["region"] == "UK"
    got = await _verify(conn, batch_id)
    assert got["ZT1-L002"][0] == "superseded" and got["ZT1-L002-M"][0] == "accepted"
    with pytest.raises(ValueError):
        await answer(conn, "ZT1-L002", "no")  # no longer an open question


async def test_load_claims_refuses_the_atlas_as_a_source(conn) -> None:
    await _setup(conn)
    atlas = _claim("ZT1-L009", "sounds_like", ALBUM, {"type": "artist", "name": "Zz Old Band"},
                   "inferred", "builds on Zz Old Band", source="map:v_atlas")  # fmt: skip
    with pytest.raises(ValueError, match="not a source"):
        await load_batch_claims(conn, StubMB(), "ZT1", [atlas], None)


async def test_an_identical_correction_leaves_the_original_in_place(conn) -> None:
    await _setup(conn)
    await load_batch_claims(conn, StubMB(), "ZT1", CLAIMS[:1], None)
    same = {**CLAIMS[0], "claim_id": "ZT1-L101", "replaces": "ZT1-L001"}
    counts, _ = await load_batch_claims(conn, StubMB(), "ZT1", [same], None)
    assert counts["correction_identical"] == 1 and not counts.get("loaded")
    status = await conn.fetchval("SELECT status FROM assertion WHERE claim_label = 'ZT1-L001'")
    assert status != "superseded"

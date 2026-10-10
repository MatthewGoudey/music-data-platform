"""The duplicate-entity merge rule (graph spec v10) and its undo, against Neon dev, rolled back;
made-up MBIDs and names."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from musicdata.graph.claims import claim_key
from musicdata.graph.merge import candidates, merge, unmerge

pytestmark = pytest.mark.integration
MBID = "00000000-0000-4000-e000-{:012d}"


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


async def _entity(conn, type_: str, name: str, mbid: str | None = None) -> int:
    return await conn.fetchval(
        """INSERT INTO entity (type, name, norm_key, mbid, resolve_status)
           VALUES ($1, $2, $3, $4, $5) RETURNING entity_id""",
        type_,
        name,
        name.lower().replace(" ", " "),
        mbid,
        "resolved" if mbid else "unresolved",
    )


async def _credit(conn, person: int, album: int, source: str, label: str) -> int:
    evidence = f"{source} credits: {label}"
    return await conn.fetchval(
        """INSERT INTO assertion (claim_key, claim_label, subject_id, predicate, object_id,
                  source, extractor, basis, evidence, album_context, status, confidence,
                  asserted_by)
           VALUES ($1, $2, $3, 'credited_on', $4, $5, $5, 'documented', $6, $4, 'accepted', 0.9,
                   'test') RETURNING assertion_id""",
        claim_key(person, "credited_on", album, source, None, evidence),
        label,
        person,
        album,
        source,
        evidence,
    )


async def test_merge_only_with_a_shared_album_and_undo(conn) -> None:
    album = await _entity(conn, "album", "Zzm Record", MBID.format(1))
    other_album = await _entity(conn, "album", "Zzm Other Record", MBID.format(2))
    firm = await _entity(conn, "person", "zzm drummer", MBID.format(3))
    loose = await _entity(conn, "person", "zzm drummer")
    apart_firm = await _entity(conn, "person", "zzm bassist", MBID.format(4))
    apart_loose = await _entity(conn, "person", "zzm bassist")
    await _credit(conn, firm, album, "musicbrainz", "ZZM-1")
    moved = await _credit(conn, loose, album, "discogs", "ZZM-2")
    await conn.execute(
        "INSERT INTO entity_link (entity_id, kind, url, source) VALUES ($1, 'other', 'https://example.com/zzm', 't')",
        loose,
    )
    await _credit(conn, apart_firm, album, "musicbrainz", "ZZM-3")
    await _credit(conn, apart_loose, other_album, "discogs", "ZZM-4")  # no shared album

    merges, apart = await candidates(conn)
    mine = [m for m in merges if m["loose"] in (loose, apart_loose)]
    assert [(m["loose"], m["firm"]) for m in mine] == [(loose, firm)]
    left = [a for a in apart if a["loose"] == apart_loose]
    assert left and left[0]["reason"] == "shares no album with the entity that has an MBID"

    log = await merge(conn, loose, firm, None)
    assert log["subject_of"] == [moved]
    assert (
        await conn.fetchval("SELECT subject_id FROM assertion WHERE assertion_id = $1", moved)
        == firm
    )
    assert not await conn.fetchval("SELECT 1 FROM entity WHERE entity_id = $1", loose)
    assert await conn.fetchval(
        "SELECT 1 FROM entity_link WHERE entity_id = $1 AND url = 'https://example.com/zzm'", firm
    )
    logged = json.loads(
        await conn.fetchval("SELECT attrs::text FROM entity WHERE entity_id = $1", firm)
    )
    assert logged["merged"][0]["entity"]["entity_id"] == loose

    restored = await unmerge(conn, firm, loose)
    assert restored == 1
    assert (
        await conn.fetchval("SELECT subject_id FROM assertion WHERE assertion_id = $1", moved)
        == loose
    )
    assert (
        await conn.fetchval("SELECT name FROM entity WHERE entity_id = $1", loose) == "zzm drummer"
    )
    after = json.loads(
        await conn.fetchval("SELECT attrs::text FROM entity WHERE entity_id = $1", firm)
    )
    assert after["merged"] == []

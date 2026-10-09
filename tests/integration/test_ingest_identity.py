"""ADR 0015's identity rules through IdentityIndex.write_page. Rolled back after each test."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from musicdata.ingest.identity import IdentityIndex
from musicdata.ingest.parse import parse_listen

pytestmark = pytest.mark.integration

FIXTURES = json.loads(
    (Path(__file__).parents[1] / "unit" / "fixtures" / "listens.json").read_text("utf-8")
)
# Made-up MBIDs: real ones collide with listening data already loaded in dev.
NIRVANA_US = "00000000-0000-4000-8000-000000000011"
NIRVANA_UK = "00000000-0000-4000-8000-000000000012"


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


def _listen(
    ts: int,
    artist: str,
    track: str,
    *,
    artist_mbid: str | None = None,
    release: str | None = None,
    rg_mbid: str | None = None,
):
    meta: dict = {"artist_name": artist, "track_name": track}
    if release:
        meta["release_name"] = release
    if artist_mbid:
        meta["mbid_mapping"] = {
            "artists": [{"artist_credit_name": artist, "artist_mbid": artist_mbid}],
            "release_group_mbid": rg_mbid,
        }
    p = parse_listen({"listened_at": ts, "track_metadata": meta})
    assert p is not None
    return p


async def _artist_rows(conn, key: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT artist_id, mbid::text FROM artist WHERE norm_key = $1 ORDER BY artist_id", key
    )


async def test_a_page_writes_listens_and_is_idempotent(conn) -> None:
    raw = copy.deepcopy(FIXTURES["mapped"])
    raw["listened_at"] = 946684800  # 2000-01-01: clear of real history
    page = [parse_listen(raw)]
    index = await IdentityIndex.load(conn)
    first = await index.write_page(conn, page)
    again = await index.write_page(conn, page)
    assert first.listens_inserted == 1
    assert again.listens_inserted == 0
    row = await conn.fetchrow(
        """SELECT a.mbid::text AS artist_mbid, rg.mbid::text AS rg_mbid
             FROM listen l JOIN artist a USING (artist_id)
             JOIN release_group rg USING (release_group_id)
            WHERE l.listened_at = to_timestamp(946684800)"""
    )
    assert row["artist_mbid"] == "f6f2326f-6b25-4170-b89d-e235b25508e8"
    assert row["rg_mbid"] == "62b427b8-1c52-34e7-b250-da2aa3e44860"


async def test_an_unmapped_artist_is_promoted_when_its_mbid_arrives(conn) -> None:
    index = await IdentityIndex.load(conn)
    await index.write_page(conn, [_listen(946684801, "Zz Test Promote Band", "One")])
    await index.write_page(
        conn,
        [_listen(946684802, "Zz Test Promote Band", "Two", artist_mbid=NIRVANA_UK)],
    )
    rows = await _artist_rows(conn, "zz test promote band")
    assert [r["mbid"] for r in rows] == [NIRVANA_UK]


async def test_two_mapped_namesakes_stay_apart_and_unmapped_gets_its_own_row(conn) -> None:
    index = await IdentityIndex.load(conn)
    await index.write_page(
        conn,
        [
            _listen(946684803, "Zz Test Nirvana", "Lithium", artist_mbid=NIRVANA_US),
            _listen(946684804, "Zz Test Nirvana", "Rainbow Chaser", artist_mbid=NIRVANA_UK),
            _listen(946684805, "Zz Test Nirvana", "Unknown Song"),
        ],
    )
    rows = await _artist_rows(conn, "zz test nirvana")
    assert sorted(r["mbid"] or "" for r in rows) == ["", NIRVANA_US, NIRVANA_UK]


async def test_unmapped_spelling_lands_on_the_single_mapped_artist(conn) -> None:
    index = await IdentityIndex.load(conn)
    await index.write_page(
        conn,
        [
            _listen(946684806, "Zz Test Sigur Rós", "A", artist_mbid=NIRVANA_US),
            _listen(946684807, "Zz Test Sigur Ros", "B"),
        ],
    )
    rows = await _artist_rows(conn, "zz test sigur ros")
    assert len(rows) == 1
    n = await conn.fetchval(
        "SELECT count(*) FROM listen WHERE artist_id = $1", rows[0]["artist_id"]
    )
    assert n == 2


async def test_rekey_moves_a_listen_off_a_comma_joined_artist(conn) -> None:
    index = await IdentityIndex.load(conn)
    raw = {
        "listened_at": 946684810,
        "track_metadata": {
            "artist_name": "Zz Rekey Lead, Zz Rekey Guest",
            "release_name": "Zz Rekey Album",
            "track_name": "Zz Song",
            "additional_info": {"release_artist_name": "Zz Rekey Lead"},
        },
    }
    old_rules = copy.deepcopy(raw)  # without the album artist, the old rule kept the whole name
    del old_rules["track_metadata"]["additional_info"]
    await index.write_page(conn, [parse_listen(old_rules)])
    assert await _artist_rows(conn, "zz rekey lead zz rekey guest")

    result = await index.write_page(conn, [parse_listen(raw)], rekey=True)
    assert (result.listens_rekeyed, result.listens_inserted) == (1, 0)
    moved = await conn.fetchval(
        """SELECT a.norm_key FROM listen l JOIN artist a USING (artist_id)
            WHERE l.listened_at = to_timestamp(946684810)"""
    )
    assert moved == "zz rekey lead"


async def test_rekey_follows_a_title_key_change_and_collapses_its_duplicate(conn) -> None:
    index = await IdentityIndex.load(conn)
    raw = {
        "listened_at": 946684820,
        "track_metadata": {
            "artist_name": "Zz Swift",
            "track_name": "Zz Song (Zz's Version)",
            "release_name": "Zz Red (Zz's Version)",
        },
    }
    p = parse_listen(raw)
    await index.write_page(conn, [p])
    listen = "SELECT listen_id FROM listen WHERE listened_at = to_timestamp(946684820)"
    (stored,) = (r["listen_id"] for r in await conn.fetch(listen))
    # What an older title rule stored, plus the duplicate a key-blind rekey then inserted.
    await conn.execute("UPDATE listen SET norm_title = 'zz song' WHERE listen_id = $1", stored)
    await conn.execute(
        """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                               norm_title, artist_name)
           SELECT listened_at, artist_id, release_group_id, track_name, $2, artist_name
             FROM listen WHERE listen_id = $1""",
        stored,
        p.norm_title,
    )
    result = await index.write_page(conn, [p], rekey=True)
    rows = await conn.fetch(listen.replace("listen_id", "listen_id, norm_title"))
    assert [(r["listen_id"], r["norm_title"]) for r in rows] == [(stored, p.norm_title)]
    assert result.listens_inserted == 0


async def test_edition_variants_share_one_release_group(conn) -> None:
    index = await IdentityIndex.load(conn)
    await index.write_page(
        conn,
        [
            _listen(946684808, "Zz Test Clash", "Train in Vain", release="London Calling"),
            _listen(
                946684809, "Zz Test Clash", "Spanish Bombs", release="London Calling (Remastered)"
            ),
        ],
    )
    rgs = await conn.fetch(
        """SELECT DISTINCT release_group_id FROM listen
            WHERE listened_at IN (to_timestamp(946684808), to_timestamp(946684809))"""
    )
    assert len(rgs) == 1
    aliases = await conn.fetchval(
        "SELECT count(*) FROM release_group_alias WHERE release_group_id = $1",
        rgs[0]["release_group_id"],
    )
    assert aliases == 2

"""Storing shows and resolving their performers. Rolled back after each test."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from musicdata.shows.job import resolve_performer, upsert_show
from musicdata.shows.parse import Performer, Show, Venue

pytestmark = pytest.mark.integration
CDT = timezone(timedelta(hours=-5))


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


def _show(source_id: str, performers: list[str], title: str | None = None) -> Show:
    return Show(
        source="omr",
        source_id=source_id,
        url=f"https://chicago.ohmyrockness.com/shows/{source_id}",
        starts_at=datetime(2031, 5, 1, 20, 0, tzinfo=CDT),
        title=title or ", ".join(performers),
        venue=Venue(source="omr", slug="zz-test-hall", name="Zz Test Hall", latitude=41.9),
        performers=[Performer(name=p, slug=p.lower().replace(" ", "-")) for p in performers],
        structured=True,
    )


async def _artist(conn, name: str, key: str) -> int:
    artist_id = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ($1, $2) RETURNING artist_id", name, key
    )
    await conn.execute(
        "INSERT INTO artist_alias (raw_name, artist_id, source) VALUES ($1, $2, 'listenbrainz')",
        name,
        artist_id,
    )
    return artist_id


async def test_a_show_is_stored_once_with_its_lineup(conn) -> None:
    band = await _artist(conn, "Zz Show Band", "zz show band")
    first = await upsert_show(
        conn, _show("990000001", ["Zz Show Band", "Zz Unknown Opener (DJ set)"])
    )
    again = await upsert_show(
        conn, _show("990000001", ["Zz Show Band", "Zz Unknown Opener (DJ set)"])
    )
    assert first == again
    rows = await conn.fetch(
        """SELECT position, role, clean_name, note, artist_id FROM show_artist
            WHERE show_id = $1 ORDER BY position""",
        first,
    )
    assert [tuple(r) for r in rows] == [
        (1, "headliner", "Zz Show Band", None, band),
        (2, "support", "Zz Unknown Opener", "DJ set", None),
    ]
    assert await conn.fetchval("SELECT count(*) FROM show_source WHERE show_id = $1", first) == 1


async def test_two_listings_of_one_night_are_one_show(conn) -> None:
    a = await upsert_show(conn, _show("990000002", ["Zz Show Band"]))
    other = _show("TM-zz-1", ["Zz Show Band", "Zz Guest"])
    other.source = "ticketmaster"
    b = await upsert_show(conn, other)
    assert a == b
    assert await conn.fetchval("SELECT count(*) FROM show_source WHERE show_id = $1", a) == 2


async def test_an_ampersand_splits_only_when_both_halves_are_artists(conn) -> None:
    lead = await _artist(conn, "Zz Lead", "zz lead")
    guest = await _artist(conn, "Zz Guest", "zz guest")
    split = await upsert_show(conn, _show("990000003", ["Zz Lead & Zz Guest"]))
    kept = await upsert_show(conn, _show("990000004", ["Zz Lead & Zz Nobody"]))
    ids = lambda show: conn.fetch(  # noqa: E731
        "SELECT clean_name, artist_id FROM show_artist WHERE show_id = $1 ORDER BY position", show
    )
    assert [tuple(r) for r in await ids(split)] == [("Zz Lead", lead), ("Zz Guest", guest)]
    assert [tuple(r) for r in await ids(kept)] == [("Zz Lead & Zz Nobody", None)]


async def test_namesakes_resolve_to_the_one_you_listen_to(conn) -> None:
    await conn.execute(
        """INSERT INTO artist (name, norm_key, mbid) VALUES
           ('Zz Twin', 'zz twin', '00000000-0000-4000-8000-000000000901'),
           ('Zz Twin', 'zz twin', '00000000-0000-4000-8000-000000000902')"""
    )
    assert await resolve_performer(conn, "Zz Twin") is None  # neither listened to


async def test_a_venue_named_differently_at_the_same_place_is_one_venue(conn) -> None:
    # Coordinates far from Chicago: dev holds real venues, and location matching would find them.
    from musicdata.shows.job import upsert_venue

    omr = Venue(source="omr", slug="zz-shed", name="Zz Shed", latitude=10.0, longitude=10.0)
    tm = Venue(
        source="ticketmaster",
        slug="ZZTM1",
        name="Zz Shed Indoors (Shed)",
        latitude=10.0004,
        longitude=10.0004,
    )
    far = Venue(
        source="ticketmaster", slug="ZZTM2", name="Zz Elsewhere", latitude=10.05, longitude=10.05
    )
    a, b, c = [await upsert_venue(conn, v) for v in (omr, tm, far)]
    assert a == b != c
    assert (
        await conn.fetchval("SELECT ticketmaster_id FROM venue WHERE venue_id = $1", a) == "ZZTM1"
    )

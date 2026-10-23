"""Where played runs land: the scenarios in fixtures/listen_homes.json, one per failure family.

Every case is built in one rolled-back transaction (made-up MBIDs, one artist per case, a day
apart in 1998), derive's placement steps run once over them, and each case's listens and
sessions are checked. Add a case when a new way of landing on the wrong album turns up.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from musicdata.derive.job import _rebuild_chunk
from musicdata.derive.redirects import apply_redirects
from musicdata.derive.reported import go_home
from musicdata.derive.runs import leave_compilations, place_runs
from musicdata.identity import album_key, norm_key, title_key

pytestmark = pytest.mark.integration

CASES = json.loads((Path(__file__).parent / "fixtures" / "listen_homes.json").read_text("utf-8"))[
    "cases"
]
T0 = datetime(1998, 1, 1, 12, tzinfo=UTC)
NS = uuid.UUID("00000000-0000-4000-8000-00000000cafe")


def _split(spec: str) -> tuple[str, str]:
    title, _, rec = spec.partition("@")
    return title, rec or title


def _mbid(case: str, what: str) -> str:
    return str(uuid.uuid5(NS, f"{case}/{what}"))


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


async def _build(conn, i: int, case: dict) -> tuple[dict[str, int], dict[int, str]]:
    name = case["name"]
    artist_name = f"Zz Homes {i}"
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key, mbid) VALUES ($1, $2, $3) RETURNING artist_id",
        artist_name,
        norm_key(artist_name),
        _mbid(name, "artist"),
    )
    groups: dict[str, int] = {}
    for key, g in case["groups"].items():
        flags = g.get("flags", [])
        rg = await conn.fetchval(
            """INSERT INTO release_group (mbid, artist_id, title, norm_key, primary_type,
                                          is_compilation, is_box_set, first_release_year)
               VALUES ($1, $2, $3, $4, $5, $6, $7, 1970) RETURNING release_group_id""",
            _mbid(name, f"group/{key}"),
            artist,
            g["title"],
            album_key(g["title"]),
            g.get("type", "Album"),
            "compilation" in flags,
            "box_set" in flags,
        )
        groups[key] = rg
        await conn.execute(
            """INSERT INTO release_group_tracklist (release_group_id, track_count, source)
               VALUES ($1, $2, 'musicbrainz')""",
            rg,
            len(g["tracks"]),
        )
        for pos, spec in enumerate(g["tracks"], 1):
            title, rec = _split(spec)
            await conn.execute(
                """INSERT INTO release_group_track (release_group_id, position, title, norm_title,
                                                    recording_mbid)
                   VALUES ($1, $2, $3, $4, $5)""",
                rg,
                pos,
                title,
                title_key(title),
                _mbid(name, f"rec/{rec}"),
            )
    plays: dict[int, str] = {}
    start = T0 + timedelta(days=i)
    for n, spec in enumerate(case["plays"]):
        title, rec = _split(spec)
        on = case["on"][n % len(case["on"])]
        lid = await conn.fetchval(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name,
                                   norm_title, artist_name, release_name, recording_mbid,
                                   reported_key)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING listen_id""",
            start + timedelta(minutes=4 * n),
            artist,
            groups[on],
            title,
            title_key(title),
            artist_name,
            case["reported"],
            _mbid(name, f"rec/{rec}"),
            album_key(case["reported"]),  # as ingest stores it
        )
        plays[lid] = title
    return groups, plays


@pytest.mark.parametrize("mode", ["nightly", "catch_up"])
async def test_played_runs_land_on_the_album_played(conn, mode: str) -> None:
    before = await conn.fetchval("SELECT now()")  # the cases' loaded_at, inside this transaction
    built = [await _build(conn, i, case) for i, case in enumerate(CASES)]
    await go_home(conn)
    await place_runs(conn, loaded_since=before if mode == "catch_up" else None)
    await leave_compilations(conn)
    await apply_redirects(conn)
    all_groups = [rg for groups, _ in built for rg in groups.values()]
    await _rebuild_chunk(conn, all_groups)

    failures = []
    for case, (groups, plays) in zip(CASES, built, strict=True):
        names = {rg: key for key, rg in groups.items()}
        held = {
            r["listen_id"]: names.get(r["release_group_id"], r["release_group_id"])
            for r in await conn.fetch(
                "SELECT listen_id, release_group_id FROM listen WHERE listen_id = ANY($1)",
                list(plays),
            )
        }
        for lid, title in plays.items():
            want = case["expect"].get(title, case["expect"].get("*"))
            if want and held[lid] != want:
                failures.append(f"{case['name']}: {title!r} on {held[lid]}, want {want}")
        for key, kind in case.get("sessions", {}).items():
            best = await conn.fetchrow(
                """SELECT session_type, tracks_played, track_count FROM album_session
                    WHERE release_group_id = $1 ORDER BY completion DESC LIMIT 1""",
                groups[key],
            )
            ok = best is not None and (
                best["tracks_played"] == best["track_count"]
                if kind == "complete"
                else best["session_type"] == kind
            )
            if not ok:
                failures.append(f"{case['name']}: {key} session {dict(best or {})}, want {kind}")
    assert not failures, "\n".join(failures)


async def test_catch_up_leaves_marks_before_its_span(conn) -> None:
    """The catch-up derive decides only the runs around new listens; a run mark from before
    them stays for the nightly derive, which decides every run."""
    artist = await conn.fetchval(
        "INSERT INTO artist (name, norm_key) VALUES ('Zz Homes old', 'zz homes old') RETURNING artist_id"
    )
    old = await conn.fetchval(
        """INSERT INTO listen (listened_at, artist_id, track_name, norm_title, artist_name,
                               release_name, reported_key, placed_by, loaded_at)
           VALUES ('1990-01-01', $1, 'Zz', 'zz', 'Zz Homes old', 'Zz', 'zz', 'run', '1990-01-01')
           RETURNING listen_id""",
        artist,
    )
    before = await conn.fetchval("SELECT now()")
    await _build(conn, 0, CASES[0])
    assert (await place_runs(conn, loaded_since=before))["runs"] > 0
    assert await conn.fetchval("SELECT placed_by FROM listen WHERE listen_id = $1", old) == "run"
    await conn.execute("DROP TABLE run_place")  # ON COMMIT DROP waits for the outer rollback
    await place_runs(conn)
    assert await conn.fetchval("SELECT placed_by FROM listen WHERE listen_id = $1", old) is None
